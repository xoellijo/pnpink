# -*- coding: utf-8 -*-
"""Shared DeckMaker app data types."""

from __future__ import annotations

from dataclasses import dataclass


APP_VERSION = "Deckmaker v0.74.0-beta.3"
OTHER_EXPORT_FORMATS = ("png", "jpeg", "jpeg2000", "pdf", "svg", "tiff", "webp", "avif", "ps", "eps", "emf", "wmf")
CUT_TEMPLATE_FORMATS = {
    "svg": "svg (vector, cricut)",
    "dxf": "dxf (vector, cameo)",
    "png": "png (raster, all)",
}
SOURCE_MODE_LABELS = ("(empty)", "local CSV", "google sheet oauth", "google sheet public")
SOURCE_MODE_LABEL_TO_VALUE = {
    "(empty)": "",
    "local CSV": "local_csv",
    "google sheet oauth": "oauth",
    "google sheet public": "public",
}
SOURCE_MODE_VALUE_TO_LABEL = {value: label for label, value in SOURCE_MODE_LABEL_TO_VALUE.items()}


@dataclass
class AppRequest:
    template: str
    sheet_id: str = ""
    sheet_range: str = ""
    dataset_source_mode: str = ""
    log_level: str = "global"
    snapshot_path: str = ""
    autorun: bool = False
    selected_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportOptions:
    formats: tuple[str, ...]
    pdf_profiles: tuple[str, ...]
    export_pdf_standard: bool
    export_pdfx: bool
    pdf_raster_mode: str
    pdf_cmyk_icc: str
    pdf_cmyk_pure_black_text: bool
    pdfx_version: str
    export_dpi: int
    jpeg_quality: int
    other_format: str
    other_unit: str
    other_pages: str
    export_cut_template: bool = False
    cut_template_format: str = "svg"
