#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resident DeckMaker launcher app.

This is intentionally small: the Inkscape extension only sends the current SVG
template to this process, and the process runs the existing engine on demand.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import traceback
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from xml.etree import ElementTree as ET

import log as LOG
import inkscape_cli as INKSCAPE
import prefs
import raster as RASTER
import svg_chunks as SVGCHUNKS

_l = LOG

HOST = "127.0.0.1"
PORT = 48751
ENV_DIRECT_RUN = "PNPINK_DECKMAKER_DIRECT"
DEFAULT_SVG_CHUNK_TARGET_BYTES = 64 * 1024 * 1024
MAX_INKSCAPE_SHELL_WORKERS = 6


@dataclass
class AppRequest:
    template: str
    sheet_id: str = ""
    sheet_range: str = ""
    log_level: str = "global"


@dataclass(frozen=True)
class ExportOptions:
    formats: tuple[str, ...]
    pdf_profiles: tuple[str, ...]
    pdf_raster_filters: bool


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(str(path or "").strip()))


def _app_icon_path() -> str:
    return os.path.join(os.path.dirname(__file__), "examples", "assets", "deckmaker_icon.png")


def _resolve_output_svg_path(template: str) -> str:
    p = _normalize_path(template)
    base_dir = os.path.dirname(p)
    stem, _ext = os.path.splitext(os.path.basename(p))
    return os.path.normpath(os.path.join(base_dir, f"{stem}_output.svg"))


def _resolve_output_pdf_path(template: str) -> str:
    svg_path = _resolve_output_svg_path(template)
    stem, _ext = os.path.splitext(svg_path)
    return os.path.normpath(stem + ".pdf")


def _resolve_output_png_path(template: str) -> str:
    svg_path = _resolve_output_svg_path(template)
    stem, _ext = os.path.splitext(svg_path)
    return os.path.normpath(stem + ".png")


def _app_log_path() -> str:
    return os.path.join(os.path.dirname(__file__), "pnpink.log")


def _resolve_profile_output_pdf_path(pdf_path: str, profile: str) -> str:
    base_pdf = _normalize_path(pdf_path)
    prof = str(profile or "default").strip().lower()
    if prof == "default":
        return base_pdf
    stem, ext = os.path.splitext(base_pdf)
    return os.path.normpath(f"{stem}_{prof}{ext or '.pdf'}")


def _resolve_output_page_pdf_path(pdf_path: str, page_number: int) -> str:
    stem, ext = os.path.splitext(_normalize_path(pdf_path))
    return os.path.normpath(f"{stem}_p{int(page_number)}{ext or '.pdf'}")


def _all_output_page_pdf_paths(pdf_path: str, page_count: int) -> list[str]:
    total = max(1, int(page_count or 1))
    return [_resolve_output_page_pdf_path(pdf_path, page_no) for page_no in range(1, total + 1)]


def _resolve_output_page_png_path(png_path: str, page_number: int) -> str:
    stem, ext = os.path.splitext(_normalize_path(png_path))
    return os.path.normpath(f"{stem}_p{int(page_number)}{ext or '.png'}")


def _all_output_page_png_paths(png_path: str, page_count: int) -> list[str]:
    total = max(1, int(page_count or 1))
    if total == 1:
        return [_normalize_path(png_path)]
    return [_resolve_output_page_png_path(png_path, page_no) for page_no in range(1, total + 1)]


def _cleanup_pdf_outputs(pdf_path: str, page_count: int) -> None:
    targets = [_normalize_path(pdf_path)] + _all_output_page_pdf_paths(pdf_path, page_count)
    for target in targets:
        try:
            if os.path.isfile(target):
                os.remove(target)
        except Exception:
            pass


def _cleanup_png_outputs(png_path: str, page_count: int) -> None:
    targets = [_normalize_path(png_path)] + _all_output_page_png_paths(png_path, page_count)
    for target in targets:
        try:
            if os.path.isfile(target):
                os.remove(target)
        except Exception:
            pass


def _cleanup_profile_pdf_outputs(pdf_path: str, profiles: list[str]) -> None:
    for profile in profiles or []:
        target = _resolve_profile_output_pdf_path(pdf_path, profile)
        try:
            if os.path.isfile(target):
                os.remove(target)
        except Exception:
            pass


def _paths_exist_with_size(paths: list[str]) -> bool:
    for path in paths:
        try:
            if not os.path.isfile(path) or os.path.getsize(path) <= 0:
                return False
        except Exception:
            return False
    return True


def _bitmap_size_px(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(str(path)) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None


def _effective_image_dpi_report(svg_path: str) -> dict:
    try:
        import inkex
        import svg as SVG

        source_info = _resolve_chunked_output_source(svg_path)
        svg_inputs = []
        if source_info.get("svg_path"):
            svg_inputs = [str(source_info.get("svg_path") or "")]
        else:
            svg_inputs = [str(p) for p in (source_info.get("chunk_paths") or []) if str(p or "").strip()]
        if not svg_inputs:
            raise FileNotFoundError(svg_path)

        rows = []
        unresolved = 0
        unreadable = 0
        idx = 0
        for svg_input in svg_inputs:
            with open(svg_input, "rb") as fh:
                doc = inkex.load_svg(fh.read())
            root = doc.getroot()
            images = root.xpath(".//svg:image", namespaces=inkex.NSS)
            for im in images:
                idx += 1
                href = SVG.get_href(im)
                absref = im.get(SVG.SODI_ABSREF) or ""
                path = SVG._resolve_image_path(href, absref, svg_input)
                if not path:
                    unresolved += 1
                    continue
                bitmap = _bitmap_size_px(path)
                if not bitmap:
                    unreadable += 1
                    continue
                w_px, h_px = bitmap
                placed_w = SVG.parse_len_px(root, im.get("width") or "0")
                placed_h = SVG.parse_len_px(root, im.get("height") or "0")
                if placed_w <= 0 or placed_h <= 0:
                    continue
                try:
                    t = SVG.composed_transform(im)
                    pts = [
                        t.apply_to_point((0, 0)),
                        t.apply_to_point((placed_w, 0)),
                        t.apply_to_point((0, placed_h)),
                        t.apply_to_point((placed_w, placed_h)),
                    ]
                    xs = [float(p[0]) for p in pts]
                    ys = [float(p[1]) for p in pts]
                    placed_w = max(xs) - min(xs)
                    placed_h = max(ys) - min(ys)
                except Exception:
                    pass
                if placed_w <= 0 or placed_h <= 0:
                    continue
                dpi_x = w_px / (placed_w / 96.0)
                dpi_y = h_px / (placed_h / 96.0)
                dpi = min(dpi_x, dpi_y)
                rows.append({
                    "index": idx,
                    "id": im.get("id") or f"image-{idx}",
                    "path": str(path),
                    "file": Path(path).name,
                    "dpi": float(dpi),
                    "dpi_x": float(dpi_x),
                    "dpi_y": float(dpi_y),
                    "px": (w_px, h_px),
                    "placed_mm": (placed_w * 25.4 / 96.0, placed_h * 25.4 / 96.0),
                })
        rows.sort(key=lambda item: item["dpi"])
        return {
            "ok": True,
            "count": len(rows),
            "unresolved": unresolved,
            "unreadable": unreadable,
            "rows": rows,
            "low": [r for r in rows if r["dpi"] < 150.0],
            "high": [r for r in rows if r["dpi"] > 900.0],
        }
    except Exception as ex:
        return {"ok": False, "error": str(ex), "count": 0, "rows": []}


def _wait_for_stable_outputs(
    paths: list[str],
    *,
    stable_checks: int = 2,
    interval_s: float = 0.5,
    timeout_s: float = 600.0,
) -> bool:
    pending = list(paths or [])
    if not pending:
        return False
    stable = 0
    last_sizes: dict[str, int] = {}
    started = time.perf_counter()
    while True:
        if (time.perf_counter() - started) > max(1.0, float(timeout_s or 600.0)):
            return False
        current: dict[str, int] = {}
        ready = True
        for path in pending:
            try:
                if not os.path.isfile(path):
                    ready = False
                    break
                size = int(os.path.getsize(path))
                if size <= 0:
                    ready = False
                    break
                current[path] = size
            except Exception:
                ready = False
                break
        if ready:
            if current == last_sizes:
                stable += 1
            else:
                stable = 1
                last_sizes = current
            if stable >= max(1, int(stable_checks or 1)):
                return True
        else:
            stable = 0
            last_sizes = {}
        time.sleep(max(0.1, float(interval_s or 0.5)))


def _watch_created_pdfs(
    paths: list[str],
    on_created,
    *,
    stop_event: threading.Event | None = None,
    interval_s: float = 0.25,
) -> None:
    pending = {_normalize_path(p) for p in (paths or []) if str(p or "").strip()}
    seen: set[str] = set()
    while pending:
        if stop_event is not None and stop_event.is_set():
            return
        ready_now = []
        for path in list(pending):
            try:
                if os.path.isfile(path) and os.path.getsize(path) > 0:
                    ready_now.append(path)
            except Exception:
                continue
        for path in ready_now:
            pending.discard(path)
            if path in seen:
                continue
            seen.add(path)
            try:
                on_created(path)
            except Exception:
                pass
        time.sleep(max(0.1, float(interval_s or 0.25)))


def _svg_page_count(svg_path: str) -> int:
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        count = 0
        for _el in root.findall(".//{http://www.inkscape.org/namespaces/inkscape}page"):
            count += 1
        return count if count > 0 else 1
    except Exception:
        return 1


def _page_selector(pages: list[int]) -> str:
    vals = [int(p) for p in (pages or []) if int(p) > 0]
    if not vals:
        return "1"
    return ",".join(str(p) for p in vals)


def _chunk_page_groups(page_count: int, max_chunks: int = 3) -> list[list[int]]:
    total = max(1, int(page_count or 1))
    chunks = max(1, min(int(max_chunks or 1), total))
    if chunks == 1:
        return [list(range(1, total + 1))]
    groups: list[list[int]] = []
    for offset in range(chunks):
        groups.append(list(range(1 + offset, total + 1, chunks)))
    return groups


def _resolve_chunked_output_source(svg_path: str) -> dict:
    src = _normalize_path(svg_path)
    if not src:
        return {"svg_path": "", "chunk_paths": [], "manifest_path": "", "chunk_dir": ""}
    if os.path.isfile(src):
        return {"svg_path": src, "chunk_paths": [], "manifest_path": "", "chunk_dir": ""}
    out_dir = os.path.dirname(os.path.abspath(src)) or "."
    stem = Path(src).stem
    chunk_dir = os.path.join(out_dir, f"{stem}_chunks")
    manifest_path = os.path.join(chunk_dir, f"{stem}.chunks.txt")
    chunk_paths: list[str] = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                chunk_paths = [os.path.normpath(line.strip()) for line in fh if line.strip()]
        except Exception:
            chunk_paths = []
    if not chunk_paths and os.path.isdir(chunk_dir):
        try:
            chunk_paths = sorted(
                os.path.normpath(os.path.join(chunk_dir, name))
                for name in os.listdir(chunk_dir)
                if re.fullmatch(rf"{re.escape(stem)}\.chunk\d+\.svg", name, re.IGNORECASE)
            )
        except Exception:
            chunk_paths = []
    chunk_paths = [path for path in chunk_paths if os.path.isfile(path)]
    return {
        "svg_path": src if os.path.isfile(src) else "",
        "chunk_paths": chunk_paths,
        "manifest_path": manifest_path if os.path.isfile(manifest_path) else "",
        "chunk_dir": chunk_dir if os.path.isdir(chunk_dir) else "",
    }


def _chunk_plan_from_existing_output(svg_path: str, pdf_path: str) -> dict | None:
    src_info = _resolve_chunked_output_source(svg_path)
    chunk_paths = list(src_info.get("chunk_paths") or [])
    if not chunk_paths:
        return None
    import svg_chunks as SVGCHUNKS
    chunks = []
    stem = Path(pdf_path).stem
    chunk_dir = str(src_info.get("chunk_dir") or (os.path.dirname(chunk_paths[0]) if chunk_paths else ""))
    for idx, chunk_svg_path in enumerate(chunk_paths, start=1):
        page_count = max(1, _svg_page_count(chunk_svg_path))
        pdf_chunk_path = os.path.join(chunk_dir, f"{stem}.chunk{idx:02d}.pdf")
        png_chunk_prefix = os.path.join(chunk_dir, f"{stem}.chunk{idx:02d}.png")
        chunks.append(
            SVGCHUNKS.SvgChunk(
                index=idx,
                pages=tuple(range(1, page_count + 1)),
                est_bytes=0,
                svg_path=os.path.normpath(chunk_svg_path),
                pdf_path=os.path.normpath(pdf_chunk_path),
                png_prefix=os.path.normpath(png_chunk_prefix),
            )
        )
    return {
        "chunks": tuple(chunks),
        "chunk_dir": os.path.normpath(chunk_dir) if chunk_dir else "",
        "fixed_images": 0,
        "source_svg": svg_path,
        "from_existing_chunks": True,
        "manifest_path": str(src_info.get("manifest_path") or ""),
    }


def _prepare_export_svg(svg_path: str, *, inkscape_exe: str | None = None, rasterize_filters: bool = False) -> tuple[str, dict]:
    try:
        import inkex
        import svg_chunks as SVGCHUNKS

        with open(svg_path, "rb") as fh:
            raw = fh.read()
        doc = inkex.load_svg(raw)
        normalize_info = SVGCHUNKS.normalize_output_doc(doc, source_svg_path=svg_path, inkscape_exe=inkscape_exe)
        fixed = int(normalize_info.get("fixed_images") or 0)

        fd, tmp_path = tempfile.mkstemp(prefix="pnpink_export_", suffix=".svg")
        os.close(fd)
        try:
            doc.write(tmp_path, encoding="utf-8", xml_declaration=True)
        except TypeError:
            doc.write(tmp_path)

        raster_info = {}
        if rasterize_filters and inkscape_exe:
            env = INKSCAPE.clean_launch_env()
            raster_info = RASTER.rasterize_filtered_nodes_for_export(
                doc,
                tmp_path,
                inkscape_exe,
                env,
                target_dpi=300,
                max_raster_dpi=600,
                max_workers=3,
            )
            if int(raster_info.get("rasterized_filters") or 0) > 0:
                try:
                    doc.write(tmp_path, encoding="utf-8", xml_declaration=True)
                except TypeError:
                    doc.write(tmp_path)

        info = {"fixed_images": fixed, "source_svg": svg_path}
        info.update(normalize_info)
        info.update(raster_info)
        return tmp_path, info
    except Exception as ex:
        return "", {"error": f"Failed to prepare export SVG: {ex}", "source_svg": svg_path}


def _export_pdf_via_inkscape(
    svg_path: str,
    pdf_path: str,
    *,
    pdf_profiles: list[str] | None = None,
    rasterize_filters: bool = False,
    chunk_count: int = 3,
    on_page_pdf_created=None,
) -> tuple[bool, dict]:
    _l.i(
        "[export.pdf] start svg='%s' pdf='%s' profiles=%s rasterize_filters=%s",
        svg_path,
        pdf_path,
        ",".join(pdf_profiles or ["default"]),
        "yes" if rasterize_filters else "no",
    )
    existing_chunk_plan = None
    if not svg_path or not os.path.isfile(svg_path):
        existing_chunk_plan = _chunk_plan_from_existing_output(svg_path, pdf_path) if svg_path else None
        if existing_chunk_plan is None:
            return False, {"error": f"SVG output not found: {svg_path}"}
    exe = INKSCAPE.find_executable()
    if not exe:
        return False, {"error": "Inkscape executable not found"}
    selected_profiles = list(pdf_profiles or ["default"])
    page_count = _svg_page_count(svg_path) if (svg_path and os.path.isfile(svg_path)) else 0
    _cleanup_profile_pdf_outputs(pdf_path, selected_profiles)
    try:
        if os.path.isfile(pdf_path):
            os.remove(pdf_path)
    except Exception:
        pass
    started = time.perf_counter()
    try:
        import inkex
        env = INKSCAPE.clean_launch_env()
        exe_dir = os.path.dirname(exe) or None
        from concurrent.futures import ThreadPoolExecutor
        import gs as GS
        import raster as RASTER

        chunk_plan = existing_chunk_plan or SVGCHUNKS.write_svg_chunks(
            svg_path,
            pdf_path,
            inkscape_exe=exe,
            target_chunk_bytes=DEFAULT_SVG_CHUNK_TARGET_BYTES,
        )
        chunks = list(chunk_plan.get("chunks") or [])
        if page_count <= 0:
            page_count = sum(len(getattr(chunk, "pages", ()) or ()) for chunk in chunks)
        fixed_images = int(chunk_plan.get("fixed_images") or 0)
        _l.i(
            "[export.pdf] chunks_ready count=%d fixed_images=%d chunk_dir='%s' existing=%s",
            len(chunks),
            fixed_images,
            str(chunk_plan.get("chunk_dir") or ""),
            "yes" if bool(chunk_plan.get("from_existing_chunks")) else "no",
        )
        if not chunks:
            return False, {"error": "No SVG chunks were generated", "fixed_images": fixed_images}

        if rasterize_filters:
            max_shell_workers = max(1, int(prefs.get_inkscape_shell_workers(MAX_INKSCAPE_SHELL_WORKERS)))
            raster_chunk_workers = max(1, min(len(chunks), max_shell_workers))
            raster_per_chunk_workers = max(1, max_shell_workers // raster_chunk_workers)

            def _rasterize_chunk(chunk):
                with open(chunk.svg_path, "rb") as fh:
                    doc = inkex.load_svg(fh.read())
                raster_info = RASTER.rasterize_filtered_nodes_for_export(
                    doc,
                    chunk.svg_path,
                    exe,
                    env,
                    target_dpi=600,
                    max_raster_dpi=600,
                    max_workers=raster_per_chunk_workers,
                )
                rasterized = int(raster_info.get("rasterized_filters") or 0)
                if rasterized > 0:
                    try:
                        doc.write(chunk.svg_path, encoding="utf-8", xml_declaration=True)
                    except TypeError:
                        doc.write(chunk.svg_path)
                return chunk, rasterized, raster_info

            rasterized_total = 0
            with ThreadPoolExecutor(max_workers=raster_chunk_workers) as pool:
                futs = [(chunk, pool.submit(_rasterize_chunk, chunk)) for chunk in chunks]
                for chunk, fut in futs:
                    chunk0, rasterized, raster_info = fut.result()
                    rasterized_total += rasterized
                    _l.i(
                        "[export.pdf] chunk_raster idx=%d pages=%s rasterized_filters=%d raster_dir='%s'",
                        int(chunk0.index),
                        ",".join(str(p) for p in chunk0.pages),
                        rasterized,
                        str(raster_info.get("raster_dir") or ""),
                    )

        jobs = []
        for chunk in chunks:
            commands = INKSCAPE.build_shell_pdf_commands(
                chunk.svg_path,
                chunk.pdf_path,
                dpi=300,
                ignore_filters=True,
            )
            jobs.append({
                "index": int(chunk.index),
                "pages": list(chunk.pages),
                "est_bytes": int(chunk.est_bytes or 0),
                "svg_path": chunk.svg_path,
                "pdf_path": chunk.pdf_path,
                "commands": commands,
            })

        results = []
        max_shell_workers = max(1, int(prefs.get_inkscape_shell_workers(MAX_INKSCAPE_SHELL_WORKERS)))
        max_workers = max(1, min(max_shell_workers, len(jobs)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
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
                ok = _paths_exist_with_size([job["pdf_path"]])
                _l.i(
                    "[export.pdf] chunk_done idx=%d pages=%s rc=%d ok=%s elapsed=%.2fs pdf='%s'",
                    int(job["index"]),
                    ",".join(str(p) for p in job["pages"]),
                    int(rc),
                    "yes" if ok else "no",
                    float(elapsed),
                    job["pdf_path"],
                )
                if msg:
                    _l.i("[export.pdf] chunk_msg idx=%d %s", int(job["index"]), str(msg)[:1200])
                results.append({
                    "index": job["index"],
                    "pages": list(job["pages"]),
                    "pdf_path": job["pdf_path"],
                    "svg_path": job["svg_path"],
                    "est_bytes": int(job["est_bytes"]),
                    "returncode": rc,
                    "message": msg,
                    "elapsed_s": elapsed,
                    "ok": ok,
                    "soft_ok": rc != 0 and ok,
                })
                if ok and on_page_pdf_created is not None:
                    try:
                        on_page_pdf_created(job["pdf_path"])
                    except Exception:
                        pass

        total_elapsed = time.perf_counter() - started
        failed = [r for r in results if not r["ok"]]
        if failed:
            first = failed[0]
            err = f"Inkscape PDF export failed for SVG chunk {first['index']}"
            _l.w("[export.pdf] failed %s", err)
            return False, {
                "error": err,
                "elapsed_s": total_elapsed,
                "results": results,
                "page_count": page_count,
                "chunk_count": len(chunks),
                "fixed_images": fixed_images,
                "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
                "target_chunk_bytes": int(chunk_plan.get("target_chunk_bytes") or DEFAULT_SVG_CHUNK_TARGET_BYTES),
            }

        ordered_chunk_pdfs = [
            item["pdf_path"]
            for item in sorted(results, key=lambda row: (min(row["pages"] or [10**9]), row["index"]))
        ]
        def _merge_profile(profile: str):
            target_pdf = _resolve_profile_output_pdf_path(pdf_path, profile)
            _l.i(
                "[export.pdf] merge profile='%s' inputs=%d target='%s'",
                profile,
                len(ordered_chunk_pdfs),
                target_pdf,
            )
            res = GS.merge_pdfs(
                ordered_chunk_pdfs,
                target_pdf,
                detect_duplicate_images=True,
                pdf_settings=None if profile == "default" else profile,
            )
            return profile, res

        gs_results = []
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
                "target_chunk_bytes": int(chunk_plan.get("target_chunk_bytes") or DEFAULT_SVG_CHUNK_TARGET_BYTES),
            }
        return True, {
            "elapsed_s": total_elapsed,
            "results": results,
            "page_count": page_count,
            "chunk_count": len(chunks),
            "pdf_path": pdf_path,
            "fixed_images": fixed_images,
            "chunk_dir": str(chunk_plan.get("chunk_dir") or ""),
            "used_parallel": len(chunks) > 1,
            "target_chunk_bytes": int(chunk_plan.get("target_chunk_bytes") or DEFAULT_SVG_CHUNK_TARGET_BYTES),
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
        }


def _export_png_pages_via_inkscape(
    svg_path: str,
    png_path: str,
    *,
    chunk_count: int = 3,
    on_page_png_created=None,
) -> tuple[bool, dict]:
    _l.i("[export.png] start svg='%s' png='%s'", svg_path, png_path)
    existing_chunk_plan = None
    if not svg_path or not os.path.isfile(svg_path):
        existing_chunk_plan = _chunk_plan_from_existing_output(svg_path, os.path.splitext(png_path)[0] + ".pdf") if svg_path else None
        if existing_chunk_plan is None:
            return False, {"error": f"SVG output not found: {svg_path}"}
    exe = INKSCAPE.find_executable()
    if not exe:
        return False, {"error": "Inkscape executable not found"}
    page_count = _svg_page_count(svg_path) if (svg_path and os.path.isfile(svg_path)) else 0
    expected_pngs = _all_output_page_png_paths(png_path, page_count)
    _cleanup_png_outputs(png_path, page_count)
    started = time.perf_counter()

    try:
        env = INKSCAPE.clean_launch_env()
        exe_dir = os.path.dirname(exe) or None
        from concurrent.futures import ThreadPoolExecutor

        chunk_plan = existing_chunk_plan or SVGCHUNKS.write_svg_chunks(
            svg_path,
            os.path.splitext(png_path)[0] + ".pdf",
            inkscape_exe=exe,
            target_chunk_bytes=DEFAULT_SVG_CHUNK_TARGET_BYTES,
        )
        chunks = list(chunk_plan.get("chunks") or [])
        if page_count <= 0:
            page_count = sum(len(getattr(chunk, "pages", ()) or ()) for chunk in chunks)
            expected_pngs = _all_output_page_png_paths(png_path, page_count)
        fixed_images = int(chunk_plan.get("fixed_images") or 0)
        _l.i(
            "[export.png] chunks_ready count=%d fixed_images=%d chunk_dir='%s' existing=%s",
            len(chunks),
            fixed_images,
            str(chunk_plan.get("chunk_dir") or ""),
            "yes" if bool(chunk_plan.get("from_existing_chunks")) else "no",
        )
        jobs = []
        for chunk in chunks:
            local_expected = _all_output_page_png_paths(chunk.png_prefix, len(chunk.pages))
            local_page_exports = [(idx, path) for idx, path in enumerate(local_expected, start=1)]
            commands = INKSCAPE.build_shell_png_page_commands(chunk.svg_path, local_page_exports, dpi=300)
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
                ok = _paths_exist_with_size(job["expected_pngs"])
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
                        final_path = _resolve_output_page_png_path(png_path, page_no) if page_count > 1 else _normalize_path(png_path)
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
            }
        return True, {
            "elapsed_s": total_elapsed,
            "results": results,
            "page_count": page_count,
            "chunk_count": len(chunks),
            "png_path": png_path,
            "png_outputs": expected_pngs,
            "fixed_images": fixed_images,
            "used_parallel": len(chunks) > 1,
        }
    except Exception as ex:
        _l.w("[export.png] exception %s", str(ex))
        return False, {
            "error": str(ex),
            "fixed_images": 0,
        }


def _send_request(req: AppRequest, timeout: float = 0.35) -> bool:
    payload = {
        "cmd": "open",
        "template": req.template,
        "sheet_id": req.sheet_id,
        "sheet_range": req.sheet_range,
        "log_level": req.log_level,
    }
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as s:
            s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            s.settimeout(timeout)
            data = s.recv(32)
        return data.strip() == b"OK"
    except Exception:
        return False


def _candidate_python_launchers() -> list[str]:
    out: list[str] = []
    exe = str(sys.executable or "").strip()
    if exe:
        out.append(exe)
        base = os.path.dirname(exe)
        if base:
            out.append(os.path.join(base, "pythonw.exe"))
            out.append(os.path.join(base, "python.exe"))
    # Common Inkscape portable layout used by this project.
    out.append(os.path.expandvars(r"%USERPROFILE%\inkscape\bin\pythonw.exe"))
    out.append(os.path.expandvars(r"%USERPROFILE%\inkscape\bin\python.exe"))

    seen = set()
    good = []
    for p in out:
        pp = os.path.normpath(p)
        key = pp.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if os.path.isfile(pp) and os.path.getsize(pp) > 0:
                good.append(pp)
        except Exception:
            continue
    return good


def notify_or_launch(template: str, sheet_id: str = "", sheet_range: str = "", log_level: str = "global") -> bool:
    req = AppRequest(
        template=_normalize_path(template),
        sheet_id=str(sheet_id or "").strip(),
        sheet_range=str(sheet_range or "").strip(),
        log_level=str(log_level or "global").strip() or "global",
    )
    if _send_request(req):
        _l.i(f"[deckmaker_app] notified resident app template='{req.template}'")
        return True

    script = os.path.abspath(__file__)
    args_tail = [
        script,
        "--template", req.template,
        "--sheet-id", req.sheet_id,
        "--sheet-range", req.sheet_range,
        "--log-level", req.log_level,
    ]
    env = os.environ.copy()
    env[ENV_DIRECT_RUN] = "1"

    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

    last_error = None
    for py in _candidate_python_launchers():
        try:
            proc = subprocess.Popen(
                [py] + args_tail,
                cwd=os.path.dirname(script),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
            # Avoid ResourceWarning in Inkscape's extension runner; we intentionally detach.
            proc.returncode = 0
            _l.i(f"[deckmaker_app] launched resident app python='{py}' template='{req.template}'")
            return True
        except Exception as ex:
            last_error = ex
            continue
    _l.w(f"[deckmaker_app] launch failed: {last_error}")
    return False


class _EngineEffect:
    def __init__(self, template: str, sheet_id: str, sheet_range: str, log_level: str):
        import inkex

        self._template = _normalize_path(template)
        with open(self._template, "rb") as fh:
            raw = fh.read()
        self.document = inkex.load_svg(raw)
        self.svg = self.document.getroot()
        self.options = SimpleNamespace(
            tab="data",
            csv_path="",
            sheet_id=str(sheet_id or "").strip(),
            sheet_range=str(sheet_range or "").strip(),
            prototypes_layer="Prototypes",
            preset="{A4}",
            stop_on_error=False,
            log_level=str(log_level or "global").strip() or "global",
        )

    def document_path(self) -> str:
        return self._template

    def _document_path_or_abort(self) -> str:
        import inkex

        if not self._template or not os.path.isfile(self._template):
            raise inkex.AbortExtension("Save the SVG template before running DeckMaker.")
        return self._template

    def _find_or_create_layer(self, root, label: str):
        import inkex

        for child in list(root):
            try:
                if not (hasattr(child, "tag") and isinstance(child.tag, str) and child.tag.endswith("g")):
                    continue
                if child.get(inkex.addNS("groupmode", "inkscape")) == "layer":
                    if (child.get(inkex.addNS("label", "inkscape")) or "") == label:
                        return child
            except Exception:
                continue
        layer = inkex.Group()
        layer.set(inkex.addNS("groupmode", "inkscape"), "layer")
        layer.set(inkex.addNS("label", "inkscape"), label)
        root.append(layer)
        return layer


class DeckMakerApp:
    def __init__(self, initial: Optional[AppRequest] = None):
        import tkinter as tk
        import tkinter.font as tkfont
        from tkinter import scrolledtext
        from tkinter import ttk

        self.tk = tk
        self.tkfont = tkfont
        self.scrolledtext = scrolledtext
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("PnPInk DeckMaker")
        self.root.geometry("720x430")
        self.root.minsize(600, 360)
        self._icon_image = None
        self._apply_window_icon()

        self._queue: "queue.Queue[AppRequest]" = queue.Queue()
        self._server_stop = threading.Event()
        self._render_thread: Optional[threading.Thread] = None
        self._activity_stop = threading.Event()
        self._activity_thread: Optional[threading.Thread] = None

        self.template_var = tk.StringVar(value=(initial.template if initial else ""))
        self.sheet_id_var = tk.StringVar(value=(initial.sheet_id if initial else ""))
        self.sheet_range_var = tk.StringVar(value=(initial.sheet_range if initial else ""))
        self.status_var = tk.StringVar(value="Ready")
        self.auto_create_var = tk.BooleanVar(value=prefs.get_auto_create())
        self.auto_open_var = tk.BooleanVar(value=prefs.get_auto_open())
        self.auto_export_var = tk.BooleanVar(value=prefs.get_auto_export())
        self.export_pdf_var = tk.BooleanVar(value=prefs.get_export_pdf())
        self.export_png_var = tk.BooleanVar(value=prefs.get_export_png())
        self.pdf_raster_filters_var = tk.BooleanVar(value=prefs.get_pdf_raster_filters())
        self.split_svg_output_var = tk.BooleanVar(value=prefs.get_split_svg_output())
        self.split_svg_chunk_mb_var = tk.StringVar(value=str(prefs.get_split_svg_chunk_mb()))
        self.pdf_profile_vars = {
            "default": tk.BooleanVar(value=False),
            "prepress": tk.BooleanVar(value=False),
        }
        self._warm_sheet_id = ""
        self._request_serial = 0
        self._autorun_serial = 0
        self._run_started_at: float | None = None
        self._post_create_busy = False
        self._web_activity_stats = {
            "wkmc_fetched": 0,
            "web_cached": 0,
            "wkmc_failed": 0,
            "raster_jobs": 0,
            "raster_chunks": 0,
        }

        self._build_ui()
        self._load_pdf_profile_prefs()
        if initial:
            self._set_request(initial)
        self._start_server()
        self.root.after(150, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.sheet_id_var.trace_add("write", lambda *_: self._schedule_auth_warmup())

    def _build_ui(self):
        tk = self.tk
        tkfont = self.tkfont
        scrolledtext = self.scrolledtext
        ttk = self.ttk

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        style = ttk.Style()
        try:
            style.configure("Thin.Horizontal.TProgressbar", thickness=6)
        except Exception:
            pass

        notebook = ttk.Notebook(frame)
        notebook.grid(row=0, column=0, sticky="nsew")

        deck_tab = ttk.Frame(notebook, padding=10)
        deck_tab.columnconfigure(1, weight=1)
        deck_tab.rowconfigure(4, weight=1)
        notebook.add(deck_tab, text="Deck")

        prefs_tab = ttk.Frame(notebook, padding=10)
        prefs_tab.columnconfigure(1, weight=1)
        notebook.add(prefs_tab, text="Preferences")

        about_tab = ttk.Frame(notebook, padding=10)
        about_tab.columnconfigure(0, weight=1)
        notebook.add(about_tab, text="About")

        ttk.Label(deck_tab, text="Template SVG").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(deck_tab, textvariable=self.template_var, state="readonly").grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(deck_tab, text="GSheet ID").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(deck_tab, textvariable=self.sheet_id_var).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(deck_tab, text="Range / gid").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(deck_tab, textvariable=self.sheet_range_var).grid(row=2, column=1, sticky="ew", pady=4)

        buttons = ttk.Frame(deck_tab)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        buttons.columnconfigure(3, weight=1)
        self.run_btn = ttk.Button(buttons, text="Create", command=self._run_clicked)
        self.run_btn.grid(row=0, column=0, sticky="w")
        self.open_btn = ttk.Button(buttons, text="Open", command=self._open_output_clicked)
        self.open_btn.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.pdf_btn = ttk.Button(buttons, text="Export", command=self._export_clicked)
        self.pdf_btn.grid(row=0, column=2, sticky="w", padx=(8, 0))
        ttk.Label(buttons, textvariable=self.status_var).grid(row=0, column=3, sticky="w", padx=(12, 0))

        log_font = tkfont.nametofont("TkFixedFont").copy()
        try:
            log_font.configure(size=max(8, int(log_font.cget("size")) - 1))
        except Exception:
            pass

        self.log_text = scrolledtext.ScrolledText(deck_tab, height=9, wrap="word", state="disabled", font=log_font)
        self.log_text.grid(row=4, column=0, columnspan=2, sticky="nsew", pady=(10, 0))

        format_box = ttk.LabelFrame(prefs_tab, text="Output Formats", padding=8)
        format_box.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Checkbutton(format_box, text="PDF", variable=self.export_pdf_var, command=self._on_export_format_prefs_changed).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Checkbutton(format_box, text="PNG pages", variable=self.export_png_var, command=self._on_export_format_prefs_changed).grid(row=0, column=1, sticky="w")

        ttk.Label(prefs_tab, text="PDF Output Profiles").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(0, 6))
        checks = ttk.Frame(prefs_tab)
        checks.grid(row=2, column=0, columnspan=2, sticky="w")
        profile_labels = [
            ("default", "High Quality"),
            ("prepress", "Compact"),
        ]
        for idx, (key, label) in enumerate(profile_labels):
            cb = ttk.Checkbutton(
                checks,
                text=label,
                variable=self.pdf_profile_vars[key],
                command=self._on_pdf_profiles_changed,
            )
            cb.grid(row=idx // 3, column=idx % 3, sticky="w", padx=(0, 12), pady=(0, 4))

        ttk.Checkbutton(
            prefs_tab,
            text="Raster filters",
            variable=self.pdf_raster_filters_var,
            command=self._on_pdf_export_prefs_changed,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        split_box = ttk.LabelFrame(prefs_tab, text="SVG Output", padding=8)
        split_box.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        split_box.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            split_box,
            text="Split SVG",
            variable=self.split_svg_output_var,
            command=self._on_svg_chunk_prefs_changed,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(split_box, text="Chunk MB").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        chunk_spin = ttk.Spinbox(
            split_box,
            from_=1,
            to=2048,
            increment=1,
            textvariable=self.split_svg_chunk_mb_var,
            width=8,
            command=self._on_svg_chunk_prefs_changed,
        )
        chunk_spin.grid(row=1, column=1, sticky="w", pady=(8, 0))
        chunk_spin.bind("<FocusOut>", lambda _e: self._on_svg_chunk_prefs_changed())
        chunk_spin.bind("<Return>", lambda _e: self._on_svg_chunk_prefs_changed())

        ttk.Label(
            prefs_tab,
            text="All selected PDF profiles are generated after the page PDFs are ready.",
            wraplength=420,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        auto_box = ttk.LabelFrame(prefs_tab, text="Automation", padding=8)
        auto_box.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Checkbutton(auto_box, text="Auto Create", variable=self.auto_create_var, command=self._on_auto_prefs_changed).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(auto_box, text="Auto Open", variable=self.auto_open_var, command=self._on_auto_prefs_changed).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(auto_box, text="Auto Export", variable=self.auto_export_var, command=self._on_auto_prefs_changed).grid(row=2, column=0, sticky="w")

        about_text = (
            "PnPInk DeckMaker\n\n"
            "Resident app for rendering SVG output and exporting final files.\n"
            "Preferences in this window are stored locally for future runs.\n"
            "Output options will continue to grow here."
        )
        ttk.Label(about_tab, text=about_text, justify="left", anchor="nw").grid(row=0, column=0, sticky="nw")

        progress_wrap = tk.Frame(frame, height=6, bd=0, highlightthickness=0)
        progress_wrap.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        progress_wrap.grid_propagate(False)
        progress_wrap.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_wrap, mode="indeterminate", style="Thin.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=0, sticky="ew")

    def _apply_window_icon(self):
        icon_path = _app_icon_path()
        if not os.path.isfile(icon_path):
            return
        try:
            self._icon_image = self.tk.PhotoImage(file=icon_path)
            self.root.iconphoto(True, self._icon_image)
        except Exception:
            self._icon_image = None

    def _log(self, message: str):
        text = str(message or "").strip()
        if not text:
            return
        stamp = time.strftime("%H:%M:%S")
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{stamp}] {text}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _start_web_activity_monitor(self):
        self._stop_web_activity_monitor()
        self._activity_stop = threading.Event()
        self._web_activity_stats = {
            "wkmc_fetched": 0,
            "web_cached": 0,
            "wkmc_failed": 0,
            "raster_jobs": 0,
            "raster_chunks": 0,
        }
        log_path = _app_log_path()

        def _emit(msg: str):
            try:
                self.root.after(0, lambda msg=msg: self._log(msg))
            except Exception:
                pass

        def worker():
            try:
                pos = 0
                try:
                    if os.path.isfile(log_path):
                        pos = os.path.getsize(log_path)
                except Exception:
                    pos = 0
                while not self._activity_stop.is_set():
                    if not os.path.isfile(log_path):
                        time.sleep(0.25)
                        continue
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                            fh.seek(pos)
                            chunk = fh.read()
                            pos = fh.tell()
                    except Exception:
                        time.sleep(0.25)
                        continue
                    if not chunk:
                        time.sleep(0.25)
                        continue
                    for raw_line in chunk.splitlines():
                        line = str(raw_line or "").strip()
                        if not line:
                            continue
                        if "[sources] wkmc batch prefetched" in line:
                            msg = line.split("[sources] wkmc batch prefetched", 1)[-1].strip()
                            _emit(f"Wikimedia batch prefetch {msg}")
                            continue
                        if "[sources] virtual prefetch scheduled:" in line:
                            m = re.search(r"virtual prefetch scheduled:\s*(\d+)\s*expr", line)
                            if m:
                                _emit(f"Preparing {m.group(1)} web source expression(s)...")
                            continue
                        if "[sources] web prefetch scheduled:" in line:
                            m = re.search(r"web prefetch scheduled:\s*(\d+)\s*url", line)
                            if m:
                                _emit(f"Preparing {m.group(1)} direct web download(s)...")
                            continue
                        if "[sources] wkmc fetched query=" in line:
                            self._web_activity_stats["wkmc_fetched"] += 1
                            n = int(self._web_activity_stats["wkmc_fetched"])
                            if n <= 3 or (n % 10) == 0:
                                _emit(f"Wikimedia resolved {n} item(s)...")
                            continue
                        if "[sources] web cached ->" in line:
                            self._web_activity_stats["web_cached"] += 1
                            n = int(self._web_activity_stats["web_cached"])
                            if n <= 3 or (n % 5) == 0:
                                _emit(f"Downloaded {n} web asset(s)...")
                            continue
                        if "[sources] wkmc fetch failed" in line:
                            self._web_activity_stats["wkmc_failed"] += 1
                            n = int(self._web_activity_stats["wkmc_failed"])
                            if n <= 3 or (n % 5) == 0:
                                _emit(f"Wikimedia retries/failures: {n}")
                            continue
                        if "[raster] export-id png jobs=" in line:
                            m = re.search(r"export-id png jobs=%d ids=%s\s+(\d+)", line)
                            if not m:
                                m = re.search(r"export-id png jobs=\s*(\d+)", line)
                            if m:
                                jobs = int(m.group(1))
                                self._web_activity_stats["raster_jobs"] += jobs
                                if jobs > 0:
                                    _emit(f"Rasterizing filtered nodes: {jobs} PNG job(s)...")
                            continue
                        if "[export.pdf] chunk_raster" in line:
                            self._web_activity_stats["raster_chunks"] += 1
                            m = re.search(r"rasterized_filters=%d.*?\s(\d+)\s", line)
                            if not m:
                                m = re.search(r"rasterized_filters=\s*(\d+)", line)
                            if m:
                                _emit(
                                    f"Raster chunk {self._web_activity_stats['raster_chunks']} done: "
                                    f"{int(m.group(1))} filter node(s)"
                                )
                            else:
                                _emit(f"Raster chunk {self._web_activity_stats['raster_chunks']} done")
                            continue
            except Exception:
                pass

        self._activity_thread = threading.Thread(target=worker, name="pnpink-web-activity", daemon=True)
        self._activity_thread.start()

    def _stop_web_activity_monitor(self):
        try:
            self._activity_stop.set()
        except Exception:
            pass

    def _load_pdf_profile_prefs(self):
        selected = set(prefs.get_pdf_profiles())
        prefs.set_pdf_profiles([key for key in ("default", "prepress") if key in selected])
        for key, var in self.pdf_profile_vars.items():
            var.set(key in selected)
        if not any(var.get() for var in self.pdf_profile_vars.values()):
            self.pdf_profile_vars["default"].set(True)

    def _selected_pdf_profiles(self) -> list[str]:
        out = [key for key, var in self.pdf_profile_vars.items() if bool(var.get())]
        return out or ["default"]

    def _on_pdf_profiles_changed(self):
        selected = self._selected_pdf_profiles()
        prefs.set_pdf_profiles(selected)
        self._log(f"Preference saved: PDF profiles = {', '.join(selected)}")

    def _on_pdf_export_prefs_changed(self):
        prefs.set_pdf_raster_filters(bool(self.pdf_raster_filters_var.get()))
        self._log(
            "Preference saved: PDF raster filters = "
            f"{'on' if self.pdf_raster_filters_var.get() else 'off'}"
        )

    def _on_svg_chunk_prefs_changed(self):
        prefs.set_split_svg_output(bool(self.split_svg_output_var.get()))
        try:
            chunk_mb = int(str(self.split_svg_chunk_mb_var.get() or "").strip())
        except Exception:
            chunk_mb = prefs.get_split_svg_chunk_mb()
        chunk_mb = max(1, min(chunk_mb, 2048))
        self.split_svg_chunk_mb_var.set(str(chunk_mb))
        prefs.set_split_svg_chunk_mb(chunk_mb)
        self._log(
            "Preference saved: split SVG = "
            f"{'on' if self.split_svg_output_var.get() else 'off'}, "
            f"chunk_mb={chunk_mb}"
        )

    def _selected_export_formats(self) -> list[str]:
        out: list[str] = []
        if bool(self.export_pdf_var.get()):
            out.append("pdf")
        if bool(self.export_png_var.get()):
            out.append("png")
        return out or ["pdf"]

    def _export_options_snapshot(self) -> ExportOptions:
        return ExportOptions(
            formats=tuple(self._selected_export_formats()),
            pdf_profiles=tuple(self._selected_pdf_profiles()),
            pdf_raster_filters=bool(self.pdf_raster_filters_var.get()),
        )

    def _on_export_format_prefs_changed(self):
        if not bool(self.export_pdf_var.get()) and not bool(self.export_png_var.get()):
            self.export_pdf_var.set(True)
        prefs.set_export_pdf(bool(self.export_pdf_var.get()))
        prefs.set_export_png(bool(self.export_png_var.get()))
        labels = []
        if self.export_pdf_var.get():
            labels.append("PDF")
        if self.export_png_var.get():
            labels.append("PNG pages")
        self._log(f"Preference saved: output formats = {', '.join(labels)}")

    def _on_auto_prefs_changed(self):
        prefs.set_auto_create(bool(self.auto_create_var.get()))
        prefs.set_auto_open(bool(self.auto_open_var.get()))
        prefs.set_auto_export(bool(self.auto_export_var.get()))
        self._log(
            "Preference saved: automation = "
            f"create={'on' if self.auto_create_var.get() else 'off'}, "
            f"open={'on' if self.auto_open_var.get() else 'off'}, "
            f"export={'on' if self.auto_export_var.get() else 'off'}"
        )

    def _log_image_dpi_preflight(self, svg_path_or_report):
        report = svg_path_or_report if isinstance(svg_path_or_report, dict) else _effective_image_dpi_report(str(svg_path_or_report))
        if not report.get("ok"):
            self._log(f"Image preflight failed: {report.get('error')}")
            return
        rows = list(report.get("rows") or [])
        if not rows:
            self._log("Image preflight: no linked bitmap images found")
            return
        dpis = [float(r["dpi"]) for r in rows]
        low = list(report.get("low") or [])
        high = list(report.get("high") or [])
        self._log(
            f"Image preflight: {len(rows)} bitmap(s), effective DPI "
            f"min={min(dpis):.0f}, median={dpis[len(dpis)//2]:.0f}, max={max(dpis):.0f}"
        )
        if low:
            self._log(f"Image preflight warning: {len(low)} image(s) below 150 dpi")
            for item in low[:5]:
                mm = item["placed_mm"]
                px = item["px"]
                self._log(
                    f"  low dpi {item['dpi']:.0f}: {item['file']} "
                    f"({px[0]}x{px[1]} px at {mm[0]:.1f}x{mm[1]:.1f} mm)"
                )
        if high:
            self._log(f"Image preflight note: {len(high)} image(s) above 900 dpi")
            for item in high[-5:]:
                mm = item["placed_mm"]
                px = item["px"]
                self._log(
                    f"  high dpi {item['dpi']:.0f}: {item['file']} "
                    f"({px[0]}x{px[1]} px at {mm[0]:.1f}x{mm[1]:.1f} mm)"
                )
        unresolved = int(report.get("unresolved") or 0)
        unreadable = int(report.get("unreadable") or 0)
        if unresolved or unreadable:
            self._log(f"Image preflight skipped: unresolved={unresolved}, unreadable={unreadable}")

    def _output_svg_path(self) -> str:
        return _resolve_output_svg_path(self.template_var.get())

    def _output_pdf_path(self) -> str:
        return _resolve_output_pdf_path(self.template_var.get())

    def _open_path_in_system(self, path: str) -> None:
        target = _normalize_path(path)
        if not target or not os.path.exists(target):
            raise FileNotFoundError(target or "missing path")
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
            return
        opener = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
        kwargs = {"args": opener, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.Popen(**kwargs)

    def _open_output_clicked(self) -> None:
        svg_path = self._output_svg_path()
        if not svg_path or not os.path.isfile(svg_path):
            self.status_var.set("Create output first")
            self._log("Create output first")
            return
        try:
            self._open_path_in_system(svg_path)
            self.status_var.set("Opened output")
            self._log(f"Opened output: {os.path.basename(svg_path)}")
        except Exception as ex:
            self.status_var.set("Open failed")
            self._log(f"Open failed: {ex}")

    def _set_request(self, req: AppRequest):
        self._request_serial += 1
        serial = self._request_serial
        self.template_var.set(_normalize_path(req.template))
        if req.sheet_id:
            self.sheet_id_var.set(req.sheet_id)
        if req.sheet_range:
            self.sheet_range_var.set(req.sheet_range)
        self.status_var.set("Template received")
        self._log(f"Template: {os.path.basename(_normalize_path(req.template))}")
        if req.sheet_id:
            detail = f" range={req.sheet_range}" if req.sheet_range else ""
            self._log(f"Google Sheets source ready{detail}")
        self._schedule_auth_warmup()
        try:
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        self.root.after(120, lambda: self._autorun(serial))

    def _schedule_auth_warmup(self):
        sheet_id = self.sheet_id_var.get().strip()
        if not sheet_id or sheet_id == self._warm_sheet_id:
            return
        self._warm_sheet_id = sheet_id
        self.root.after(600, self._auth_warmup)

    def _auth_warmup(self):
        sheet_id = self.sheet_id_var.get().strip()
        if not sheet_id or sheet_id != self._warm_sheet_id:
            return

        def worker():
            try:
                import gsheets_client_pkce as GS

                self.root.after(0, lambda: self._log("Checking Google Sheets session..."))
                ok = GS.warm_session()
                if ok:
                    self.root.after(0, lambda: self.status_var.set("Google Sheets session ready"))
                    self.root.after(0, lambda: self._log("Google Sheets session ready"))
            except Exception:
                pass

        threading.Thread(target=worker, name="pnpink-gsheets-auth-warmup", daemon=True).start()

    def _start_server(self):
        def serve():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((HOST, PORT))
            srv.listen(5)
            srv.settimeout(0.4)
            try:
                while not self._server_stop.is_set():
                    try:
                        conn, _addr = srv.accept()
                    except socket.timeout:
                        continue
                    with conn:
                        data = b""
                        while b"\n" not in data:
                            chunk = conn.recv(8192)
                            if not chunk:
                                break
                            data += chunk
                        try:
                            msg = json.loads(data.decode("utf-8").strip() or "{}")
                            if msg.get("cmd") == "open":
                                self._queue.put(AppRequest(
                                    template=_normalize_path(msg.get("template") or ""),
                                    sheet_id=str(msg.get("sheet_id") or "").strip(),
                                    sheet_range=str(msg.get("sheet_range") or "").strip(),
                                    log_level=str(msg.get("log_level") or "global").strip() or "global",
                                ))
                                conn.sendall(b"OK\n")
                            else:
                                conn.sendall(b"ERR\n")
                        except Exception:
                            conn.sendall(b"ERR\n")
            finally:
                srv.close()

        threading.Thread(target=serve, name="pnpink-deckmaker-app-server", daemon=True).start()

    def _drain_queue(self):
        try:
            while True:
                self._set_request(self._queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self._drain_queue)

    def _autorun(self, serial: int):
        if serial <= self._autorun_serial:
            return
        self._autorun_serial = serial
        if not bool(self.auto_create_var.get()):
            return
        if self._render_thread and self._render_thread.is_alive():
            return
        if not _normalize_path(self.template_var.get()) or not os.path.isfile(_normalize_path(self.template_var.get())):
            return
        self._run_clicked(autorun=True)

    def _run_clicked(self, autorun: bool = False):
        if self._render_thread and self._render_thread.is_alive():
            return
        template = _normalize_path(self.template_var.get())
        if not template or not os.path.isfile(template):
            self.status_var.set("Save/open a template SVG first")
            self._log("Save/open a template SVG first")
            return
        req = AppRequest(
            template=template,
            sheet_id=self.sheet_id_var.get().strip(),
            sheet_range=self.sheet_range_var.get().strip(),
        )
        _l.i(f"[deckmaker_app] run clicked template='{req.template}' sheet_id={'yes' if req.sheet_id else 'no'} range='{req.sheet_range}'")
        self._run_started_at = time.perf_counter()
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("Creating...")
        self._log(("Auto create" if autorun else "Create") + " started")
        self._render_thread = threading.Thread(target=self._render_worker, args=(req,), daemon=True)
        self._render_thread.start()

    def _render_worker(self, req: AppRequest):
        try:
            import dataset_state as DSTATE
            import engine as ENG
            from deckmaker import __version__ as deckmaker_version

            self._start_web_activity_monitor()
            self.root.after(0, lambda: self._log("Loading template and dataset..."))
            effect = _EngineEffect(req.template, req.sheet_id, req.sheet_range, req.log_level)
            self.root.after(0, lambda: self._log("Rendering output SVG..."))
            ENG.run(effect, deckmaker_version)
            try:
                access_mode = str(getattr(effect.options, "_dataset_access_mode", "") or "").strip().lower()
                if req.sheet_id:
                    DSTATE.set_gsheet_for_svg(req.template, req.sheet_id, req.sheet_range, access_mode)
            except Exception:
                _l.w("[deckmaker_app] dataset state save failed\n" + traceback.format_exc())
            dpi_report = _effective_image_dpi_report(_resolve_output_svg_path(req.template))
            self.root.after(0, lambda dpi_report=dpi_report: self._log_image_dpi_preflight(dpi_report))
            elapsed = (time.perf_counter() - self._run_started_at) if self._run_started_at else 0.0
            self.root.after(0, lambda: self._render_done(f"Done ({elapsed:.2f}s)"))
            self.root.after(0, self._after_create_success)
        except Exception as ex:
            _l.w("[deckmaker_app] render failed:\n" + traceback.format_exc())
            self.root.after(0, lambda: self._render_done(f"Error: {ex}"))
        finally:
            self._stop_web_activity_monitor()

    def _render_done(self, status: str):
        self.progress.stop()
        self.run_btn.configure(state="normal")
        self.open_btn.configure(state="normal")
        self.pdf_btn.configure(state="normal")
        self.status_var.set(status)
        self._log(status)

    def _after_create_success(self):
        if self._post_create_busy:
            return
        self._post_create_busy = True
        auto_open = bool(self.auto_open_var.get())
        auto_export = bool(self.auto_export_var.get())
        output_svg_path = self._output_svg_path()
        template = self.template_var.get()
        export_options = self._export_options_snapshot()

        def worker():
            try:
                if auto_open:
                    try:
                        self._open_path_in_system(output_svg_path)
                        self.root.after(0, lambda: self._log("Opened output automatically"))
                    except Exception as ex:
                        self.root.after(0, lambda ex=ex: self._log(f"Auto open failed: {ex}"))
                if auto_export:
                    self.root.after(0, lambda: self._begin_export_ui(export_options, auto=True))
                    self._export_worker(template, export_options)
            finally:
                self._post_create_busy = False

        threading.Thread(target=worker, name="pnpink-post-create", daemon=True).start()

    def _begin_export_ui(self, options: ExportOptions, *, auto: bool = False):
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.pdf_btn.configure(state="disabled")
        self.progress.start(10)
        formats = list(options.formats)
        label = ", ".join(fmt.upper() if fmt == "pdf" else "PNG" for fmt in formats)
        self.status_var.set(f"Exporting {label}...")
        self._log(("Auto export" if auto else "Export") + f" started: {label}")
        _l.i(
            "[export.ui] start auto=%s formats=%s profiles=%s",
            "yes" if auto else "no",
            ",".join(formats),
            ",".join(options.pdf_profiles or ("default",)),
        )
        self._log(
            "Export options: "
            f"profiles={','.join(options.pdf_profiles or ('default',))}"
        )

    def _export_clicked(self):
        if self._render_thread and self._render_thread.is_alive():
            self._log("Wait for render to finish before exporting")
            return
        template = _normalize_path(self.template_var.get())
        if not template or not os.path.isfile(template):
            self.status_var.set("Save/open a template SVG first")
            self._log("Save/open a template SVG first")
            return
        options = self._export_options_snapshot()
        self._begin_export_ui(options)
        threading.Thread(target=self._export_worker, args=(template, options), daemon=True).start()

    def _export_worker(self, template: str, options: ExportOptions):
        try:
            self._start_web_activity_monitor()
            svg_path = _resolve_output_svg_path(template)
            formats = list(options.formats)
            started = time.perf_counter()
            _l.i(
                "[export.worker] start template='%s' svg='%s' formats=%s",
                template,
                svg_path,
                ",".join(formats),
            )
            self.root.after(0, lambda: self._log(f"Export source: {os.path.basename(svg_path)}"))
            source_info = _resolve_chunked_output_source(svg_path)
            prepared_svg_path = svg_path
            prepared_info = {}
            if source_info.get("svg_path"):
                try:
                    prepared_svg_path, prepared_info = _prepare_export_svg(svg_path)
                    if not prepared_svg_path:
                        prepared_svg_path = svg_path
                        prepared_info = {}
                except Exception:
                    prepared_info = {}
            elif source_info.get("chunk_paths"):
                prepared_info = {"chunk_dir": str(source_info.get("chunk_dir") or ""), "chunk_count": len(source_info.get("chunk_paths") or [])}
            fixed_images_prepared = int((prepared_info or {}).get("fixed_images") or 0) if isinstance(prepared_info, dict) else 0
            if fixed_images_prepared > 0:
                self.root.after(0, lambda fixed_images_prepared=fixed_images_prepared: self._log(
                    f"Export temp SVG rewrote {fixed_images_prepared} linked image(s) to file://"
                ))
            elif source_info.get("chunk_paths"):
                self.root.after(0, lambda count=len(source_info.get("chunk_paths") or []): self._log(
                    f"Using existing chunked SVG output ({count} chunk(s))"
                ))

            failures: list[str] = []
            try:
                if "pdf" in formats:
                    pdf_path = _resolve_output_pdf_path(template)
                    selected_profiles = list(options.pdf_profiles or ("default",))
                    self.root.after(0, lambda: self._log(f"PDF profiles: {', '.join(selected_profiles)}"))
                    self.root.after(
                        0,
                        lambda enabled=bool(options.pdf_raster_filters): self._log(
                            "PDF filter mode: raster chunk filters" if enabled else "PDF filter mode: ignore filters"
                        ),
                    )

                    def _page_pdf_created(path: str):
                        self.root.after(0, lambda path=path: self._log(f"Created chunk PDF: {os.path.basename(path)}"))

                    ok, info = _export_pdf_via_inkscape(
                        prepared_svg_path,
                        pdf_path,
                        pdf_profiles=selected_profiles,
                        rasterize_filters=bool(options.pdf_raster_filters),
                        on_page_pdf_created=_page_pdf_created,
                    )
                    chunk_dir = str((info or {}).get("chunk_dir") or "") if isinstance(info, dict) else ""
                    if chunk_dir:
                        self.root.after(0, lambda chunk_dir=chunk_dir: self._log(f"PDF chunk dir: {chunk_dir}"))
                    if ok:
                        elapsed = float((info or {}).get("elapsed_s") or 0.0)
                        page_count = int((info or {}).get("page_count") or 0)
                        used_chunks = int((info or {}).get("chunk_count") or 1)
                        self.root.after(0, lambda elapsed=elapsed, page_count=page_count, used_chunks=used_chunks: self._log(
                            f"PDF export done in {elapsed:.2f}s across {page_count} page(s) using {used_chunks} SVG chunk(s)"
                        ))
                        for item in list((info or {}).get("gs_outputs") or []):
                            profile = str(item.get("profile") or "default")
                            output_pdf = str(item.get("output_pdf") or "")
                            if output_pdf:
                                self.root.after(0, lambda profile=profile, output_pdf=output_pdf: self._log(
                                    f"PDF profile {profile} -> {os.path.basename(output_pdf)}"
                                ))
                    else:
                        failures.append("PDF")
                        err = str((info or {}).get("error") or "PDF export failed")
                        elapsed = float((info or {}).get("elapsed_s") or 0.0) if isinstance(info, dict) else 0.0
                        if elapsed > 0:
                            self.root.after(0, lambda elapsed=elapsed: self._log(f"PDF export failed after {elapsed:.2f}s"))
                        self.root.after(0, lambda err=err: self._log(err))

                if "png" in formats:
                    png_path = _resolve_output_png_path(template)

                    def _page_png_created(path: str):
                        self.root.after(0, lambda path=path: self._log(f"Created page PNG: {os.path.basename(path)}"))

                    ok, info = _export_png_pages_via_inkscape(
                        prepared_svg_path,
                        png_path,
                        on_page_png_created=_page_png_created,
                    )
                    if ok:
                        elapsed = float((info or {}).get("elapsed_s") or 0.0)
                        page_count = int((info or {}).get("page_count") or 0)
                        used_chunks = int((info or {}).get("chunk_count") or 1)
                        mode_label = "x3" if used_chunks >= 3 else "x1"
                        self.root.after(0, lambda elapsed=elapsed, page_count=page_count, mode_label=mode_label: self._log(
                            f"PNG export done in {elapsed:.2f}s across {page_count} page(s) using {mode_label}"
                        ))
                    else:
                        failures.append("PNG")
                        err = str((info or {}).get("error") or "PNG export failed")
                        elapsed = float((info or {}).get("elapsed_s") or 0.0) if isinstance(info, dict) else 0.0
                        if elapsed > 0:
                            self.root.after(0, lambda elapsed=elapsed: self._log(f"PNG export failed after {elapsed:.2f}s"))
                        self.root.after(0, lambda err=err: self._log(err))
            finally:
                try:
                    if prepared_svg_path and prepared_svg_path != svg_path and os.path.isfile(prepared_svg_path):
                        os.remove(prepared_svg_path)
                except Exception:
                    pass

            total_elapsed = time.perf_counter() - started
            if failures:
                _l.w(
                    "[export.worker] failed failures=%s elapsed=%.2fs",
                    ",".join(failures),
                    float(total_elapsed),
                )
                self.root.after(0, lambda failures=failures: self._render_done(f"Export failed: {', '.join(failures)}"))
                return
            _l.i("[export.worker] ok elapsed=%.2fs", float(total_elapsed))
            self.root.after(0, lambda total_elapsed=total_elapsed: self._render_done(f"Export ({total_elapsed:.2f}s)"))
        except Exception as ex:
            _l.w("[export.worker] exception %s\n%s", str(ex), traceback.format_exc())
            self.root.after(0, lambda ex=ex: self._render_done(f"Export failed: {ex}"))
        finally:
            self._stop_web_activity_monitor()

    def _on_close(self):
        self._server_stop.set()
        self._stop_web_activity_monitor()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="")
    ap.add_argument("--sheet-id", default="")
    ap.add_argument("--sheet-range", default="")
    ap.add_argument("--log-level", default="global")
    ns = ap.parse_args(argv)

    initial = None
    if ns.template:
        initial = AppRequest(
            template=_normalize_path(ns.template),
            sheet_id=ns.sheet_id,
            sheet_range=ns.sheet_range,
            log_level=ns.log_level,
        )
    app = DeckMakerApp(initial)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
