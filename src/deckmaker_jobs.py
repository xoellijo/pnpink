# -*- coding: utf-8 -*-
"""UI-independent DeckMaker export jobs."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import time
from typing import Callable

import deckmaker_paths as DMPATHS
import export as EXPORT
import export_cut as EXPORTCUT
import export_items as EXPORTITEMS
import export_pdf as EXPORTPDF
import log as LOG
import temp_paths as TEMPPATHS
from deckmaker_types import ExportOptions


_l = LOG


def _ignore(*_args, **_kwargs):
    return None


@dataclass(frozen=True)
class JobEvents:
    log: Callable[[str], None] = _ignore
    activity: Callable[[str], None] = _ignore
    progress: Callable[[str, int, int], None] = _ignore


@dataclass(frozen=True)
class ExportResult:
    ok: bool
    status: str
    failures: tuple[str, ...] = ()


def available_other_export_formats(base_formats: tuple[str, ...]) -> tuple[str, ...]:
    return EXPORT.available_other_export_formats(base_formats)


def selected_pdf_profiles(options: ExportOptions) -> list[str]:
    names = ("default", "screen", "ebook", "printer", "prepress")
    selected = list(options.pdf_profiles)
    out: list[str] = []
    if options.export_pdf_standard:
        out.extend([name for name in names if name in selected] or ["default"])
    if options.export_pdfx and "cmyk" not in out:
        out.append("cmyk")
    return out or ["default"]


def _final_pdf_output(events: JobEvents, total_pages: int):
    total = max(0, int(total_pages or 0))
    buffer = {"text": ""}

    def on_output(chunk: str):
        buffer["text"] += str(chunk or "")
        while "\n" in buffer["text"] or "\r" in buffer["text"]:
            line, separator, rest = buffer["text"].partition("\n")
            if not separator:
                line, _separator, rest = buffer["text"].partition("\r")
            buffer["text"] = rest
            item = line.strip()
            if not item:
                continue
            match = re.search(r"PNPINK_FINAL_PDF_PROGRESS\s+(\d+)\s+(\d+)", item)
            if match:
                events.progress("Preparing final PDF", int(match.group(1)), int(match.group(2)))
                continue
            match = re.search(r"\bPage\s+(\d+)\b", item, re.IGNORECASE)
            if match and total:
                events.progress("Preparing final PDF", min(int(match.group(1)), total), total)
            else:
                events.activity("Preparing final PDF...")

    return on_output


def export_project(template: str, svg_path: str, options: ExportOptions, events: JobEvents) -> ExportResult:
    TEMPPATHS.cleanup_old_runs()
    formats = list(options.formats)
    started = time.perf_counter()
    source_kind = "generated output" if os.path.normcase(svg_path) == os.path.normcase(DMPATHS.output_svg(template)) else "current SVG"
    events.activity("Resolving SVG export source...")
    events.log(f"Export source: {os.path.basename(svg_path)} ({source_kind})")
    source_info = EXPORT.resolve_chunked_output_source(svg_path)
    if source_info.get("chunk_paths"):
        events.log(f"Using existing parted SVG output ({len(source_info.get('chunk_paths') or [])} part(s))")

    failures: list[str] = []
    details: list[str] = []
    if "pdf" in formats:
        profiles = selected_pdf_profiles(options)
        pdf_path = DMPATHS.output_pdf(template)
        page_count = EXPORT.svg_page_count(svg_path) if os.path.isfile(svg_path) else 0
        events.activity("Preparing PDF export...")
        events.log(f"PDF profiles: {', '.join(profiles)}")
        events.log(f"PDF filter mode: {options.pdf_raster_mode}")

        def page_created(path: str):
            events.log(f"Created temp part PDF: {os.path.basename(path)}")

        def raster_progress(done: int, total: int):
            label = str(options.pdf_raster_mode or "png").replace("png_alpha", "png alpha")
            events.progress(f"Creating {label} rasters for complex filters", int(done or 0), int(total or 0))

        ok, info = EXPORTPDF.export_pdf_via_inkscape(
            svg_path,
            pdf_path,
            pdf_profiles=profiles,
            raster_filter_mode=str(options.pdf_raster_mode or "png"),
            cmyk_icc=options.pdf_cmyk_icc,
            cmyk_pure_black_text=bool(options.pdf_cmyk_pure_black_text),
            pdfx_version=options.pdfx_version,
            export_dpi=int(options.export_dpi or 300),
            on_page_pdf_created=page_created,
            on_raster_progress=raster_progress,
            on_ghostscript_output=_final_pdf_output(events, page_count),
        )
        if ok:
            info = info or {}
            events.log(
                f"PDF export done in {float(info.get('elapsed_s') or 0):.2f}s across "
                f"{int(info.get('page_count') or 0)} page(s) using {int(info.get('chunk_count') or 1)} SVG part(s)"
            )
            for item in list(info.get("gs_outputs") or []):
                output_pdf = str(item.get("output_pdf") or "")
                if output_pdf:
                    events.log(f"PDF profile {item.get('profile') or 'default'} -> {os.path.basename(output_pdf)}")
        else:
            error = str((info or {}).get("error") or "PDF export failed")
            failures.append("PDF")
            details.append(f"PDF: {error}")
            events.log(error)

    for export_type in [item for item in formats if item != "pdf"]:
        out_path = DMPATHS.output_other(template, export_type)
        events.activity(f"Preparing {export_type.upper()} export...")

        def page_created(path: str, label=export_type.upper()):
            events.log(f"Created page {label}: {os.path.basename(path)}")

        def item_created(path: str, node_id: str, label=export_type.upper()):
            events.log(f"Created ID {label}: {node_id} -> {os.path.basename(path)}")

        if options.other_unit == "items":
            ok, info = EXPORTITEMS.export_items_via_inkscape(
                svg_path, out_path, export_type=export_type, item_spec=options.other_pages,
                export_dpi=int(options.export_dpi or 300), on_item_created=item_created,
            )
        elif options.other_unit == "ids":
            ok, info = EXPORT.export_other_ids_via_inkscape(
                svg_path, out_path, export_type=export_type, id_spec=options.other_pages,
                export_dpi=int(options.export_dpi or 300), jpeg_quality=int(options.jpeg_quality or 90),
                on_id_created=item_created,
            )
        else:
            ok, info = EXPORT.export_other_pages_via_inkscape(
                svg_path, out_path, export_type=export_type, page_spec=options.other_pages,
                export_dpi=int(options.export_dpi or 300), jpeg_quality=int(options.jpeg_quality or 90),
                on_page_created=page_created,
            )
        if ok:
            info = info or {}
            count = int(info.get("item_count") or info.get("id_count") or info.get("page_count") or 0)
            events.log(f"{export_type.upper()} export done in {float(info.get('elapsed_s') or 0):.2f}s across {count} item(s)")
        else:
            error = str((info or {}).get("error") or f"{export_type.upper()} export failed")
            failures.append(export_type.upper())
            details.append(f"{export_type.upper()}: {error}")
            events.log(error)

    if options.export_cut_template:
        cut_format = str(options.cut_template_format or "svg").strip().lower()
        events.activity(f"Preparing CUT-{cut_format.upper()} export...")
        ok, info = EXPORTCUT.export_cut_templates(
            svg_path, DMPATHS.output_svg(template), export_format=cut_format,
            export_dpi=int(options.export_dpi or 300),
        )
        if ok:
            outputs = list((info or {}).get("outputs") or [])
            for path in outputs:
                events.log(f"Created CUT-{cut_format.upper()}: {os.path.basename(path)}")
        else:
            error = str((info or {}).get("error") or "Cut template export failed")
            failures.append(f"CUT-{cut_format.upper()}")
            details.append(f"CUT-{cut_format.upper()}: {error}")
            events.log(error)

    elapsed = time.perf_counter() - started
    if failures:
        if details:
            events.log("Export details: " + " | ".join(details))
        return ExportResult(False, f"Export failed: {', '.join(failures)}", tuple(failures))
    _l.i("[export.worker] ok elapsed=%.2fs", float(elapsed))
    return ExportResult(True, f"Export ({elapsed:.2f}s)")
