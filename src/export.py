# -*- coding: utf-8 -*-
"""Shared export helpers used by PDF and bitmap exporters."""

from __future__ import annotations

import os
import shutil
import time
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import deckmaker_paths as DMPATHS
import inkscape_cli as INKSCAPE
import log as LOG
import prefs
import svg_chunks as SVGCHUNKS
import temp_paths as TEMPPATHS

_l = LOG


DEFAULT_SVG_CHUNK_TARGET_BYTES = 64 * 1024 * 1024
MAX_INKSCAPE_SHELL_WORKERS = 6


def split_chunk_kwargs() -> dict:
    if not prefs.get_split_svg_output(False):
        return {}
    mode = prefs.get_split_svg_mode("limits")
    if mode == "parts":
        parts = prefs.get_split_svg_parts()
        return {"target_parts": parts}
    limit_mb = prefs.get_split_svg_chunk_mb_optional()
    return {
        "target_chunk_bytes": (int(limit_mb) * 1024 * 1024) if limit_mb else None,
        "target_pages": prefs.get_split_svg_limit_pages(),
        "target_records": prefs.get_split_svg_limit_records(),
    }

_PILLOW_EXPORT_ALIASES = {
    "jpeg": "JPEG",
    "jpg": "JPEG",
    "jpeg2000": "JPEG2000",
    "jp2": "JPEG2000",
    "webp": "WEBP",
    "tiff": "TIFF",
    "tif": "TIFF",
}


def _normalize_export_name(name: str) -> str:
    item = str(name or "").strip().lower()
    if item == "jpg":
        return "jpeg"
    if item == "jp2":
        return "jpeg2000"
    if item == "tif":
        return "tiff"
    return item or "png"


def _inkscape_export_type(name: str) -> str:
    return "jpg" if name == "jpeg" else name


def _pillow_format_status(format_name: str) -> tuple[bool, str]:
    try:
        from PIL import Image  # type: ignore
    except Exception as ex:
        return False, f"Pillow import failed: {ex}"
    fmt = _PILLOW_EXPORT_ALIASES.get(_normalize_export_name(format_name))
    if not fmt:
        return False, f"Unsupported format name: {format_name}"
    try:
        Image.init()
    except Exception as ex:
        return False, f"Pillow plugin init failed: {ex}"
    if fmt not in getattr(Image, "SAVE", {}):
        registered = ",".join(sorted(str(k) for k in getattr(Image, "SAVE", {}).keys()))
        return False, f"Pillow has no SAVE plugin for {fmt}; registered={registered}"
    try:
        test = Image.new("RGB", (1, 1), (255, 255, 255))
        sink = BytesIO()
        test.save(sink, format=fmt)
        return True, f"Pillow supports {fmt}"
    except Exception as ex:
        return False, f"Pillow {fmt} save test failed: {ex}"


def _pillow_supports_format(format_name: str) -> bool:
    ok, _reason = _pillow_format_status(format_name)
    return ok


def _pillow_convert_image(src_png: str, dst_path: str, export_name: str, *, jpeg_quality: int) -> None:
    from PIL import Image  # type: ignore

    normalized = _normalize_export_name(export_name)
    fmt = _PILLOW_EXPORT_ALIASES.get(normalized)
    if not fmt:
        raise ValueError(f"Unsupported Pillow export format: {export_name}")
    params: dict = {}
    if fmt == "JPEG":
        params["quality"] = int(max(70, min(int(jpeg_quality or 90), 95)))
        params["subsampling"] = 0
        params["optimize"] = True
    with Image.open(src_png) as image:
        if fmt == "JPEG":
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
        image.save(dst_path, format=fmt, **params)


def paths_exist_with_size(paths: list[str]) -> bool:
    for path in paths:
        try:
            if not os.path.isfile(path) or os.path.getsize(path) <= 0:
                return False
        except Exception:
            return False
    return True


def svg_page_count(svg_path: str) -> int:
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        count = 0
        for _el in root.findall(".//{http://www.inkscape.org/namespaces/inkscape}page"):
            count += 1
        return count if count > 0 else 1
    except Exception:
        return 1


def parse_page_spec(spec: str, *, max_page: int) -> list[int]:
    text = str(spec or "").strip()
    if not text:
        return list(range(1, max(1, int(max_page or 1)) + 1))
    out: set[int] = set()
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a.strip())
            end = int(b.strip())
            if start > end:
                start, end = end, start
            for page_no in range(start, end + 1):
                if 1 <= page_no <= int(max_page):
                    out.add(page_no)
        else:
            page_no = int(part)
            if 1 <= page_no <= int(max_page):
                out.add(page_no)
    return sorted(out)


def resolve_chunked_output_source(svg_path: str) -> dict:
    return SVGCHUNKS.resolve_chunked_output(svg_path)


def chunk_plan_from_existing_output(svg_path: str, artifact_dir: str, artifact_stem: str) -> dict | None:
    if not prefs.get_split_svg_output(False):
        return None
    src_info = resolve_chunked_output_source(svg_path)
    chunk_paths = list(src_info.get("chunk_paths") or [])
    if not chunk_paths:
        return None
    chunks = SVGCHUNKS.build_chunk_outputs(
        chunk_paths,
        artifact_dir=artifact_dir,
        artifact_stem=artifact_stem,
    )
    return {
        "chunks": tuple(chunks),
        "chunk_dir": str(src_info.get("chunk_dir") or ""),
        "work_dir": os.path.normpath(artifact_dir),
        "fixed_images": 0,
        "source_svg": svg_path,
        "from_existing_chunks": True,
        "manifest_path": str(src_info.get("manifest_path") or ""),
    }


def cleanup_png_outputs(png_path: str, page_count: int) -> None:
    targets = [DMPATHS.normalize(png_path)] + DMPATHS.output_page_pngs(png_path, page_count)
    for target in targets:
        try:
            if os.path.isfile(target):
                os.remove(target)
        except Exception:
            pass


def cleanup_profile_pdf_outputs(pdf_path: str, profiles: list[str]) -> None:
    for profile in profiles or []:
        target = DMPATHS.profile_pdf(pdf_path, profile)
        try:
            if os.path.isfile(target):
                os.remove(target)
        except Exception:
            pass


def export_png_pages_via_inkscape(
    svg_path: str,
    png_path: str,
    *,
    export_dpi: int = 300,
    on_page_png_created=None,
) -> tuple[bool, dict]:
    _l.i("[export.png] start svg='%s' png='%s'", svg_path, png_path)
    export_work_dir = TEMPPATHS.make_work_dir("png_export", stem=Path(png_path).stem)
    existing_chunk_plan = chunk_plan_from_existing_output(svg_path, export_work_dir, Path(png_path).stem) if svg_path else None
    if not svg_path or not os.path.isfile(svg_path):
        if existing_chunk_plan is None:
            return False, {"error": f"SVG output not found: {svg_path}"}
    exe = INKSCAPE.find_executable()
    if not exe:
        return False, {"error": "Inkscape executable not found"}
    page_count = svg_page_count(svg_path) if (svg_path and os.path.isfile(svg_path)) else 0
    expected_pngs = DMPATHS.output_page_pngs(png_path, page_count)
    cleanup_png_outputs(png_path, page_count)
    export_dpi = max(1, int(export_dpi or 300))
    started = time.perf_counter()

    try:
        env = INKSCAPE.clean_launch_env()
        exe_dir = os.path.dirname(exe) or None
        from concurrent.futures import ThreadPoolExecutor

        chunk_plan = existing_chunk_plan or SVGCHUNKS.write_svg_chunks(
            svg_path,
            os.path.splitext(png_path)[0] + ".pdf",
            inkscape_exe=exe,
            artifact_dir=export_work_dir,
            **split_chunk_kwargs(),
        )
        chunks = list(chunk_plan.get("chunks") or [])
        if page_count <= 0:
            page_count = sum(len(getattr(chunk, "pages", ()) or ()) for chunk in chunks)
            expected_pngs = DMPATHS.output_page_pngs(png_path, page_count)
        fixed_images = int(chunk_plan.get("fixed_images") or 0)
        _l.i(
            "[export.png] chunks_ready count=%d fixed_images=%d chunk_dir='%s' work_dir='%s' existing=%s",
            len(chunks),
            fixed_images,
            str(chunk_plan.get("chunk_dir") or ""),
            str(chunk_plan.get("work_dir") or export_work_dir),
            "yes" if bool(chunk_plan.get("from_existing_chunks")) else "no",
        )
        jobs = []
        for chunk in chunks:
            local_expected = DMPATHS.output_page_pngs(chunk.png_prefix, len(chunk.pages))
            local_page_exports = [(idx, path) for idx, path in enumerate(local_expected, start=1)]
            commands = INKSCAPE.build_shell_png_page_commands(chunk.svg_path, local_page_exports, dpi=export_dpi)
            jobs.append({
                "index": int(chunk.index),
                "pages": list(chunk.pages),
                "chunk_png_prefix": chunk.png_prefix,
                "expected_pngs": local_expected,
                "commands": commands,
            })

        results = []
        max_shell_workers = max(1, int(prefs.get_inkscape_shell_workers(MAX_INKSCAPE_SHELL_WORKERS)))
        with ThreadPoolExecutor(max_workers=max(1, min(max_shell_workers, len(jobs)))) as pool:
            futs = []
            for job in jobs:
                job_started = time.perf_counter()
                fut = pool.submit(
                    INKSCAPE.run_shell_commands,
                    exe,
                    job["commands"],
                    exe_dir=exe_dir,
                    env=env,
                )
                futs.append((job, job_started, fut))
            for job, job_started, fut in futs:
                rc, msg = fut.result()
                elapsed = time.perf_counter() - job_started
                ok = paths_exist_with_size(job["expected_pngs"])
                _l.i(
                    "[export.png] chunk_done idx=%d pages=%s rc=%d ok=%s elapsed=%.2fs prefix='%s'",
                    int(job["index"]),
                    ",".join(str(p) for p in job["pages"]),
                    int(rc),
                    "yes" if ok else "no",
                    float(elapsed),
                    job["chunk_png_prefix"],
                )
                if msg:
                    _l.i("[export.png] chunk_msg idx=%d %s", int(job["index"]), str(msg)[:1200])
                if ok:
                    ordered_local = list(job["expected_pngs"])
                    for page_no, src in zip(job["pages"], ordered_local):
                        final_path = DMPATHS.output_page_png(png_path, page_no) if page_count > 1 else DMPATHS.normalize(png_path)
                        try:
                            if os.path.isfile(final_path):
                                os.remove(final_path)
                        except Exception:
                            pass
                        try:
                            shutil.move(src, final_path)
                        except Exception:
                            pass
                        if on_page_png_created is not None and os.path.isfile(final_path):
                            try:
                                on_page_png_created(final_path)
                            except Exception:
                                pass
                results.append({
                    "index": job["index"],
                    "pages": list(job["pages"]),
                    "png_path": job["chunk_png_prefix"],
                    "expected_pngs": list(job["expected_pngs"]),
                    "returncode": rc,
                    "message": msg,
                    "elapsed_s": elapsed,
                    "ok": ok,
                    "soft_ok": rc != 0 and ok,
                })

        total_elapsed = time.perf_counter() - started
        failed = [r for r in results if not r["ok"]]
        if failed:
            first = failed[0]
            _l.w("[export.png] failed worker=%d", int(first["index"]))
            return False, {
                "error": f"Inkscape PNG export failed for worker {first['index']}",
                "elapsed_s": total_elapsed,
                "results": results,
                "page_count": page_count,
                "chunk_count": len(chunks),
                "fixed_images": fixed_images,
                "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
                "work_dir": str(chunk_plan.get("work_dir") or export_work_dir),
            }
        return True, {
            "elapsed_s": total_elapsed,
            "results": results,
            "page_count": page_count,
            "chunk_count": len(chunks),
            "png_path": png_path,
            "png_outputs": expected_pngs,
            "fixed_images": fixed_images,
            "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
            "work_dir": str(chunk_plan.get("work_dir") or export_work_dir),
            "used_parallel": len(chunks) > 1,
        }
    except Exception as ex:
        _l.w("[export.png] exception %s", str(ex))
        return False, {
            "error": str(ex),
            "fixed_images": 0,
            "work_dir": export_work_dir,
        }
    finally:
        try:
            shutil.rmtree(export_work_dir, ignore_errors=True)
        except Exception:
            pass


def export_other_pages_via_inkscape(
    svg_path: str,
    out_path: str,
    *,
    export_type: str,
    page_spec: str = "",
    export_dpi: int = 300,
    jpeg_quality: int = 90,
    on_page_created=None,
) -> tuple[bool, dict]:
    export_name = _normalize_export_name(export_type)
    if export_name == "png":
        return export_png_pages_via_inkscape(svg_path, out_path, export_dpi=export_dpi, on_page_png_created=on_page_created)
    pillow_ok, pillow_reason = _pillow_format_status(export_name)
    use_png_intermediate = export_name in {"jpeg", "tiff", "jpeg2000", "webp"} and pillow_ok
    if export_name in {"tiff", "jpeg2000", "webp"} and not use_png_intermediate:
        _l.w("[export.%s] pillow unsupported: %s", export_name, pillow_reason)
        return False, {
            "error": f"Pillow does not support {export_name.upper()} on this system: {pillow_reason}",
            "fixed_images": 0,
        }
    if use_png_intermediate:
        _l.i("[export.%s] using PNG intermediate + Pillow: %s", export_name, pillow_reason)

    _l.i("[export.%s] start svg='%s' out='%s' pages='%s'", export_name, svg_path, out_path, page_spec)
    export_work_dir = TEMPPATHS.make_work_dir(f"{export_name}_export", stem=Path(out_path).stem)
    existing_chunk_plan = chunk_plan_from_existing_output(svg_path, export_work_dir, Path(out_path).stem) if svg_path else None
    if not svg_path or not os.path.isfile(svg_path):
        if existing_chunk_plan is None:
            return False, {"error": f"SVG output not found: {svg_path}"}
    exe = INKSCAPE.find_executable()
    if not exe:
        return False, {"error": "Inkscape executable not found"}
    page_count = svg_page_count(svg_path) if (svg_path and os.path.isfile(svg_path)) else 0
    export_dpi = max(1, int(export_dpi or 300))
    started = time.perf_counter()

    try:
        env = INKSCAPE.clean_launch_env()
        exe_dir = os.path.dirname(exe) or None
        from concurrent.futures import ThreadPoolExecutor

        chunk_plan = existing_chunk_plan or SVGCHUNKS.write_svg_chunks(
            svg_path,
            os.path.splitext(out_path)[0] + ".pdf",
            inkscape_exe=exe,
            artifact_dir=export_work_dir,
            **split_chunk_kwargs(),
        )
        chunks = list(chunk_plan.get("chunks") or [])
        if page_count <= 0:
            page_count = sum(len(getattr(chunk, "pages", ()) or ()) for chunk in chunks)
        selected_pages = parse_page_spec(page_spec, max_page=max(1, page_count))
        if not selected_pages:
            return False, {"error": f"No pages selected for export: {page_spec}"}
        selected_set = set(selected_pages)
        fixed_images = int(chunk_plan.get("fixed_images") or 0)
        _l.i(
            "[export.%s] chunks_ready count=%d fixed_images=%d chunk_dir='%s' work_dir='%s' existing=%s selected_pages=%s",
            export_name,
            len(chunks),
            fixed_images,
            str(chunk_plan.get("chunk_dir") or ""),
            str(chunk_plan.get("work_dir") or export_work_dir),
            "yes" if bool(chunk_plan.get("from_existing_chunks")) else "no",
            ",".join(str(p) for p in selected_pages[:20]),
        )
        jobs = []
        for chunk in chunks:
            local_page_exports = []
            final_paths = []
            for local_idx, global_page in enumerate(chunk.pages, start=1):
                if int(global_page) not in selected_set:
                    continue
                final_path = DMPATHS.output_page(out_path, int(global_page)) if len(selected_pages) > 1 else DMPATHS.normalize(out_path)
                temp_ext = "png" if use_png_intermediate else export_name
                temp_path = os.path.join(export_work_dir, f"{Path(out_path).stem}_p{int(global_page)}.{temp_ext}")
                local_page_exports.append((local_idx, temp_path))
                final_paths.append((temp_path, final_path))
            if not local_page_exports:
                continue
            commands = INKSCAPE.build_shell_page_export_commands(
                chunk.svg_path,
                local_page_exports,
                export_type=_inkscape_export_type("png" if use_png_intermediate else export_name),
                dpi=export_dpi,
            )
            jobs.append({
                "index": int(chunk.index),
                "pages": [int(p) for p in chunk.pages if int(p) in selected_set],
                "expected_paths": list(final_paths),
                "commands": commands,
            })

        results = []
        max_shell_workers = max(1, int(prefs.get_inkscape_shell_workers(MAX_INKSCAPE_SHELL_WORKERS)))
        with ThreadPoolExecutor(max_workers=max(1, min(max_shell_workers, len(jobs) or 1))) as pool:
            futs = []
            for job in jobs:
                job_started = time.perf_counter()
                fut = pool.submit(
                    INKSCAPE.run_shell_commands,
                    exe,
                    job["commands"],
                    exe_dir=exe_dir,
                    env=env,
                )
                futs.append((job, job_started, fut))
            for job, job_started, fut in futs:
                rc, msg = fut.result()
                elapsed = time.perf_counter() - job_started
                ok = paths_exist_with_size([src for src, _dst in job["expected_paths"]])
                _l.i(
                    "[export.%s] chunk_done idx=%d pages=%s rc=%d ok=%s elapsed=%.2fs",
                    export_name,
                    int(job["index"]),
                    ",".join(str(p) for p in job["pages"]),
                    int(rc),
                    "yes" if ok else "no",
                    float(elapsed),
                )
                if msg:
                    _l.i("[export.%s] chunk_msg idx=%d %s", export_name, int(job["index"]), str(msg)[:1200])
                if ok:
                    for temp_path, final_path in list(job["expected_paths"]):
                        try:
                            if os.path.isfile(final_path):
                                os.remove(final_path)
                        except Exception:
                            pass
                        try:
                            if use_png_intermediate:
                                _pillow_convert_image(temp_path, final_path, export_name, jpeg_quality=jpeg_quality)
                                try:
                                    os.remove(temp_path)
                                except Exception:
                                    pass
                            else:
                                shutil.move(temp_path, final_path)
                        except Exception:
                            pass
                        if on_page_created is not None and os.path.isfile(final_path):
                            try:
                                on_page_created(final_path)
                            except Exception:
                                pass
                results.append({
                    "index": job["index"],
                    "pages": list(job["pages"]),
                    "expected_paths": list(job["expected_paths"]),
                    "returncode": rc,
                    "message": msg,
                    "elapsed_s": elapsed,
                    "ok": ok,
                    "soft_ok": rc != 0 and ok,
                })

        total_elapsed = time.perf_counter() - started
        failed = [r for r in results if not r["ok"]]
        if failed:
            first = failed[0]
            _l.w("[export.%s] failed worker=%d", export_name, int(first["index"]))
            return False, {
                "error": f"Inkscape {export_name.upper()} export failed for worker {first['index']}",
                "elapsed_s": total_elapsed,
                "results": results,
                "page_count": page_count,
                "selected_pages": selected_pages,
                "chunk_count": len(chunks),
                "fixed_images": fixed_images,
                "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
                "work_dir": str(chunk_plan.get("work_dir") or export_work_dir),
            }
        return True, {
            "elapsed_s": total_elapsed,
            "results": results,
            "page_count": page_count,
            "selected_pages": selected_pages,
            "chunk_count": len(chunks),
            "output_path": out_path,
            "fixed_images": fixed_images,
            "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
            "work_dir": str(chunk_plan.get("work_dir") or export_work_dir),
            "used_parallel": len(chunks) > 1,
        }
    except Exception as ex:
        _l.w("[export.%s] exception %s", export_name, str(ex))
        return False, {
            "error": str(ex),
            "fixed_images": 0,
            "work_dir": export_work_dir,
        }
    finally:
        try:
            shutil.rmtree(export_work_dir, ignore_errors=True)
        except Exception:
            pass
