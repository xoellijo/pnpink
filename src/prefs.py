#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prefs.py — PnPInk Preferences (INI-based)
Adds:
  - console_log_level
  - file_log_level
  - log_json (0/1)
"""
import os, configparser, tempfile
from typing import Any, Optional
import builtins as BI
_bi = BI
import inkex

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

    # PDF output profile preset.
    "pdf_settings":       "default",
    "auto_create":        "1",
    "auto_open":          "0",
    "auto_export":        "0",
    "export_pdf":         "1",
    "export_png":         "0",
    "pdf_raster_filters": "0",
    "split_svg_output":   "0",
    "split_svg_chunk_mb": "64",
    "inkscape_shell_workers": "6",

}


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
    cfg = configparser.ConfigParser()
    cfg[_SECTION] = {}
    keys = _bi.set(_DEFAULTS.keys()) | _bi.set(_CACHE.keys())
    for k in sorted(keys):
        v = _CACHE.get(k, _DEFAULTS.get(k, ""))
        cfg[_SECTION][k] = "" if v is None else str(v)
    os.makedirs(os.path.dirname(_INI_PATH), exist_ok=True)
    fd, tmp_path = tempfile.mkdtemp(), None
    # Use simple write (atomic enough for our use in extensions)
    with open(_INI_PATH, "w", encoding="utf-8") as fh:
        cfg.write(fh)

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


def get_pdf_settings(default: str = "default") -> str:
    valid = {"default", "prepress"}
    value = str(get("pdf_settings", default) or default).strip().lower()
    return value if value in valid else default


def set_pdf_settings(value: str) -> None:
    set("pdf_settings", get_pdf_settings(str(value or "default")), save=True)


def get_pdf_profiles(default: str = "default") -> list[str]:
    valid = {"default", "prepress"}
    raw = str(get("pdf_profiles", default) or default).strip().lower()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        parts = [default]
    out: list[str] = []
    for part in parts:
        if part in valid and part not in out:
            out.append(part)
    return out or [default]


def set_pdf_profiles(values: list[str] | tuple[str, ...]) -> None:
    valid = {"default", "prepress"}
    out: list[str] = []
    for value in (values or []):
        item = str(value or "").strip().lower()
        if item in valid and item not in out:
            out.append(item)
    if not out:
        out = ["default"]
    set("pdf_profiles", ",".join(out), save=True)


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


def get_export_png(default: bool = False) -> bool:
    return str(get("export_png", "1" if default else "0")).strip() == "1"


def set_export_png(flag: bool) -> None:
    set("export_png", "1" if flag else "0", save=True)


def get_pdf_raster_filters(default: bool = False) -> bool:
    return str(get("pdf_raster_filters", "1" if default else "0")).strip() == "1"


def set_pdf_raster_filters(flag: bool) -> None:
    set("pdf_raster_filters", "1" if flag else "0", save=True)


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

# ---------------- Saver extension (UI -> INI) ----------------
class PrefsSave(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab")
        pars.add_argument("--console_log_level", type=str, default="warn")
        pars.add_argument("--file_log_level",    type=str, default="trace")
        pars.add_argument("--log_json",          type=inkex.Boolean, default=False)

    def effect(self):
        try:
            set_console_level(self.options.console_log_level)
            set_file_level(self.options.file_log_level)
            set_log_json(bool(self.options.log_json))
            inkex.utils.errormsg(f"[PnPInk Prefs] Saved to {ini_path()}")
        except Exception as ex:
            inkex.utils.errormsg(f"[PnPInk Prefs] ERROR saving: {ex}")

if __name__ == "__main__":
    PrefsSave().run()
