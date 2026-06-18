# -*- coding: utf-8 -*-
"""Path resolution helpers for the resident DeckMaker app."""

from __future__ import annotations

import os
from pathlib import Path


def normalize(path: str) -> str:
    return os.path.normpath(os.path.abspath(str(path or "").strip()))


def examples_dir(base_dir: str) -> str:
    return os.path.join(base_dir, "examples")


def app_icon(base_dir: str) -> str:
    return os.path.join(base_dir, "assets", "deckmaker_icon.png")


def app_log(base_dir: str) -> str:
    return os.path.join(base_dir, "pnpink.log")


def output_svg(template: str) -> str:
    p = normalize(template)
    base_dir = os.path.dirname(p)
    stem = Path(p).stem
    return os.path.normpath(os.path.join(base_dir, f"{stem}_output.svg"))


def output_pdf(template: str) -> str:
    stem, _ext = os.path.splitext(output_svg(template))
    return os.path.normpath(stem + ".pdf")


def output_png(template: str) -> str:
    stem, _ext = os.path.splitext(output_svg(template))
    return os.path.normpath(stem + ".png")


def output_other(template: str, export_type: str) -> str:
    stem, _ext = os.path.splitext(output_svg(template))
    ext = str(export_type or "png").strip().lower() or "png"
    return os.path.normpath(f"{stem}.{ext}")


def profile_pdf(pdf_path: str, profile: str) -> str:
    base_pdf = normalize(pdf_path)
    prof = str(profile or "default").strip().lower()
    if prof == "default":
        return base_pdf
    stem, ext = os.path.splitext(base_pdf)
    return os.path.normpath(f"{stem}_{prof}{ext or '.pdf'}")


def output_page_png(png_path: str, page_number: int) -> str:
    stem, ext = os.path.splitext(normalize(png_path))
    return os.path.normpath(f"{stem}_p{int(page_number)}{ext or '.png'}")


def output_page(base_path: str, page_number: int) -> str:
    stem, ext = os.path.splitext(normalize(base_path))
    return os.path.normpath(f"{stem}_p{int(page_number)}{ext}")


def output_page_pngs(png_path: str, page_count: int) -> list[str]:
    total = max(1, int(page_count or 1))
    if total == 1:
        return [normalize(png_path)]
    return [output_page_png(png_path, page_no) for page_no in range(1, total + 1)]


def output_pages(base_path: str, page_count: int) -> list[str]:
    total = max(1, int(page_count or 1))
    if total == 1:
        return [normalize(base_path)]
    return [output_page(base_path, page_no) for page_no in range(1, total + 1)]
