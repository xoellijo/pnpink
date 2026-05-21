# -*- coding: utf-8 -*-
"""Tk/GUI-facing helpers shared by the resident DeckMaker app."""

from __future__ import annotations

from typing import Callable


_PROGRESS_LISTENER: Callable[[str, dict], None] | None = None


def set_listener(fn: Callable[[str, dict], None] | None) -> None:
    global _PROGRESS_LISTENER
    _PROGRESS_LISTENER = fn


def clear_listener(fn: Callable[[str, dict], None] | None = None) -> None:
    global _PROGRESS_LISTENER
    if fn is None or _PROGRESS_LISTENER is fn:
        _PROGRESS_LISTENER = None


def emit(kind: str, **payload) -> None:
    listener = _PROGRESS_LISTENER
    if listener is None:
        return
    try:
        listener(str(kind or ""), dict(payload))
    except Exception:
        pass


def progress_text(label: str, current: int, total: int, *, width: int = 50) -> str:
    total_i = max(0, int(total or 0))
    current_i = max(0, int(current or 0))
    prefix = f"{str(label or '').strip()} " if str(label or "").strip() else ""
    if total_i <= 0:
        return f"{prefix}{current_i}".strip()
    current_i = min(current_i, total_i)
    bar_width = max(4, int(width or 50))
    ratio = float(current_i) / float(total_i)
    filled = max(0, min(int(round(ratio * bar_width)), bar_width))
    bar = "#" * filled + "." * (bar_width - filled)
    pct = int(round(ratio * 100.0))
    return f"{prefix}{current_i}/{total_i} [{bar}] {pct}%"


class StatusBar:
    """Segmented bottom status bar for long-running GUI work."""

    def __init__(self, parent, *, textvariable=None):
        try:
            import tkinter as tk
            from tkinter import ttk
        except Exception:  # pragma: no cover
            raise

        self.tk = tk
        self.ttk = ttk
        self.main_var = textvariable if textvariable is not None else tk.StringVar(value="Ready")
        self.phase_var = tk.StringVar(value="")
        self.detail_var = tk.StringVar(value="")
        self.retry_var = tk.StringVar(value="")
        self.pulse_var = tk.StringVar(value="")

        self.frame = ttk.Frame(parent)
        self.frame.columnconfigure(0, minsize=130)
        self.frame.columnconfigure(2, minsize=150)
        self.frame.columnconfigure(4, weight=1, minsize=220)
        self.frame.columnconfigure(6, minsize=120)
        self.frame.columnconfigure(8, minsize=42)

        self.main_label = ttk.Label(self.frame, textvariable=self.main_var, anchor="w")
        self.main_label.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._phase_sep = ttk.Separator(self.frame, orient="vertical")
        self._phase_label = ttk.Label(self.frame, textvariable=self.phase_var, anchor="w")
        self._detail_sep = ttk.Separator(self.frame, orient="vertical")
        self._detail_label = ttk.Label(self.frame, textvariable=self.detail_var, anchor="w")
        self._retry_sep = ttk.Separator(self.frame, orient="vertical")
        self._retry_label = ttk.Label(self.frame, textvariable=self.retry_var, anchor="w")
        self._pulse_sep = ttk.Separator(self.frame, orient="vertical")
        self._pulse_label = ttk.Label(self.frame, textvariable=self.pulse_var, anchor="w")

        self._segments = [
            (self.phase_var, self._phase_sep, 1, self._phase_label, 2, (6, 6)),
            (self.detail_var, self._detail_sep, 3, self._detail_label, 4, (6, 6)),
            (self.retry_var, self._retry_sep, 5, self._retry_label, 6, (6, 6)),
            (self.pulse_var, self._pulse_sep, 7, self._pulse_label, 8, (6, 6)),
        ]

        try:
            self.sizegrip = ttk.Sizegrip(self.frame)
            self.sizegrip.grid(row=0, column=9, sticky="e", padx=(6, 0))
        except Exception:
            self.sizegrip = None
        self._refresh_segments()

    def grid(self, *args, **kwargs):
        return self.frame.grid(*args, **kwargs)

    def pack(self, *args, **kwargs):
        return self.frame.pack(*args, **kwargs)

    def set_main(self, text: str) -> None:
        self.main_var.set(str(text or "").strip() or "Ready")

    def set_phase(self, text: str) -> None:
        self.phase_var.set(self._short(text, 70))
        self._refresh_segments()

    def set_detail(self, text: str) -> None:
        self.detail_var.set(self._short(text, 140))
        self._refresh_segments()

    def set_retry(self, text: str) -> None:
        self.retry_var.set(self._short(text, 40))
        self._refresh_segments()

    def set_pulse(self, text: str) -> None:
        self.pulse_var.set(self._short(text, 12))
        self._refresh_segments()

    def clear_activity(self) -> None:
        self.set_phase("")
        self.clear_detail()

    def clear_detail(self) -> None:
        self.set_detail("")
        self.set_retry("")
        self.set_pulse("")

    def _refresh_segments(self) -> None:
        for var, sep, sep_col, label, label_col, padx in self._segments:
            if str(var.get() or "").strip():
                sep.grid(row=0, column=sep_col, sticky="ns", padx=(0, 0))
                label.grid(row=0, column=label_col, sticky="ew", padx=padx)
            else:
                sep.grid_remove()
                label.grid_remove()

    @staticmethod
    def _short(text: str, limit: int) -> str:
        s = " ".join(str(text or "").split())
        if len(s) <= limit:
            return s
        return s[: max(0, int(limit) - 3)].rstrip() + "..."


class Tooltip:
    """Small Tk tooltip with delayed entry."""

    def __init__(self, widget, text: str, *, delay_ms: int = 1000):
        self.widget = widget
        self.text = str(text or "").strip()
        self.delay_ms = max(0, int(delay_ms or 0))
        self._after_id = None
        self._window = None
        if not self.text:
            return
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is None:
            return
        try:
            self.widget.after_cancel(self._after_id)
        except Exception:
            pass
        self._after_id = None

    def _show(self):
        if self._window is not None or not self.text:
            return
        try:
            import tkinter as tk

            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                win,
                text=self.text,
                justify="left",
                background="#ffffe8",
                relief="solid",
                borderwidth=1,
                padx=6,
                pady=3,
                wraplength=360,
            )
            label.pack()
            self._window = win
        except Exception:
            self._window = None

    def _hide(self, _event=None):
        self._cancel()
        if self._window is None:
            return
        try:
            self._window.destroy()
        except Exception:
            pass
        self._window = None


def attach_tooltip(widget, text: str, *, delay_ms: int = 1000):
    tooltip = Tooltip(widget, text, delay_ms=delay_ms)
    try:
        widget._pnpink_tooltip = tooltip
    except Exception:
        pass
    return tooltip
