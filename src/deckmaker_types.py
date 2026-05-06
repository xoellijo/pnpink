# -*- coding: utf-8 -*-
"""Shared DeckMaker app data types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppRequest:
    template: str
    sheet_id: str = ""
    sheet_range: str = ""
    dataset_source_mode: str = ""
    log_level: str = "global"


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
    other_pages: str
