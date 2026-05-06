#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prefs.py — PnPInk Preferences (INI-based)
Adds:
  - console_log_level
  - file_log_level
  - log_json (0/1)
"""
import os, configparser
from typing import Any, Optional
import builtins as BI
_bi = BI

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_INI_PATH = os.path.join(_BASE_DIR, "preferences.ini")
_SECTION  = "prefs"

_CACHE_LOADED = False
_CACHE: dict[str, str] = {}

_DEFAULTS = {
    "console_log_level": "warn",  # none|error|warn|info|debug|trace|all|debug_only|trace_only
    "file_log_level":    "trace",
    "log_json":          "0",     # 1 -> JSON on both sinks (unless env overrides)

    # Default style for Marks{} (cut marks)
    # These are used when no style id is provided or cannot be resolved.
    "marks_stroke":       "#000000",
    # Cut marks default thickness: 0.1mm (0.2mm is visually heavy in most print workflows)
    "marks_stroke_width": "0.1mm",
    "marks_opacity":      "1.0",
    "marks_linecap":      "butt",
    "marks_linejoin":     "miter",
    "marks_dasharray":    "",

    "pdf_profiles":       "default",
    "export_pdfx":        "0",
    "pdfx_version":       "3",
    "pdf_cmyk_icc":       "",
    "pdf_cmyk_pure_black_text": "1",
    "auto_create":        "1",
    "auto_open":          "0",
    "auto_export":        "0",
    "export_pdf":         "1",
    "export_png":         "0",
    "export_other_format": "png",
    "export_other_pages": "",
    "export_png_antialias": "2",
    "export_png_background": "#ffffff",
    "export_png_background_opacity": "0.0",
    "pdf_raster_mode":    "png",
    "export_dpi":         "300",
    "export_jpeg_quality": "90",
    "split_svg_output":   "0",
    "split_svg_chunk_mb": "64",
    "inkscape_shell_workers": "6",

}

_PREF_DOCS: list[tuple[str, tuple[str, ...]]] = [
    ("auto_create", ("Auto-generate the deck when the window starts. Values: 0 | 1",)),
    ("auto_export", ("Auto-export after generation. Values: 0 | 1",)),
    ("auto_open", ("Open the generated SVG after generation. Values: 0 | 1",)),
    ("console_log_level", ("Console log verbosity. Values: none | error | warn | info | debug | trace | all | debug_only | trace_only",)),
    ("file_log_level", ("File log verbosity written to pnpink.log. Values: none | error | warn | info | debug | trace | all | debug_only | trace_only",)),
    ("log_json", ("Write logs as JSON. Values: 0 | 1",)),
    ("export_pdf", ("Enable standard PDF export. Values: 0 | 1",)),
    ("export_pdfx", ("Enable PDF/X CMYK export. Values: 0 | 1",)),
    ("pdfx_version", ("PDF/X standard used for CMYK export. Values: 1a | 3 | 4",)),
    ("pdf_profiles", ("Standard PDF output profiles. Comma-separated values: default, screen, ebook, printer, prepress",)),
    ("pdf_cmyk_icc", (
        "ICC profile used for PDF/X CMYK export.",
        "Values: iso_coated_v2_fogra39 | pso_coated_v3_fogra51 | pso_coated_v3_300 | pso_uncoated_v3_fogra52 | iso_uncoated_yellowish_fogra29 | gracol_2006_coated1 | gracol_2013_crpc6 | us_web_coated_swop_v2",
        "Absolute paths to *.icc or *.icm are accepted as an advanced fallback.",
    )),
    ("pdf_cmyk_pure_black_text", ("Force text to pure black in PDF/X CMYK export when supported. Values: 0 | 1",)),
    ("pdf_raster_mode", (
        "Filter rasterization strategy for PDF export.",
        "Values: png | jpeg | png_alpha | inkscape | none",
    )),
    ("export_png", ("Enable additional non-PDF export. Values: 0 | 1",)),
    ("export_other_format", ("Additional output format. Values: png | jpeg | jpeg2000 | pdf | svg | tiff | webp | ps | eps | emf | wmf",)),
    ("export_other_pages", ("Optional pages for additional output. Examples: 1,3-5,8. Empty = all pages",)),
    ("export_png_antialias", ("PNG antialias level used by png_alpha raster export. Values: 0 | 1 | 2 | 3",)),
    ("export_png_background", ("Bitmap matte/background color when applicable. Values: any valid SVG color string",)),
    ("export_png_background_opacity", ("Bitmap background opacity when applicable. Values: 0.0..1.0 or 1..255",)),
    ("export_dpi", ("Global Inkscape export DPI. Raster filters use export_dpi * 1.5. Values: integer >= 1",)),
    ("export_jpeg_quality", ("JPEG quality used by JPEG exports (including Pillow fallback conversions). Values: integer 70..95",)),
    ("inkscape_shell_workers", ("Parallel Inkscape shell workers used during export. Values: integer >= 1",)),
    ("split_svg_output", ("Split DM_output into SVG parts. Values: 0 | 1",)),
    ("split_svg_chunk_mb", ("Target size per SVG part in megabytes. Values: integer >= 1",)),
    ("marks_stroke", ("Default cut/registration mark stroke color. Values: any valid SVG color string",)),
    ("marks_stroke_width", ("Default cut/registration mark stroke width. Values: any SVG/CSS length",)),
    ("marks_opacity", ("Default cut/registration mark opacity. Values: 0.0..1.0",)),
    ("marks_linecap", ("Default cut/registration mark line cap. Values: butt | round | square",)),
    ("marks_linejoin", ("Default cut/registration mark line join. Values: miter | round | bevel",)),
    ("marks_dasharray", ("Default cut/registration mark dash pattern. Values: empty or any SVG stroke-dasharray value",)),
]


def get_marks_style_dict() -> dict[str, str]:
    """Return default stroke-related style for Marks{}.

    Dev note: this is intentionally minimal and additive; no UI wiring in this iteration.
    """
    return {
        "stroke": str(get("marks_stroke", "#000000")),
        "stroke-width": str(get("marks_stroke_width", "0.1mm")),
        "opacity": str(get("marks_opacity", "1.0")),
        "stroke-linecap": str(get("marks_linecap", "butt")),
        "stroke-linejoin": str(get("marks_linejoin", "miter")),
        "stroke-dasharray": str(get("marks_dasharray", "")),
        "fill": "none",
    }

def ini_path() -> str: return _INI_PATH

def _ensure_loaded() -> None:
    global _CACHE_LOADED, _CACHE
    if _CACHE_LOADED: return
    _CACHE = {}
    cfg = configparser.ConfigParser()
    if os.path.isfile(_INI_PATH):
        try:
            with open(_INI_PATH, "r", encoding="utf-8") as f:
                cfg.read_file(f)
            if cfg.has_section(_SECTION):
                for k, v in cfg.items(_SECTION): _CACHE[k] = v
        except Exception: _CACHE = {}
    _CACHE_LOADED = True

def reload() -> None:
    global _CACHE_LOADED, _CACHE
    _CACHE_LOADED = False; _CACHE = {}; _ensure_loaded()

def get(name: str, default: Optional[Any]=None) -> Any:
    _ensure_loaded()
    if name in _CACHE: return _CACHE[name]
    if name in _DEFAULTS: return _DEFAULTS[name] if default is None else default
    return default

def set(name: str, value: Any, save: bool=True) -> None:
    _ensure_loaded()
    _CACHE[name] = "" if value is None else str(value)
    if save: _save_ini()

def _save_ini() -> None:
    keys = _bi.set(_DEFAULTS.keys()) | _bi.set(_CACHE.keys())
    documented = [key for key, _doc in _PREF_DOCS if key in keys]
    documented_set = _bi.set(documented)
    doc_by_key = dict(_PREF_DOCS)
    extra = sorted(key for key in keys if key not in documented_set)
    os.makedirs(os.path.dirname(_INI_PATH), exist_ok=True)
    with open(_INI_PATH, "w", encoding="utf-8") as fh:
        fh.write(f"[{_SECTION}]\n")
        for key in documented + extra:
            fh.write("\n")
            for line in doc_by_key.get(key, ("Custom/unknown preference.",)):
                fh.write(f"# {line}\n")
            v = _CACHE.get(key, _DEFAULTS.get(key, ""))
            fh.write(f"{key} = {'' if v is None else str(v)}\n")

# Convenience getters/setters
def _norm_level(s: str, default: str) -> str:
    valid = {"none","error","warn","info","debug","trace","all","debug_only","trace_only"}
    s = (s or "").strip().lower()
    return s if s in valid else default

def get_console_level(default: str="warn") -> str:
    return _norm_level(str(get("console_log_level", default)), default)

def set_console_level(level: str) -> None:
    set("console_log_level", _norm_level(level, "warn"), save=True)

def get_file_level(default: str="trace") -> str:
    return _norm_level(str(get("file_log_level", default)), default)

def set_file_level(level: str) -> None:
    set("file_log_level", _norm_level(level, "trace"), save=True)

def set_log_json(flag: bool) -> None:
    set("log_json", "1" if flag else "0", save=True)


def get_pdf_profiles(default: str = "default") -> list[str]:
    valid = {"default", "screen", "ebook", "printer", "prepress"}
    raw = str(get("pdf_profiles", default) or default).strip().lower()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return []
    out: list[str] = []
    for part in parts:
        if part in valid and part not in out:
            out.append(part)
    return out


def set_pdf_profiles(values: list[str] | tuple[str, ...]) -> None:
    valid = {"default", "screen", "ebook", "printer", "prepress"}
    out: list[str] = []
    for value in (values or []):
        item = str(value or "").strip().lower()
        if item in valid and item not in out:
            out.append(item)
    set("pdf_profiles", ",".join(out), save=True)


def get_pdf_cmyk_icc(default: str = "") -> str:
    return str(get("pdf_cmyk_icc", default) or default).strip()


def set_pdf_cmyk_icc(value: str) -> None:
    set("pdf_cmyk_icc", str(value or "").strip(), save=True)


def get_pdf_cmyk_pure_black_text(default: bool = True) -> bool:
    return str(get("pdf_cmyk_pure_black_text", "1" if default else "0")).strip() == "1"


def set_pdf_cmyk_pure_black_text(flag: bool) -> None:
    set("pdf_cmyk_pure_black_text", "1" if flag else "0", save=True)


def _normalize_pdfx_version(value: str, default: str = "3") -> str:
    text = str(value or default or "3").strip().lower()
    aliases = {
        "1": "1a",
        "x1": "1a",
        "x-1": "1a",
        "pdf/x-1": "1a",
        "pdf/x-1a": "1a",
        "1a": "1a",
        "3": "3",
        "x3": "3",
        "pdf/x-3": "3",
        "4": "4",
        "x4": "4",
        "pdf/x-4": "4",
    }
    if "pdf/x-1a" in text:
        return "1a"
    if "pdf/x-3" in text:
        return "3"
    if "pdf/x-4" in text:
        return "4"
    fallback = str(default or "3").strip().lower()
    return aliases.get(text, fallback if fallback in {"1a", "3", "4"} else "3")


def get_pdfx_version(default: str = "3") -> str:
    return _normalize_pdfx_version(get("pdfx_version", default), default)


def set_pdfx_version(value: str) -> None:
    set("pdfx_version", _normalize_pdfx_version(str(value or "3")), save=True)


def get_auto_create(default: bool = True) -> bool:
    return str(get("auto_create", "1" if default else "0")).strip() == "1"


def set_auto_create(flag: bool) -> None:
    set("auto_create", "1" if flag else "0", save=True)


def get_auto_open(default: bool = False) -> bool:
    return str(get("auto_open", "1" if default else "0")).strip() == "1"


def set_auto_open(flag: bool) -> None:
    set("auto_open", "1" if flag else "0", save=True)


def get_auto_export(default: bool = False) -> bool:
    return str(get("auto_export", "1" if default else "0")).strip() == "1"


def set_auto_export(flag: bool) -> None:
    set("auto_export", "1" if flag else "0", save=True)


def get_export_pdf(default: bool = True) -> bool:
    return str(get("export_pdf", "1" if default else "0")).strip() == "1"


def set_export_pdf(flag: bool) -> None:
    set("export_pdf", "1" if flag else "0", save=True)


def get_export_pdfx(default: bool = False) -> bool:
    return str(get("export_pdfx", "1" if default else "0")).strip() == "1"


def set_export_pdfx(flag: bool) -> None:
    set("export_pdfx", "1" if flag else "0", save=True)


def get_export_png(default: bool = False) -> bool:
    return str(get("export_png", "1" if default else "0")).strip() == "1"


def set_export_png(flag: bool) -> None:
    set("export_png", "1" if flag else "0", save=True)


def get_export_other_format(default: str = "png") -> str:
    valid = {"png", "jpeg", "jpeg2000", "pdf", "svg", "tiff", "webp", "ps", "eps", "emf", "wmf"}
    value = str(get("export_other_format", default) or default).strip().lower()
    return value if value in valid else str(default or "png").strip().lower()


def set_export_other_format(value: str) -> None:
    valid = {"png", "jpeg", "jpeg2000", "pdf", "svg", "tiff", "webp", "ps", "eps", "emf", "wmf"}
    item = str(value or "png").strip().lower()
    set("export_other_format", item if item in valid else "png", save=True)


def get_export_other_pages(default: str = "") -> str:
    return str(get("export_other_pages", default) or default).strip()


def set_export_other_pages(value: str) -> None:
    set("export_other_pages", str(value or "").strip(), save=True)


def get_export_png_antialias(default: int = 2) -> int:
    try:
        value = int(str(get("export_png_antialias", default) or default).strip())
    except Exception:
        value = int(default)
    return max(0, min(value, 3))


def set_export_png_antialias(value: int) -> None:
    try:
        out = int(value)
    except Exception:
        out = 2
    set("export_png_antialias", str(max(0, min(out, 3))), save=True)


def get_export_png_background(default: str = "#ffffff") -> str:
    value = str(get("export_png_background", default) or default).strip()
    return value or str(default)


def set_export_png_background(value: str) -> None:
    set("export_png_background", str(value or "#ffffff").strip() or "#ffffff", save=True)


def get_pdf_raster_mode(default: str = "png") -> str:
    valid = {"png", "jpeg", "png_alpha", "inkscape", "none"}
    value = str(get("pdf_raster_mode", default) or default).strip().lower()
    return value if value in valid else str(default or "png").strip().lower()


def set_pdf_raster_mode(value: str) -> None:
    valid = {"png", "jpeg", "png_alpha", "inkscape", "none"}
    item = str(value or "png").strip().lower()
    set("pdf_raster_mode", item if item in valid else "png", save=True)


def get_export_png_background_opacity(default: str = "0.0") -> str:
    raw = str(get("export_png_background_opacity", default) or default).strip()
    if not raw:
        return str(default)
    try:
        num = float(raw)
    except Exception:
        return str(default)
    if num < 0.0:
        num = 0.0
    if num <= 1.0:
        return f"{num:.3f}".rstrip("0").rstrip(".") if "." in f"{num:.3f}" else str(num)
    if num > 255.0:
        num = 255.0
    return str(int(round(num)))


def set_export_png_background_opacity(value: str | float | int) -> None:
    set("export_png_background_opacity", get_export_png_background_opacity(str(value)), save=True)


def get_export_dpi(default: int = 300) -> int:
    try:
        value = int(float(str(get("export_dpi", default) or default).strip()))
    except Exception:
        value = int(default)
    return max(1, min(value, 2400))


def set_export_dpi(value: int) -> None:
    try:
        out = int(float(str(value).strip()))
    except Exception:
        out = 300
    set("export_dpi", str(max(1, min(out, 2400))), save=True)


def get_export_jpeg_quality(default: int = 90) -> int:
    try:
        value = int(float(str(get("export_jpeg_quality", default) or default).strip()))
    except Exception:
        value = int(default)
    return max(70, min(value, 95))


def set_export_jpeg_quality(value: int) -> None:
    try:
        out = int(float(str(value).strip()))
    except Exception:
        out = 90
    set("export_jpeg_quality", str(max(70, min(out, 95))), save=True)


def get_split_svg_output(default: bool = False) -> bool:
    return str(get("split_svg_output", "1" if default else "0")).strip() == "1"


def set_split_svg_output(flag: bool) -> None:
    set("split_svg_output", "1" if flag else "0", save=True)


def get_split_svg_chunk_mb(default: int = 64) -> int:
    try:
        value = int(str(get("split_svg_chunk_mb", default) or default).strip())
    except Exception:
        value = int(default)
    return max(1, min(value, 2048))


def set_split_svg_chunk_mb(value: int) -> None:
    try:
        out = int(value)
    except Exception:
        out = 64
    set("split_svg_chunk_mb", str(max(1, min(out, 2048))), save=True)


def get_inkscape_shell_workers(default: int = 6) -> int:
    try:
        value = int(str(get("inkscape_shell_workers", default) or default).strip())
    except Exception:
        value = int(default)
    return max(1, min(value, 32))


def set_inkscape_shell_workers(value: int) -> None:
    try:
        out = int(value)
    except Exception:
        out = 6
    set("inkscape_shell_workers", str(max(1, min(out, 32))), save=True)
