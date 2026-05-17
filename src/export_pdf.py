# -*- coding: utf-8 -*-
"""PDF/PDF-X export helpers."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import deckmaker_paths as DMPATHS
import export as EXPORT
import gs as GS
import icc_profiles as ICC
import inkscape_cli as INKSCAPE
import log as LOG
import prefs
import svg_chunks as SVGCHUNKS
import temp_paths as TEMPPATHS

_l = LOG


_INKSCAPE_PDF_FATAL_MARKERS = (
    "failed to save inkscape file",
    "couldn't render page in output",
    "error while rendering output",
    "error while rendering page",
    "error while writing to output stream",
    "insufficient memory to store",
)


def _inkscape_pdf_message_is_fatal(message: str | None) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in _INKSCAPE_PDF_FATAL_MARKERS)


def pdfx_version_number(value: str) -> int:
    item = str(value or "3").strip().lower()
    if item in {"1", "1a", "pdf/x-1a"}:
        return 1
    if item in {"4", "pdf/x-4"}:
        return 4
    return 3


def cmyk_merge_kwargs(
    *,
    export_work_dir: str,
    target_pdf: str,
    cmyk_icc: str,
    pure_black_text: bool,
    pdfx_version: str,
) -> dict:
    cmyk_icc_path = ""
    try:
        cmyk_icc_path = ICC.ensure_profile(cmyk_icc)
        _l.i(f"[export.pdf] cmyk ICC resolved='{cmyk_icc_path}'")
    except Exception as ex:
        _l.w(f"[export.pdf] cmyk ICC unavailable '{cmyk_icc}': {ex}")
    if not cmyk_icc_path:
        cmyk_icc_path = GS.get_default_cmyk_icc_profile()
        _l.w(f"[export.pdf] cmyk ICC fallback to Ghostscript default='{cmyk_icc_path}'")

    gs_pdfx_version = pdfx_version_number(pdfx_version)
    pdfx_def = os.path.join(export_work_dir, f"{Path(target_pdf).stem}.pdfx_def.ps")
    GS.write_pdfx_def(
        pdfx_def,
        icc_profile=cmyk_icc_path,
        title=Path(target_pdf).stem,
        output_condition="CMYK print output",
        output_condition_identifier=Path(cmyk_icc_path).stem or "CMYK",
        pdfx_version=gs_pdfx_version,
    )
    return {
        "pdf_settings": None,
        "compatibility_level": "1.6" if gs_pdfx_version == 4 else "1.3",
        "color_conversion_strategy": "CMYK",
        "process_color_model": "DeviceCMYK",
        "output_icc_profile": cmyk_icc_path or None,
        "override_icc": True,
        "text_k_preserve": 2 if pure_black_text else None,
        "safer": False,
        "extra_switches": [
            f"-dPDFX={gs_pdfx_version}",
            pdfx_def,
        ],
    }


def _run_pdf_chunk_job(job: dict, *, exe_dir: str | None, env: dict[str, str] | None, on_output=None) -> dict:
    started = time.perf_counter()
    pdf_path = str(job["pdf_path"])
    try:
        if os.path.isfile(pdf_path):
            os.remove(pdf_path)
    except Exception as ex:
        _l.w("[export.pdf] cannot remove stale chunk pdf='%s': %s", pdf_path, ex)
    rc, msg = INKSCAPE.run(job["argv"], exe_dir=exe_dir, env=env, on_output=on_output)
    elapsed = time.perf_counter() - started
    fatal_msg = _inkscape_pdf_message_is_fatal(msg)
    ok = rc == 0 and not fatal_msg and EXPORT.paths_exist_with_size([pdf_path])
    output_size = 0
    try:
        output_size = os.path.getsize(pdf_path) if os.path.isfile(pdf_path) else 0
    except Exception:
        output_size = 0
    return {
        "index": int(job["index"]),
        "pages": list(job["pages"]),
        "pdf_path": pdf_path,
        "svg_path": job["svg_path"],
        "est_bytes": int(job["est_bytes"]),
        "returncode": int(rc),
        "message": msg,
        "elapsed_s": float(elapsed),
        "ok": bool(ok),
        "soft_ok": False,
        "fatal_message": bool(fatal_msg),
        "output_size": int(output_size),
    }


def export_pdf_via_inkscape(
    svg_path: str,
    pdf_path: str,
    *,
    pdf_profiles: list[str] | None = None,
    raster_filter_mode: str = "png",
    cmyk_icc: str = "",
    cmyk_pure_black_text: bool = True,
    pdfx_version: str = "3",
    export_dpi: int = 300,
    on_page_pdf_created=None,
    on_raster_progress=None,
    on_inkscape_output=None,
    on_ghostscript_output=None,
) -> tuple[bool, dict]:
    _l.i(
        "[export.pdf] start svg='%s' pdf='%s' profiles=%s raster_filter_mode=%s",
        svg_path,
        pdf_path,
        ",".join(pdf_profiles or ["default"]),
        str(raster_filter_mode or "png"),
    )
    export_work_dir = TEMPPATHS.make_work_dir("pdf_export", stem=Path(pdf_path).stem)
    existing_chunk_plan = EXPORT.chunk_plan_from_existing_output(svg_path, export_work_dir, Path(pdf_path).stem) if svg_path else None
    if not svg_path or not os.path.isfile(svg_path):
        if existing_chunk_plan is None:
            return False, {"error": f"SVG output not found: {svg_path}"}
    exe = INKSCAPE.find_executable()
    if not exe:
        return False, {"error": "Inkscape executable not found"}
    selected_profiles = list(pdf_profiles or ["default"])
    page_count = EXPORT.svg_page_count(svg_path) if (svg_path and os.path.isfile(svg_path)) else 0
    EXPORT.cleanup_profile_pdf_outputs(pdf_path, selected_profiles)
    try:
        if os.path.isfile(pdf_path):
            os.remove(pdf_path)
    except Exception:
        pass
    started = time.perf_counter()
    try:
        import inkex
        import raster as RASTER
        from concurrent.futures import ThreadPoolExecutor

        env = INKSCAPE.clean_launch_env()
        exe_dir = os.path.dirname(exe) or None
        max_shell_workers = max(1, int(prefs.get_inkscape_shell_workers(EXPORT.MAX_INKSCAPE_SHELL_WORKERS)))

        chunk_plan = existing_chunk_plan or SVGCHUNKS.write_svg_chunks(
            svg_path,
            pdf_path,
            inkscape_exe=exe,
            artifact_dir=export_work_dir,
            **EXPORT.split_chunk_kwargs(),
        )
        chunks = list(chunk_plan.get("chunks") or [])
        if page_count <= 0:
            page_count = sum(len(getattr(chunk, "pages", ()) or ()) for chunk in chunks)
        fixed_images = int(chunk_plan.get("fixed_images") or 0)
        _l.i(
            "[export.pdf] chunks_ready count=%d fixed_images=%d chunk_dir='%s' work_dir='%s' existing=%s",
            len(chunks),
            fixed_images,
            str(chunk_plan.get("chunk_dir") or ""),
            str(chunk_plan.get("work_dir") or export_work_dir),
            "yes" if bool(chunk_plan.get("from_existing_chunks")) else "no",
        )
        if not chunks:
            return False, {"error": "No SVG parts were generated", "fixed_images": fixed_images}

        mode = str(raster_filter_mode or "png").strip().lower()
        rasterize_filters = mode in {"png", "jpeg", "png_alpha"}
        ignore_filters = mode != "inkscape"
        export_dpi = max(1, int(export_dpi or 300))
        raster_dpi = max(1, int(round(float(export_dpi) * 1.5)))

        if rasterize_filters:
            raster_chunk_workers = max(1, min(len(chunks), max_shell_workers))
            raster_per_chunk_workers = max(1, max_shell_workers // raster_chunk_workers)
            raster_dirs: list[str] = []

            def _rasterize_chunk(chunk):
                export_svg_path = os.path.join(
                    export_work_dir,
                    f"{Path(chunk.svg_path).stem}.raster{Path(chunk.svg_path).suffix or '.svg'}",
                )
                shutil.copy2(chunk.svg_path, export_svg_path)
                with open(export_svg_path, "rb") as fh:
                    doc = inkex.load_svg(fh.read())
                raster_info = RASTER.rasterize_filtered_nodes_for_export(
                    doc,
                    export_svg_path,
                    exe,
                    env,
                    pipeline=mode,
                    progress_callback=on_raster_progress,
                    target_dpi=export_dpi,
                    max_raster_dpi=raster_dpi,
                    max_workers=raster_per_chunk_workers,
                )
                rasterized = int(raster_info.get("rasterized_filters") or 0)
                if rasterized > 0:
                    try:
                        doc.write(export_svg_path, encoding="utf-8", xml_declaration=True)
                    except TypeError:
                        doc.write(export_svg_path)
                return chunk, export_svg_path, rasterized, raster_info

            rasterized_svg_by_index: dict[int, str] = {}
            with ThreadPoolExecutor(max_workers=raster_chunk_workers) as pool:
                futs = [(chunk, pool.submit(_rasterize_chunk, chunk)) for chunk in chunks]
                for _chunk, fut in futs:
                    chunk0, raster_svg_path, rasterized, raster_info = fut.result()
                    rasterized_svg_by_index[int(chunk0.index)] = raster_svg_path
                    raster_dir = str(raster_info.get("raster_dir") or "")
                    if raster_dir:
                        raster_dirs.append(raster_dir)
                    _l.i(
                        "[export.pdf] chunk_raster idx=%d pages=%s rasterized_filters=%d raster_dir='%s'",
                        int(chunk0.index),
                        ",".join(str(p) for p in chunk0.pages),
                        rasterized,
                        raster_dir,
                    )
        else:
            raster_dirs = []
            rasterized_svg_by_index = {}

        jobs = []
        for chunk in chunks:
            job_svg_path = rasterized_svg_by_index.get(int(chunk.index), chunk.svg_path)
            jobs.append({
                "index": int(chunk.index),
                "pages": list(chunk.pages),
                "est_bytes": int(chunk.est_bytes or 0),
                "svg_path": job_svg_path,
                "source_svg_path": chunk.svg_path,
                "pdf_path": chunk.pdf_path,
                "argv": INKSCAPE.build_pdf_export_argv(
                    exe,
                    job_svg_path,
                    chunk.pdf_path,
                    dpi=export_dpi,
                    ignore_filters=ignore_filters,
                ),
            })

        results = []
        with ThreadPoolExecutor(max_workers=max(1, min(max_shell_workers, len(jobs)))) as pool:
            futs = [
                (job, pool.submit(_run_pdf_chunk_job, job, exe_dir=exe_dir, env=env, on_output=on_inkscape_output))
                for job in jobs
            ]
            for job, fut in futs:
                result = fut.result()
                _l.i(
                    "[export.pdf] chunk_done idx=%d pages=%s rc=%d ok=%s fatal_msg=%s size=%d elapsed=%.2fs pdf='%s'",
                    int(result["index"]),
                    ",".join(str(p) for p in result["pages"]),
                    int(result["returncode"]),
                    "yes" if result["ok"] else "no",
                    "yes" if result.get("fatal_message") else "no",
                    int(result.get("output_size") or 0),
                    float(result["elapsed_s"]),
                    result["pdf_path"],
                )
                if result["message"]:
                    _l.i("[export.pdf] chunk_msg idx=%d %s", int(result["index"]), str(result["message"])[:1200])
                results.append(result)
                if result["ok"] and on_page_pdf_created is not None:
                    try:
                        on_page_pdf_created(result["pdf_path"])
                    except Exception:
                        pass

        failed = [r for r in results if not r["ok"]]
        if failed:
            for failed_result in failed:
                job = next((item for item in jobs if int(item["index"]) == int(failed_result["index"])), None)
                if not job:
                    continue
                _l.w(
                    "[export.pdf] chunk_retry idx=%d pages=%s svg='%s' pdf='%s'",
                    int(job["index"]),
                    ",".join(str(p) for p in job["pages"]),
                    job["svg_path"],
                    job["pdf_path"],
                )
                retried = _run_pdf_chunk_job(job, exe_dir=exe_dir, env=env, on_output=on_inkscape_output)
                _l.i(
                    "[export.pdf] chunk_retry_done idx=%d rc=%d ok=%s fatal_msg=%s size=%d elapsed=%.2fs pdf='%s'",
                    int(retried["index"]),
                    int(retried["returncode"]),
                    "yes" if retried["ok"] else "no",
                    "yes" if retried.get("fatal_message") else "no",
                    int(retried.get("output_size") or 0),
                    float(retried["elapsed_s"]),
                    retried["pdf_path"],
                )
                if retried["message"]:
                    _l.i("[export.pdf] chunk_retry_msg idx=%d %s", int(retried["index"]), str(retried["message"])[:1200])
                for idx, current in enumerate(results):
                    if int(current["index"]) == int(retried["index"]):
                        results[idx] = retried
                        break
                if retried["ok"]:
                    _l.w("[export.pdf] chunk_retry_recovered idx=%d svg='%s'", int(retried["index"]), retried["svg_path"])
                    if on_page_pdf_created is not None:
                        try:
                            on_page_pdf_created(retried["pdf_path"])
                        except Exception:
                            pass
                else:
                    _l.w(
                        "[export.pdf] chunk_retry_failed idx=%d svg='%s' pdf='%s'",
                        int(retried["index"]),
                        retried["svg_path"],
                        retried["pdf_path"],
                    )

        total_elapsed = time.perf_counter() - started
        failed = [r for r in results if not r["ok"]]
        if failed:
            first = failed[0]
            err = f"Inkscape PDF export failed for SVG part {first['index']}"
            _l.w("[export.pdf] failed %s svg='%s' pdf='%s'", err, str(first.get("svg_path") or ""), str(first.get("pdf_path") or ""))
            return False, {
                "error": err,
                "failed_chunk_svg": str(first.get("svg_path") or ""),
                "failed_chunk_pdf": str(first.get("pdf_path") or ""),
                "failed_chunk_index": int(first.get("index") or 0),
                "failed_chunk_returncode": int(first.get("returncode") or 0),
                "failed_chunk_fatal_message": bool(first.get("fatal_message")),
                "failed_chunk_message": str(first.get("message") or "")[:1200],
                "elapsed_s": total_elapsed,
                "results": results,
                "page_count": page_count,
                "chunk_count": len(chunks),
                "fixed_images": fixed_images,
                "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
                "work_dir": str(chunk_plan.get("work_dir") or export_work_dir),
                "raster_dirs": raster_dirs,
                "target_chunk_bytes": int(chunk_plan.get("target_chunk_bytes") or EXPORT.DEFAULT_SVG_CHUNK_TARGET_BYTES),
            }

        ordered_chunk_pdfs = [
            item["pdf_path"]
            for item in sorted(results, key=lambda row: (min(row["pages"] or [10**9]), row["index"]))
        ]

        def _merge_profile(profile: str):
            target_pdf = DMPATHS.profile_pdf(pdf_path, profile)
            _l.i("[export.pdf] merge profile='%s' inputs=%d target='%s'", profile, len(ordered_chunk_pdfs), target_pdf)
            merge_kwargs = {
                "detect_duplicate_images": True,
                "pdf_settings": None if profile == "default" else profile,
            }
            if str(profile or "").strip().lower() == "cmyk":
                merge_kwargs.update(cmyk_merge_kwargs(
                    export_work_dir=export_work_dir,
                    target_pdf=target_pdf,
                    cmyk_icc=cmyk_icc,
                    pure_black_text=cmyk_pure_black_text,
                    pdfx_version=pdfx_version,
                ))
            progress_stop = threading.Event()

            def _progress_output(text: str) -> None:
                if on_ghostscript_output is not None:
                    try:
                        on_ghostscript_output(text)
                    except Exception:
                        pass

            def _progress_tick() -> None:
                total = max(1, int(page_count or 0))
                current = 0
                _progress_output(f"PNPINK_FINAL_PDF_PROGRESS {current} {total}\n")
                while not progress_stop.wait(0.7):
                    current = min(total - 1, current + 1)
                    _progress_output(f"PNPINK_FINAL_PDF_PROGRESS {current} {total}\n")

            progress_thread = threading.Thread(target=_progress_tick, daemon=True)
            progress_thread.start()
            try:
                res = GS.merge_pdfs(ordered_chunk_pdfs, target_pdf, on_output=on_ghostscript_output, **merge_kwargs)
            finally:
                progress_stop.set()
                try:
                    progress_thread.join(timeout=0.2)
                except Exception:
                    pass
                _progress_output(f"PNPINK_FINAL_PDF_PROGRESS {max(1, int(page_count or 0))} {max(1, int(page_count or 0))}\n")
            if (
                str(profile or "").strip().lower() == "cmyk"
                and merge_kwargs.get("text_k_preserve") is not None
                and (not res or not getattr(res, "ok", False))
            ):
                _l.w(f"[export.pdf] cmyk retry without TextKPreserve profile='{profile}'")
                merge_kwargs["text_k_preserve"] = None
                res = GS.merge_pdfs(ordered_chunk_pdfs, target_pdf, on_output=on_ghostscript_output, **merge_kwargs)
            return profile, res

        gs_results = []
        _l.i("[export.pdf] merge_profiles total=%d", len(selected_profiles))
        with ThreadPoolExecutor(max_workers=max(1, len(selected_profiles))) as pool:
            futs = [pool.submit(_merge_profile, profile) for profile in selected_profiles]
            for fut in futs:
                gs_results.append(fut.result())

        total_elapsed = time.perf_counter() - started
        failed_profiles = [profile for profile, res in gs_results if not res or not getattr(res, "ok", False)]
        if failed_profiles:
            _l.w("[export.pdf] profile_failures=%s", ",".join(failed_profiles))
            details = []
            for profile, res in gs_results:
                if profile not in failed_profiles:
                    continue
                if not res:
                    details.append(f"{profile}: no output result")
                    continue
                rc = getattr(res, "returncode", "")
                msg = str(getattr(res, "message", "") or "").strip()
                if len(msg) > 700:
                    msg = msg[:700] + "..."
                details.append(f"{profile}: rc={rc} {msg}".strip())
            return False, {
                "error": f"PDF profile failed: {', '.join(failed_profiles)}" + (f" ({' | '.join(details)})" if details else ""),
                "elapsed_s": total_elapsed,
                "results": results,
                "profile_results": [
                    {
                        "profile": profile,
                        "ok": bool(getattr(res, "ok", False)) if res else False,
                        "returncode": getattr(res, "returncode", None) if res else None,
                        "message": getattr(res, "message", "") if res else "",
                        "output_pdf": getattr(res, "output_pdf", "") if res else "",
                    }
                    for profile, res in gs_results
                ],
                "page_count": page_count,
                "chunk_count": len(chunks),
                "fixed_images": fixed_images,
                "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
                "work_dir": str(chunk_plan.get("work_dir") or export_work_dir),
                "raster_dirs": raster_dirs,
                "target_chunk_bytes": int(chunk_plan.get("target_chunk_bytes") or EXPORT.DEFAULT_SVG_CHUNK_TARGET_BYTES),
            }
        return True, {
            "elapsed_s": total_elapsed,
            "results": results,
            "page_count": page_count,
            "chunk_count": len(chunks),
            "pdf_path": pdf_path,
            "fixed_images": fixed_images,
            "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
            "work_dir": str(chunk_plan.get("work_dir") or export_work_dir),
            "raster_dirs": raster_dirs,
            "used_parallel": len(chunks) > 1,
            "target_chunk_bytes": int(chunk_plan.get("target_chunk_bytes") or EXPORT.DEFAULT_SVG_CHUNK_TARGET_BYTES),
            "gs_outputs": [
                {"profile": profile, "output_pdf": getattr(res, "output_pdf", ""), "ok": bool(getattr(res, "ok", False))}
                for profile, res in gs_results
            ],
        }
    except Exception as ex:
        _l.w("[export.pdf] exception %s", str(ex))
        return False, {
            "error": str(ex),
            "fixed_images": 0,
            "work_dir": export_work_dir,
        }
    finally:
        _l.i("[export.pdf] keep_work_dir '%s'", export_work_dir)
