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
