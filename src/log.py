# -*- coding: utf-8 -*-
"""
PnPInk logger (FAST) — optimized for speed, no inspect.stack(), open file handle.
"""

from __future__ import annotations
import os
import time
import inspect

import inkex
import prefs

__all__ = ['get_logger', 'init', 'Logger', 'd', 'i', 'w', 'e', 't', 'a', 'close', 's']

# ---------------------------- Levels ----------------------------------------
_VALID = {"none","error","warn","info","debug","trace",
          "all","debug_only","trace_only"}

def _norm_level(s: str, default="warn"):
    s = (s or "").strip().lower()
    return s if s in _VALID else default

def _sink_flags(level: str):
    """Return tuple: (allow_info, allow_warn, allow_error, allow_debug, allow_trace)"""
    lvl = _norm_level(level)
    allow_error = lvl != "none"
    allow_warn  = lvl in ("warn","info","debug","trace","all","debug_only","trace_only")
    allow_info  = lvl in ("info","debug","trace","all","debug_only","trace_only")
    allow_debug = lvl in ("debug","all","debug_only")
    allow_trace = lvl in ("trace","all","trace_only")

    if lvl == "error":
        return (False, False, True, False, False)
    if lvl == "none":
        return (False, False, False, False, False)
    return (allow_info, allow_warn or allow_info, True, allow_debug, allow_trace)

# ----------------------------- Logger ----------------------------------------
class Logger:
    """
    FAST Logger:
    - No inspect.stack()
    - Keeps output file open
    - Lightweight str() conversion
    - Safe close()
    """

    def __init__(self, tag, console_level, file_level, file_path):
        self.tag = tag
        self.console_level = _norm_level(console_level)
        self.file_level    = _norm_level(file_level)
        self.file_path     = file_path
        self._t_prev = time.perf_counter()

        # Precompute flags one time
        self._console_flags = _sink_flags(self.console_level)
        self._file_flags    = _sink_flags(self.file_level)

        # Open file handle once
        self._fh = None
        try:
            self._fh = open(self.file_path, "w", encoding="utf-8")
        except Exception as ex:
            inkex.utils.errormsg(f"[{self.tag}] ERROR opening log file: {ex}")

    # ------------ helpers
    def _dt_ms(self):
        now = time.perf_counter()
        dt = int((now - self._t_prev) * 1000)
        self._t_prev = now
        return dt

    def _caller_module(self):
        """Super-fast version: walk a few frames up."""
        try:
            frame = inspect.currentframe()
            # go up ~4 frames: wrapper → _log → _compose → _caller_module
            for _ in range(4):
                if frame is None:
                    break
                frame = frame.f_back
            # walk until we find a real module
            while frame is not None:
                g = frame.f_globals
                mod = g.get("__name__")
                if mod and mod != __name__:
                    return mod.split(".")[-1]
                frame = frame.f_back
        except Exception:
            pass
        return self.tag  # fallback

    def _ensure_str(self, x):
        try:
            return str(x)
        except Exception:
            try: return repr(x)
            except Exception: return "<unprintable>"

    def _compose(self, level, msg, *args):
        mod = self._caller_module()
        dt = self._dt_ms()
        dt_part = f" +{dt}ms" if dt>0 else ""

        base = msg if isinstance(msg, str) else self._ensure_str(msg)
        if args:
            parts = [self._ensure_str(a) for a in args]
            base = base + " " + " ".join(parts)

        line = f"[{level.upper()}: {mod}{dt_part}] {base}"
        return line, self._console_flags, self._file_flags

    def _emit_console(self, line, con_flags, level):
        allow_info, allow_warn, allow_error, allow_debug, allow_trace = con_flags
        ok = ((level == "INFO" and allow_info) or
              (level == "WARN" and allow_warn) or
              (level == "ERROR" and allow_error) or
              (level == "DEBUG" and allow_debug) or
              (level == "TRACE" and allow_trace))
        if ok:
            inkex.utils.debug(line)

    def _emit_file(self, line, file_flags, level):
        if not self._fh: 
            return
        allow_info, allow_warn, allow_error, allow_debug, allow_trace = file_flags
        ok = ((level == "INFO" and allow_info) or
              (level == "WARN" and allow_warn) or
              (level == "ERROR" and allow_error) or
              (level == "DEBUG" and allow_debug) or
              (level == "TRACE" and allow_trace))
        if ok:
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except Exception:
                pass

    # ------------- public API
    def info(self, msg, *a):  self._log("INFO", msg, *a)
    def warn(self, msg, *a):  self._log("WARN", msg, *a)
    def error(self, msg, *a): self._log("ERROR", msg, *a)
    def debug(self, msg, *a): self._log("DEBUG", msg, *a)
    def trace(self, msg, *a): self._log("TRACE", msg, *a)

    i = info; w = warn; e = error; d = debug; t = trace

    def _log(self, level, msg, *a):
        line, con_flags, file_flags = self._compose(level, msg, *a)
        self._emit_console(line, con_flags, level)
        self._emit_file(line, file_flags, level)

    def close(self):
        try:
            if self._fh and not self._fh.closed:
                self._fh.flush()
                self._fh.close()
        except Exception:
            pass

# --------------------------- singleton API -----------------------------------
_LOGGER = None

def get_logger(effect=None, console_level="global",
               file_level="global", tag_override=None):
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    if console_level == "global":
        console_level = prefs.get_console_level("warn")
    if file_level == "global":
        file_level = prefs.get_file_level("trace")

    tag = (tag_override or 
           (effect.__class__.__name__[:10] if effect else "PnPInk"))

    ext_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(ext_dir, "pnpink.log")

    _LOGGER = Logger(tag, console_level, file_level, file_path)
    return _LOGGER

def init(tag="PnP", console_level="warn", file_level="trace"):
    global _LOGGER
    ext_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(ext_dir, "pnpink.log")
    _LOGGER = Logger(tag, console_level, file_level, file_path)
    return _LOGGER

def close():
    global _LOGGER
    if _LOGGER is not None:
        try: _LOGGER.close()
        except: pass

# ------------------- shorthand API (import as _l) ----------------------------
def _ensure_logger():
    if globals().get("_LOGGER") is None:
        get_logger()

def d(msg, *a): _ensure_logger(); _LOGGER.d(msg, *a)
def i(msg, *a): _ensure_logger(); _LOGGER.i(msg, *a)
def w(msg, *a): _ensure_logger(); _LOGGER.w(msg, *a)
def e(msg, *a): _ensure_logger(); _LOGGER.e(msg, *a)
def t(msg, *a): _ensure_logger(); _LOGGER.t(msg, *a)

def a(msg, *a):
    _ensure_logger()
    _LOGGER.i(msg, *a)
    _LOGGER.d(msg, *a)
    _LOGGER.t(msg, *a)


def s(title: str = ""):
    """Log a separator/divider line."""
    _ensure_logger()
    if title:
        _LOGGER.i(f"----- {title} -----")
    else:
        _LOGGER.i("------------------------------")
