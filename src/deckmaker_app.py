#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resident DeckMaker launcher app.

This is intentionally small: the Inkscape extension only sends the current SVG
template to this process, and the process runs the existing engine on demand.
"""

from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
import traceback
import time
import webbrowser
from typing import Optional

import log as LOG
import icc_profiles as ICC
import image_preflight as PREFLIGHT
import deckmaker_paths as DMPATHS
import deckmaker_ipc as IPC
import export as EXPORT
import export_pdf as EXPORTPDF
import inkscape_cli as INKSCAPE
import prefs
import gui as GUI
import deckmaker_runner as RUNNER
from deckmaker_types import AppRequest, ExportOptions
import temp_paths as TEMPPATHS

_l = LOG

_normalize_path = DMPATHS.normalize

APP_VERSION = "Deckmaker v0.49"
DOCS_INTRO_URL = "https://xoellijo.github.io/pnpink/intro/"
DOCS_GUIDE_URL = "https://xoellijo.github.io/pnpink/quickstart/"
OTHER_EXPORT_FORMATS = ("png", "jpeg", "jpeg2000", "pdf", "svg", "tiff", "webp", "ps", "eps", "emf", "wmf")
SOURCE_MODE_LABELS = ("(empty)", "local CSV", "google sheet oauth", "google sheet public")
SOURCE_MODE_LABEL_TO_VALUE = {
    "(empty)": "",
    "local CSV": "local_csv",
    "google sheet oauth": "oauth",
    "google sheet public": "public",
}
SOURCE_MODE_VALUE_TO_LABEL = {value: label for label, value in SOURCE_MODE_LABEL_TO_VALUE.items()}


def notify_or_launch(
    template: str,
    sheet_id: str = "",
    sheet_range: str = "",
    log_level: str = "global",
    dataset_source_mode: str = "",
) -> bool:
    try:
        TEMPPATHS.cleanup_runs_now()
    except Exception:
        pass
    return IPC.notify_or_launch(__file__, template, sheet_id, sheet_range, log_level, dataset_source_mode)


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
        self.root.title(APP_VERSION)
        self.root.geometry("860x500")
        self.root.minsize(720, 430)
        self._icon_image = None
        self._apply_window_icon()

        self._queue: "queue.Queue[AppRequest]" = queue.Queue()
        self._ui_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._server_stop = threading.Event()
        self._render_thread: Optional[threading.Thread] = None
        self._activity_listener = None
        self._progress_listener = None
        self._live_activity_text = ""
        self._last_log_stamp = ""
        self._active_progress_label = ""

        self.template_var = tk.StringVar(value=(initial.template if initial else ""))
        self._refresh_window_title()
        self.sheet_id_var = tk.StringVar(value=(initial.sheet_id if initial else ""))
        self.sheet_range_var = tk.StringVar(value=(initial.sheet_range if initial else ""))
        self.source_mode_var = tk.StringVar(value=SOURCE_MODE_VALUE_TO_LABEL.get(initial.dataset_source_mode if initial else "", "(empty)"))
        self.status_var = tk.StringVar(value="Ready")
        self.auto_create_var = tk.BooleanVar(value=prefs.get_auto_create())
        self.auto_open_var = tk.BooleanVar(value=prefs.get_auto_open())
        self.auto_export_var = tk.BooleanVar(value=prefs.get_auto_export())
        self.export_pdf_var = tk.BooleanVar(value=prefs.get_export_pdf())
        self.export_pdfx_var = tk.BooleanVar(value=prefs.get_export_pdfx())
        self.export_png_var = tk.BooleanVar(value=prefs.get_export_png())
        self.other_export_format_var = tk.StringVar(value=prefs.get_export_other_format())
        self.other_export_pages_var = tk.StringVar(value=prefs.get_export_other_pages())
        self.pdf_raster_mode_var = tk.StringVar(value=prefs.get_pdf_raster_mode())
        self.export_dpi_var = tk.StringVar(value=str(prefs.get_export_dpi()))
        self.export_jpeg_quality_var = tk.StringVar(value=str(prefs.get_export_jpeg_quality()))
        self.split_svg_output_var = tk.BooleanVar(value=prefs.get_split_svg_output())
        self.split_svg_chunk_mb_var = tk.StringVar(value=str(prefs.get_split_svg_chunk_mb()))
        self._pdf_profile_names = ("default", "screen", "ebook", "printer", "prepress")
        self.pdf_profile_vars = {key: tk.BooleanVar(value=False) for key in self._pdf_profile_names}
        self._icc_profiles = ICC.display_choices()
        self.pdf_cmyk_icc_var = tk.StringVar(value=ICC.display_value(prefs.get_pdf_cmyk_icc()))
        self.pdf_cmyk_pure_black_text_var = tk.BooleanVar(value=prefs.get_pdf_cmyk_pure_black_text())
        self.pdfx_version_var = tk.StringVar(value=self._pdfx_label_from_value(prefs.get_pdfx_version()))
        self._warm_sheet_id = ""
        self._request_serial = 0
        self._autorun_serial = 0
        self._run_started_at: float | None = None
        self._post_create_busy = False
        self._render_rows_total = 0
        self._active_progress_started_at: float | None = None
        self._active_progress_rate_unit = ""
        self._web_activity_stats = self._reset_web_activity_stats()
        try:
            TEMPPATHS.cleanup_runs_now()
        except Exception:
            pass

        self._build_ui()
        self._load_pdf_profile_prefs()
        self._refresh_export_button_state()
        if initial:
            self._set_request(initial)
        IPC.start_server(self._queue, self._server_stop)
        self.root.after(150, self._drain_queue)
        self.root.after(80, self._drain_ui_queue)
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

        deck_tab = ttk.Frame(notebook, padding=12)
        deck_tab.columnconfigure(0, weight=1)
        deck_tab.rowconfigure(1, weight=1)
        notebook.add(deck_tab, text="Deck")

        pdf_tab = ttk.Frame(notebook, padding=12)
        pdf_tab.columnconfigure(0, weight=1)
        notebook.add(pdf_tab, text="Export")

        other_tab = ttk.Frame(notebook, padding=12)
        other_tab.columnconfigure(0, weight=1)
        notebook.add(other_tab, text="Preferences")

        about_tab = ttk.Frame(notebook, padding=12)
        about_tab.columnconfigure(0, weight=1)
        notebook.add(about_tab, text="About")

        source_row = ttk.Frame(deck_tab)
        source_row.grid(row=0, column=0, sticky="ew", pady=4)
        source_row.columnconfigure(1, weight=2)
        source_row.columnconfigure(3, weight=3)
        source_row.columnconfigure(5, weight=2)
        gsheet_label = ttk.Label(source_row, text="GSheet ID")
        gsheet_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        gsheet_entry = ttk.Entry(source_row, textvariable=self.sheet_id_var, width=24)
        gsheet_entry.grid(row=0, column=1, sticky="ew")
        range_label = ttk.Label(source_row, text="Range / gid")
        range_label.grid(row=0, column=2, sticky="w", padx=(12, 8))
        range_entry = ttk.Entry(source_row, textvariable=self.sheet_range_var, width=24)
        range_entry.grid(row=0, column=3, sticky="ew")
        ttk.Label(source_row, text="Source").grid(row=0, column=4, sticky="w", padx=(12, 8))
        source_combo = ttk.Combobox(
            source_row,
            textvariable=self.source_mode_var,
            values=SOURCE_MODE_LABELS,
            state="readonly",
            width=24,
        )
        source_combo.grid(row=0, column=5, sticky="ew")
        source_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_source_mode_changed())
        GUI.attach_tooltip(
            gsheet_label,
            "Google Sheets spreadsheet ID from the sheet URL. Leave empty to use a CSV next to the SVG.",
        )
        GUI.attach_tooltip(
            gsheet_entry,
            "Google Sheets spreadsheet ID from the sheet URL. Leave empty to use a CSV next to the SVG.",
        )
        GUI.attach_tooltip(
            range_label,
            "A1 range, sheet title, or gid. Leave empty for OAuth/default sheet discovery.",
        )
        GUI.attach_tooltip(
            range_entry,
            "A1 range, sheet title, or gid. Leave empty for OAuth/default sheet discovery.",
        )
        GUI.attach_tooltip(
            source_combo,
            "Dataset source detected at startup. Choose local CSV, OAuth, or public Google Sheet to force a mode.",
        )

        log_font = tkfont.nametofont("TkFixedFont").copy()
        try:
            log_font.configure(size=max(8, int(log_font.cget("size")) - 1))
        except Exception:
            pass

        buttons = ttk.LabelFrame(frame, text="Actions:", padding=8)
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(6, weight=1)
        auto_create_cb = ttk.Checkbutton(buttons, text="", variable=self.auto_create_var, command=self._on_auto_prefs_changed)
        auto_create_cb.grid(row=0, column=0, sticky="w", padx=(0, 2))
        self.run_btn = ttk.Button(buttons, text="Generate", command=self._run_clicked)
        self.run_btn.grid(row=0, column=1, sticky="w", padx=(0, 12))
        auto_open_cb = ttk.Checkbutton(buttons, text="", variable=self.auto_open_var, command=self._on_auto_prefs_changed)
        auto_open_cb.grid(row=0, column=2, sticky="w", padx=(0, 2))
        self.open_btn = ttk.Button(buttons, text="Open SVG", command=self._open_output_clicked)
        self.open_btn.grid(row=0, column=3, sticky="w", padx=(0, 12))
        auto_export_cb = ttk.Checkbutton(buttons, text="", variable=self.auto_export_var, command=self._on_auto_prefs_changed)
        auto_export_cb.grid(row=0, column=4, sticky="w", padx=(0, 2))
        self.pdf_btn = ttk.Button(buttons, text="Export", command=self._export_clicked)
        self.pdf_btn.grid(row=0, column=5, sticky="w")
        ttk.Label(buttons, textvariable=self.status_var).grid(row=0, column=6, sticky="w", padx=(12, 0))
        GUI.attach_tooltip(auto_create_cb, "Auto-start generate")
        GUI.attach_tooltip(auto_open_cb, "Auto-start Open SVG")
        GUI.attach_tooltip(auto_export_cb, "Auto-start export")
        self.log_text = scrolledtext.ScrolledText(deck_tab, height=13, wrap="word", state="disabled", font=log_font)
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        try:
            self.log_text.tag_configure("live_activity", foreground="#555555")
        except Exception:
            pass

        pdf_export_toggle = ttk.Checkbutton(
            pdf_tab,
            text="PDF export",
            variable=self.export_pdf_var,
            command=self._on_export_format_prefs_changed,
        )
        pdf_profiles_box = ttk.LabelFrame(pdf_tab, labelwidget=pdf_export_toggle, padding=8)
        pdf_profiles_box.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        profiles_row = ttk.Frame(pdf_profiles_box)
        profiles_row.grid(row=0, column=0, sticky="w")
        ttk.Label(profiles_row, text="PDF output profiles:").grid(row=0, column=0, sticky="w", padx=(0, 10))
        for idx, key in enumerate(self._pdf_profile_names, start=1):
            cb = ttk.Checkbutton(
                profiles_row,
                text=key,
                variable=self.pdf_profile_vars[key],
                command=self._on_pdf_profiles_changed,
            )
            cb.grid(row=0, column=idx, sticky="w", padx=(0, 10))

        pdfx_toggle = ttk.Checkbutton(
            pdf_tab,
            text="PDF/X export (CMYK)",
            variable=self.export_pdfx_var,
            command=self._on_export_format_prefs_changed,
        )
        cmyk_box = ttk.LabelFrame(pdf_tab, labelwidget=pdfx_toggle, padding=8)
        cmyk_box.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        cmyk_box.columnconfigure(1, weight=1)
        ttk.Label(cmyk_box, text="ICC").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.pdf_cmyk_icc_combo = ttk.Combobox(
            cmyk_box,
            textvariable=self.pdf_cmyk_icc_var,
            values=self._icc_profiles,
            state="readonly",
        )
        self.pdf_cmyk_icc_combo.grid(row=0, column=1, sticky="ew")
        self.pdf_cmyk_icc_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_pdf_profiles_changed())
        ttk.Label(cmyk_box, text="PDF/X").grid(row=0, column=2, sticky="w", padx=(12, 6))
        self.pdfx_version_combo = ttk.Combobox(
            cmyk_box,
            textvariable=self.pdfx_version_var,
            values=("PDF/X-1a", "PDF/X-3", "PDF/X-4 (supports transparencies)"),
            state="readonly",
            width=28,
        )
        self.pdfx_version_combo.grid(row=0, column=3, sticky="w")
        self.pdfx_version_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_pdf_profiles_changed())
        ttk.Checkbutton(
            cmyk_box,
            text="Text in pure black",
            variable=self.pdf_cmyk_pure_black_text_var,
            command=self._on_pdf_profiles_changed,
        ).grid(row=0, column=4, sticky="w", padx=(12, 0))

        options_box = ttk.LabelFrame(pdf_tab, text="PDF options", padding=8)
        options_box.grid(row=2, column=0, sticky="ew", padx=(18, 0), pady=(0, 12))
        options_row = ttk.Frame(options_box)
        options_row.grid(row=0, column=0, sticky="w")
        ttk.Label(options_row, text="Raster filters:").grid(row=0, column=0, sticky="w", padx=(0, 10))
        raster_modes = (
            ("png", "png"),
            ("jpeg", "jpeg"),
            ("png_alpha", "png_alfa"),
            ("inkscape", "inkscape"),
            ("none", "none"),
        )
        for idx, (value, label) in enumerate(raster_modes, start=1):
            ttk.Radiobutton(
                options_row,
                text=label,
                value=value,
                variable=self.pdf_raster_mode_var,
                command=self._on_pdf_export_prefs_changed,
            ).grid(row=0, column=idx, sticky="w", padx=(0, 10))

        other_toggle = ttk.Checkbutton(
            pdf_tab,
            text="Other formats",
            variable=self.export_png_var,
            command=self._on_export_format_prefs_changed,
        )
        format_box = ttk.LabelFrame(pdf_tab, labelwidget=other_toggle, padding=8)
        format_box.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        format_box.columnconfigure(3, weight=1)
        ttk.Label(format_box, text="Format").grid(row=0, column=0, sticky="w", padx=(0, 8))
        other_format_combo = ttk.Combobox(
            format_box,
            textvariable=self.other_export_format_var,
            values=list(OTHER_EXPORT_FORMATS),
            state="readonly",
            width=12,
        )
        other_format_combo.grid(row=0, column=1, sticky="w")
        other_format_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_export_format_prefs_changed())
        ttk.Label(format_box, text="Pages").grid(row=0, column=2, sticky="w", padx=(12, 8))
        pages_entry = ttk.Entry(format_box, textvariable=self.other_export_pages_var, width=18)
        pages_entry.grid(row=0, column=3, sticky="w")
        ttk.Label(format_box, text="e.g. 1,3-5,8 (empty = all)").grid(row=0, column=4, sticky="w", padx=(8, 0))
        pages_entry.bind("<FocusOut>", lambda _e: self._on_export_format_prefs_changed())
        pages_entry.bind("<Return>", lambda _e: self._on_export_format_prefs_changed())

        export_options_box = ttk.LabelFrame(pdf_tab, text="Export Options", padding=8)
        export_options_box.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(export_options_box, text="DPI").grid(row=0, column=0, sticky="w", padx=(0, 8))
        dpi_spin = ttk.Spinbox(
            export_options_box,
            from_=1,
            to=2400,
            increment=50,
            textvariable=self.export_dpi_var,
            width=8,
            command=self._on_export_dpi_changed,
        )
        dpi_spin.grid(row=0, column=1, sticky="w")
        dpi_spin.bind("<FocusOut>", lambda _e: self._on_export_dpi_changed())
        dpi_spin.bind("<Return>", lambda _e: self._on_export_dpi_changed())
        ttk.Label(export_options_box, text="JPEG quality").grid(row=0, column=2, sticky="w", padx=(14, 8))
        jpeg_quality_spin = ttk.Spinbox(
            export_options_box,
            from_=70,
            to=95,
            increment=1,
            textvariable=self.export_jpeg_quality_var,
            width=8,
            command=self._on_export_jpeg_quality_changed,
        )
        jpeg_quality_spin.grid(row=0, column=3, sticky="w")
        jpeg_quality_spin.bind("<FocusOut>", lambda _e: self._on_export_jpeg_quality_changed())
        jpeg_quality_spin.bind("<Return>", lambda _e: self._on_export_jpeg_quality_changed())

        split_box = ttk.LabelFrame(other_tab, text="SVG Parts", padding=8)
        split_box.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        split_box.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            split_box,
            text="Split SVG",
            variable=self.split_svg_output_var,
            command=self._on_svg_chunk_prefs_changed,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(split_box, text="Part target MB").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
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

        about_text = (
            "PnPInk DeckMaker\n\n"
            "PnPInk is an open-source extension suite for Inkscape that turns it into a practical "
            "production environment for print-and-play components: cards, tiles, counters, boards, "
            "and player aids.\n\n"
            "You design with normal SVG objects, and PnPInk handles replication, data filling, "
            "placement, and pagination automatically. The output remains editable SVG, and you can "
            "export using Inkscape formats such as PDF, PNG, JPG, and SVG."
        )
        ttk.Label(about_tab, text=about_text, justify="left", anchor="nw", wraplength=700).grid(row=0, column=0, sticky="nw")

        help_box = ttk.LabelFrame(about_tab, text="Help", padding=8)
        help_box.grid(row=1, column=0, sticky="ew", pady=(12, 12))
        ttk.Button(help_box, text="Open Intro", command=lambda: self._open_url_in_system(DOCS_INTRO_URL)).grid(row=0, column=0, sticky="w")
        ttk.Button(help_box, text="Open Guide", command=lambda: self._open_url_in_system(DOCS_GUIDE_URL)).grid(row=0, column=1, sticky="w", padx=(8, 0))

        examples_box = ttk.LabelFrame(about_tab, text="Examples", padding=8)
        examples_box.grid(row=2, column=0, sticky="ew")
        ttk.Label(
            examples_box,
            text="The examples folder contains ready-to-open templates, sample datasets, and generated outputs for testing the full workflow.",
            justify="left",
            wraplength=700,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(examples_box, text="Open Examples Folder", command=self._open_examples_folder).grid(row=1, column=0, sticky="w", pady=(8, 0))

        progress_wrap = tk.Frame(frame, height=6, bd=0, highlightthickness=0)
        progress_wrap.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        progress_wrap.grid_propagate(False)
        progress_wrap.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_wrap, mode="indeterminate", style="Thin.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=0, sticky="ew")

    def _apply_window_icon(self):
        icon_path = DMPATHS.app_icon(os.path.dirname(__file__))
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
            self._clear_live_activity_locked()
            prefix = f"[{stamp}] " if stamp != self._last_log_stamp else " " * 11
            self.log_text.insert("end-1c", f"{prefix}{text}\n")
            self._last_log_stamp = stamp
            self._apply_live_activity_locked()
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _refresh_window_title(self):
        try:
            template = _normalize_path(self.template_var.get()) if hasattr(self, "template_var") else ""
            name = os.path.basename(template) if template else ""
            self.root.title(f"{APP_VERSION} - {name}" if name else APP_VERSION)
        except Exception:
            try:
                self.root.title(APP_VERSION)
            except Exception:
                pass

    def _set_activity(self, message: str):
        text = str(message or "").strip()
        try:
            self.log_text.configure(state="normal")
            self._live_activity_text = text
            self._clear_live_activity_locked()
            self._apply_live_activity_locked()
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _clear_live_activity_locked(self):
        try:
            ranges = self.log_text.tag_ranges("live_activity")
            if len(ranges) >= 2:
                self.log_text.delete(ranges[0], ranges[1])
            self.log_text.tag_remove("live_activity", "1.0", "end")
        except Exception:
            pass

    def _apply_live_activity_locked(self):
        text = str(self._live_activity_text or "").strip()
        if not text:
            return
        try:
            start = self.log_text.index("end-1c")
            self.log_text.insert("end-1c", f"> {text}")
            end = self.log_text.index("end-1c")
            self.log_text.tag_add("live_activity", start, end)
        except Exception:
            pass

    def _queue_ui_log(self, message: str):
        try:
            self._ui_queue.put(("log", str(message or "")))
        except Exception:
            pass

    def _queue_ui_activity(self, message: str):
        try:
            self._ui_queue.put(("activity", str(message or "")))
        except Exception:
            pass

    def _set_activity_progress(self, label: str, current: int, total: int):
        try:
            self._ui_queue.put(("progress", (str(label or "").strip(), int(current or 0), int(total or 0))))
        except Exception:
            pass

    def _progress_rate_suffix(self, label: str, current: int) -> str:
        unit = str(getattr(self, "_active_progress_rate_unit", "") or "").strip()
        if unit != "records":
            return ""
        started = getattr(self, "_active_progress_started_at", None)
        if started is None:
            return ""
        elapsed = max(0.0, time.perf_counter() - float(started))
        if elapsed <= 0.25 or int(current or 0) <= 0:
            return ""
        rate = (float(current) * 60.0) / elapsed
        return f"  {rate:,.0f} records/min"

    def _make_process_output_activity(self, label: str):
        prefix = str(label or "Process").strip() or "Process"
        buffer = {"text": ""}

        def on_output(chunk: str):
            text = str(chunk or "")
            if not text:
                return
            buffer["text"] += text
            while "\n" in buffer["text"] or "\r" in buffer["text"]:
                line, sep, rest = buffer["text"].partition("\n")
                if not sep:
                    line, _sep, rest = buffer["text"].partition("\r")
                buffer["text"] = rest
                item = line.strip()
                if item:
                    self._queue_ui_activity(f"{prefix}: {item}")

        return on_output

    def _handle_progress_update(self, label: str, current: int, total: int):
        text_label = str(label or "").strip()
        if not text_label:
            return
        if text_label != self._active_progress_label:
            if self._active_progress_label:
                self._commit_activity_to_log()
            self._active_progress_label = text_label
            self._active_progress_started_at = time.perf_counter()
            self._active_progress_rate_unit = "records" if text_label.lower().startswith("generating records") else ""
            self._log(text_label)
        progress = GUI.progress_text("", current, total, width=50)
        self._set_activity(progress + self._progress_rate_suffix(text_label, current))
        if int(total or 0) > 0 and int(current or 0) >= int(total or 0):
            self._commit_activity_to_log()
            self._active_progress_label = ""
            self._active_progress_started_at = None
            self._active_progress_rate_unit = ""

    def _commit_activity_to_log(self):
        text = str(self._live_activity_text or "").strip()
        if not text:
            return
        try:
            self.log_text.configure(state="normal")
            self._clear_live_activity_locked()
            self._live_activity_text = ""
            self.log_text.configure(state="disabled")
        except Exception:
            pass
        self._log(text)

    def _reset_web_activity_stats(self) -> dict[str, int]:
        return {
            "wkmc_fetched": 0,
            "web_cached": 0,
            "wkmc_failed": 0,
            "raster_jobs": 0,
            "raster_chunks": 0,
            "raster_job_total": 0,
            "raster_job_done": 0,
            "svg_parts_total": 0,
            "svg_parts_done": 0,
            "pdf_parts_total": 0,
            "pdf_parts_done": 0,
            "png_parts_total": 0,
            "png_parts_done": 0,
            "pdf_merge_total": 0,
            "pdf_merge_done": 0,
            "last_pdf_logged": 0,
            "last_png_logged": 0,
            "last_raster_logged": 0,
        }

    def _start_web_activity_monitor(self):
        self._stop_web_activity_monitor()
        self._web_activity_stats = self._reset_web_activity_stats()

        def _set_activity(msg: str):
            self._queue_ui_activity(msg)

        def _set_progress(label: str, current: int, total: int):
            self._set_activity_progress(label, current, total)

        def process_line(raw_line: str):
            line = str(raw_line or "").strip()
            if not line:
                return
            m = re.search(r"\[datasets\].*placed=(\d+)\s+cards; end_page=(\d+)", line)
            if m:
                _set_activity(f"Generated {int(m.group(1))} cards across {int(m.group(2))} page(s)")
                return
            if "[svg_chunks] plan total=" in line:
                m = re.search(r"plan total=\s*(\d+)", line)
                if m:
                    self._web_activity_stats["svg_parts_total"] = int(m.group(1))
                    self._web_activity_stats["svg_parts_done"] = 0
                    _set_progress("Creating SVG parts", 0, int(m.group(1)))
                return
            if "[svg_chunks] wrote chunk=" in line:
                total = int(self._web_activity_stats.get("svg_parts_total") or 0)
                if total > 0:
                    self._web_activity_stats["svg_parts_done"] += 1
                    done = int(self._web_activity_stats.get("svg_parts_done") or 0)
                    _set_progress("Creating SVG parts", min(done, total), total)
                else:
                    _set_activity("Creating SVG parts...")
                return
            if "[export.pdf] chunks_ready" in line:
                m = re.search(r"count=\s*(\d+)", line)
                if m:
                    self._web_activity_stats["pdf_parts_total"] = int(m.group(1))
                    self._web_activity_stats["pdf_parts_done"] = 0
                    _set_progress("Preparing PDF parts", 0, int(m.group(1)))
                return
            if "[export.png] chunks_ready" in line:
                m = re.search(r"count=\s*(\d+)", line)
                if m:
                    self._web_activity_stats["png_parts_total"] = int(m.group(1))
                    self._web_activity_stats["png_parts_done"] = 0
                    _set_progress("Preparing PNG parts", 0, int(m.group(1)))
                return
            if "[sources] wkmc batch prefetched" in line:
                msg = line.split("[sources] wkmc batch prefetched", 1)[-1].strip()
                _set_activity(f"Wikimedia batch prefetch {msg}")
                return
            if "[sources] virtual prefetch scheduled:" in line:
                m = re.search(r"virtual prefetch scheduled:\s*(\d+)\s*expr", line)
                if m:
                    _set_activity(f"Preparing {m.group(1)} web source expression(s)...")
                return
            if "[sources] web prefetch scheduled:" in line:
                m = re.search(r"web prefetch scheduled:\s*(\d+)\s*url", line)
                if m:
                    _set_activity(f"Preparing {m.group(1)} direct web download(s)...")
                return
            if "[sources] wkmc fetched query=" in line:
                self._web_activity_stats["wkmc_fetched"] += 1
                n = int(self._web_activity_stats["wkmc_fetched"])
                if n <= 3 or (n % 10) == 0:
                    _set_activity(f"Wikimedia resolved {n} item(s)...")
                return
            if "[sources] web cached ->" in line:
                self._web_activity_stats["web_cached"] += 1
                n = int(self._web_activity_stats["web_cached"])
                if n <= 3 or (n % 5) == 0:
                    _set_activity(f"Downloaded {n} web asset(s)...")
                return
            if "[sources] wkmc fetch failed" in line:
                self._web_activity_stats["wkmc_failed"] += 1
                n = int(self._web_activity_stats["wkmc_failed"])
                if n <= 3 or (n % 5) == 0:
                    _set_activity(f"Wikimedia retries/failures: {n}")
                return
            if "[raster] export-id png jobs=" in line:
                m = re.search(r"export-id png jobs=%d ids=%s\s+(\d+)", line)
                if not m:
                    m = re.search(r"export-id png jobs=\s*(\d+)", line)
                if m:
                    jobs = int(m.group(1))
                    self._web_activity_stats["raster_jobs"] += jobs
                    self._web_activity_stats["raster_job_total"] = jobs
                    self._web_activity_stats["raster_job_done"] = 0
                    if jobs > 0:
                        _set_progress("Creating rasters for complex filters", 0, jobs)
                return
            if "[export.pdf] chunk_raster" in line:
                self._web_activity_stats["raster_chunks"] += 1
                m = re.search(r"rasterized_filters=%d.*?\s(\d+)\s", line)
                if not m:
                    m = re.search(r"rasterized_filters=\s*(\d+)", line)
                total = int(self._web_activity_stats.get("pdf_parts_total") or 0)
                done = int(self._web_activity_stats.get("raster_chunks") or 0)
                if total > 0:
                    _set_progress("Rasterizing PDF parts", min(done, total), total)
                    self._web_activity_stats["last_raster_logged"] = done
                elif m:
                    _set_activity(
                        f"Raster part {self._web_activity_stats['raster_chunks']} done: {int(m.group(1))} filter node(s)"
                    )
                else:
                    _set_activity(f"Raster part {self._web_activity_stats['raster_chunks']} done")
                return
            if "[export.pdf] chunk_done" in line:
                self._web_activity_stats["pdf_parts_done"] += 1
                total = int(self._web_activity_stats.get("pdf_parts_total") or 0)
                done = int(self._web_activity_stats.get("pdf_parts_done") or 0)
                if total > 0:
                    _set_progress("Exporting PDF parts", min(done, total), total)
                    self._web_activity_stats["last_pdf_logged"] = done
                return
            if "[export.png] chunk_done" in line:
                self._web_activity_stats["png_parts_done"] += 1
                total = int(self._web_activity_stats.get("png_parts_total") or 0)
                done = int(self._web_activity_stats.get("png_parts_done") or 0)
                if total > 0:
                    _set_progress("Exporting PNG parts", min(done, total), total)
                    self._web_activity_stats["last_png_logged"] = done
                return
            if "[export.pdf] merge_profiles total=" in line:
                m = re.search(r"merge_profiles total=\s*(\d+)", line)
                if m:
                    self._web_activity_stats["pdf_merge_total"] = int(m.group(1))
                    self._web_activity_stats["pdf_merge_done"] = 0
                    _set_progress("Merging PDF profiles", 0, int(m.group(1)))
                return
            if "[export.pdf] merge profile=" in line:
                total = int(self._web_activity_stats.get("pdf_merge_total") or 0)
                if total > 0:
                    self._web_activity_stats["pdf_merge_done"] += 1
                    done = int(self._web_activity_stats.get("pdf_merge_done") or 0)
                    _set_progress("Merging PDF profiles", min(done, total), total)
                else:
                    _set_activity("Merging PDF profiles...")
                return

        self._activity_listener = process_line
        try:
            LOG.add_listener(self._activity_listener)
        except Exception:
            self._activity_listener = None

    def _stop_web_activity_monitor(self):
        if self._activity_listener is not None:
            try:
                LOG.remove_listener(self._activity_listener)
            except Exception:
                pass
            self._activity_listener = None

    def _start_progress_listener(self):
        self._stop_progress_listener()
        self._render_rows_total = 0

        def on_progress(kind: str, payload: dict):
            if kind == "render_rows_total":
                try:
                    self._render_rows_total = int(payload.get("total") or 0)
                except Exception:
                    self._render_rows_total = 0
                if self._render_rows_total > 0:
                    dataset_count = int(payload.get("dataset_count") or 0)
                    dataset_index = int(payload.get("dataset_index") or 0)
                    label = (
                        f"Generating records dataset {dataset_index}/{dataset_count}"
                        if dataset_count > 1 and dataset_index > 0
                        else "Generating records"
                    )
                    self._set_activity_progress(label, 0, self._render_rows_total)
                return
            if kind == "render_row":
                try:
                    current = int(payload.get("current") or 0)
                except Exception:
                    current = 0
                if current <= 0:
                    return
                total = int(payload.get("total") or self._render_rows_total or 0)
                if total > 0:
                    dataset_count = int(payload.get("dataset_count") or 0)
                    dataset_index = int(payload.get("dataset_index") or 0)
                    label = (
                        f"Generating records dataset {dataset_index}/{dataset_count}"
                        if dataset_count > 1 and dataset_index > 0
                        else "Generating records"
                    )
                    self._set_activity_progress(label, current, total)
                else:
                    self._queue_ui_activity(f"Generating record {current}...")

        self._progress_listener = on_progress
        GUI.set_listener(self._progress_listener)

    def _stop_progress_listener(self):
        if self._progress_listener is not None:
            GUI.clear_listener(self._progress_listener)
            self._progress_listener = None

    def _load_pdf_profile_prefs(self):
        selected = set(prefs.get_pdf_profiles())
        prefs.set_pdf_profiles([key for key in self._pdf_profile_names if key in selected])
        for key, var in self.pdf_profile_vars.items():
            var.set(key in selected)
        if not self.pdf_cmyk_icc_var.get().strip():
            try:
                self.pdf_cmyk_icc_var.set(ICC.display_value(""))
            except Exception:
                pass

    @staticmethod
    def _pdfx_label_from_value(value: str) -> str:
        item = str(value or "3").strip().lower()
        if item in {"1", "1a", "pdf/x-1a"}:
            return "PDF/X-1a"
        if item in {"4", "pdf/x-4"}:
            return "PDF/X-4 (supports transparencies)"
        return "PDF/X-3"

    @staticmethod
    def _pdfx_value_from_label(value: str) -> str:
        item = str(value or "PDF/X-3").strip().lower()
        if "1a" in item:
            return "1a"
        if "4" in item:
            return "4"
        return "3"

    @staticmethod
    def _source_mode_label(value: str) -> str:
        return SOURCE_MODE_VALUE_TO_LABEL.get(str(value or "").strip().lower(), "(empty)")

    def _source_mode_value(self) -> str:
        return SOURCE_MODE_LABEL_TO_VALUE.get(str(self.source_mode_var.get() or "").strip(), "")

    def _detect_source_mode(self, template: str, sheet_id: str, explicit: str = "") -> str:
        mode = str(explicit or "").strip().lower()
        if mode and mode in SOURCE_MODE_VALUE_TO_LABEL:
            return mode
        template = _normalize_path(template)
        sheet_id = str(sheet_id or "").strip()
        if sheet_id:
            try:
                import dataset_state as DSTATE

                rec = DSTATE.get_gsheet_for_svg(template) or {}
                if str(rec.get("sheet_id") or "").strip() == sheet_id:
                    access_mode = str(rec.get("access_mode") or "").strip().lower()
                    if access_mode in {"oauth", "public"}:
                        return access_mode
            except Exception:
                pass
            return ""
        if template:
            csv_path = os.path.splitext(template)[0] + ".csv"
            if os.path.isfile(csv_path):
                return "local_csv"
        return ""

    def _refresh_source_mode(self, template: str = "", explicit: str = "") -> None:
        detected = self._detect_source_mode(
            template or self.template_var.get(),
            self.sheet_id_var.get().strip(),
            explicit,
        )
        self.source_mode_var.set(self._source_mode_label(detected))

    def _on_source_mode_changed(self) -> None:
        self._log(f"Dataset source mode = {self.source_mode_var.get()}")

    def _refresh_icc_profile_choices(self):
        current = ICC.preference_value(self.pdf_cmyk_icc_var.get())
        self._icc_profiles = ICC.display_choices()
        try:
            self.pdf_cmyk_icc_combo.configure(values=self._icc_profiles)
        except Exception:
            pass
        self.pdf_cmyk_icc_var.set(ICC.display_value(current))

    def _selected_pdf_profiles(self) -> list[str]:
        return [key for key, var in self.pdf_profile_vars.items() if bool(var.get())]

    def _selected_pdf_profiles_for_export(self, options: ExportOptions | None = None) -> list[str]:
        export_standard = bool(options.export_pdf_standard) if options is not None else bool(self.export_pdf_var.get())
        export_pdfx = bool(options.export_pdfx) if options is not None else bool(self.export_pdfx_var.get())
        selected = list(options.pdf_profiles) if options is not None else self._selected_pdf_profiles()
        out: list[str] = []
        if export_standard:
            standard = [key for key in self._pdf_profile_names if key in selected]
            out.extend(standard or ["default"])
        if export_pdfx and "cmyk" not in out:
            out.append("cmyk")
        return out or ["default"]

    def _on_pdf_profiles_changed(self):
        selected = self._selected_pdf_profiles()
        prefs.set_pdf_profiles(selected)
        prefs.set_pdf_cmyk_icc(ICC.preference_value(self.pdf_cmyk_icc_var.get()))
        prefs.set_pdf_cmyk_pure_black_text(bool(self.pdf_cmyk_pure_black_text_var.get()))
        prefs.set_pdfx_version(self._pdfx_value_from_label(self.pdfx_version_var.get()))
        self._log(f"Preference saved: PDF profiles = {', '.join(selected or ['none'])}")

    def _on_pdf_export_prefs_changed(self):
        prefs.set_pdf_raster_mode(self.pdf_raster_mode_var.get())
        self._log(
            "Preference saved: PDF raster mode = "
            f"{self.pdf_raster_mode_var.get()}"
        )

    def _on_export_dpi_changed(self):
        try:
            export_dpi = int(float(str(self.export_dpi_var.get() or "").strip()))
        except Exception:
            export_dpi = prefs.get_export_dpi()
        export_dpi = max(1, min(export_dpi, 2400))
        self.export_dpi_var.set(str(export_dpi))
        prefs.set_export_dpi(export_dpi)
        self._log(f"Preference saved: export DPI = {export_dpi}")

    def _on_export_jpeg_quality_changed(self):
        try:
            quality = int(float(str(self.export_jpeg_quality_var.get() or "").strip()))
        except Exception:
            quality = prefs.get_export_jpeg_quality()
        quality = max(70, min(quality, 95))
        self.export_jpeg_quality_var.set(str(quality))
        prefs.set_export_jpeg_quality(quality)
        self._log(f"Preference saved: JPEG quality = {quality}")

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
            "Preference saved: split SVG into parts = "
            f"{'on' if self.split_svg_output_var.get() else 'off'}, "
            f"part_mb={chunk_mb}"
        )

    def _selected_export_formats(self) -> list[str]:
        out: list[str] = []
        if bool(self.export_pdf_var.get()) or bool(self.export_pdfx_var.get()):
            out.append("pdf")
        if bool(self.export_png_var.get()):
            out.append(str(self.other_export_format_var.get() or "png").strip().lower() or "png")
        return out

    def _refresh_export_button_state(self):
        try:
            enabled = bool(self._selected_export_formats())
            self.pdf_btn.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _export_dpi_value(self) -> int:
        try:
            value = int(float(str(self.export_dpi_var.get() or prefs.get_export_dpi()).strip()))
        except Exception:
            value = prefs.get_export_dpi()
        return max(1, min(value, 2400))

    def _export_jpeg_quality_value(self) -> int:
        try:
            value = int(float(str(self.export_jpeg_quality_var.get() or prefs.get_export_jpeg_quality()).strip()))
        except Exception:
            value = prefs.get_export_jpeg_quality()
        return max(70, min(value, 95))

    def _export_options_snapshot(self) -> ExportOptions:
        return ExportOptions(
            formats=tuple(self._selected_export_formats()),
            pdf_profiles=tuple(self._selected_pdf_profiles()),
            export_pdf_standard=bool(self.export_pdf_var.get()),
            export_pdfx=bool(self.export_pdfx_var.get()),
            pdf_raster_mode=str(self.pdf_raster_mode_var.get() or "png").strip().lower(),
            pdf_cmyk_icc=ICC.preference_value(self.pdf_cmyk_icc_var.get()),
            pdf_cmyk_pure_black_text=bool(self.pdf_cmyk_pure_black_text_var.get()),
            pdfx_version=self._pdfx_value_from_label(self.pdfx_version_var.get()),
            export_dpi=self._export_dpi_value(),
            jpeg_quality=self._export_jpeg_quality_value(),
            other_format=str(self.other_export_format_var.get() or "png").strip().lower(),
            other_pages=str(self.other_export_pages_var.get() or "").strip(),
        )

    def _on_export_format_prefs_changed(self):
        prefs.set_export_pdf(bool(self.export_pdf_var.get()))
        prefs.set_export_pdfx(bool(self.export_pdfx_var.get()))
        prefs.set_export_png(bool(self.export_png_var.get()))
        prefs.set_pdfx_version(self._pdfx_value_from_label(self.pdfx_version_var.get()))
        prefs.set_export_other_format(self.other_export_format_var.get())
        prefs.set_export_other_pages(self.other_export_pages_var.get())
        labels = []
        if self.export_pdf_var.get():
            labels.append("PDF")
        if self.export_pdfx_var.get():
            labels.append("PDF/X")
        if self.export_png_var.get():
            labels.append(str(self.other_export_format_var.get() or "png").upper())
        self._refresh_export_button_state()
        self._log(f"Preference saved: output formats = {', '.join(labels) if labels else 'none'}")

    def _on_auto_prefs_changed(self):
        prefs.set_auto_create(bool(self.auto_create_var.get()))
        prefs.set_auto_open(bool(self.auto_open_var.get()))
        prefs.set_auto_export(bool(self.auto_export_var.get()))
        self._log(
            "Preference saved: automation = "
            f"generate={'on' if self.auto_create_var.get() else 'off'}, "
            f"open={'on' if self.auto_open_var.get() else 'off'}, "
            f"export={'on' if self.auto_export_var.get() else 'off'}"
        )

    def _log_image_dpi_preflight(self, svg_path_or_report):
        report = svg_path_or_report if isinstance(svg_path_or_report, dict) else PREFLIGHT.effective_image_dpi_report(str(svg_path_or_report))
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
        return DMPATHS.output_svg(self.template_var.get())

    def _output_pdf_path(self) -> str:
        return DMPATHS.output_pdf(self.template_var.get())

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

    def _open_url_in_system(self, url: str) -> None:
        if not str(url or "").strip():
            raise FileNotFoundError("missing url")
        webbrowser.open(str(url), new=2)

    def _open_examples_folder(self) -> None:
        self._open_path_in_system(DMPATHS.examples_dir(os.path.dirname(__file__)))

    def _open_output_clicked(self) -> None:
        svg_path = self._output_svg_path()
        open_target = svg_path if os.path.isfile(svg_path) else ""
        if not open_target or not os.path.isfile(open_target):
            self.status_var.set("Generate output first")
            self._log("Generate output first")
            return
        try:
            self._open_svg_in_inkscape(open_target)
            self.status_var.set("Opened output")
            self._log(f"Opened output: {os.path.basename(open_target)}")
        except Exception as ex:
            self.status_var.set("Open failed")
            self._log(f"Open failed: {ex}")

    def _open_svg_in_inkscape(self, svg_path: str) -> None:
        target = _normalize_path(svg_path)
        if not target or not os.path.isfile(target):
            raise FileNotFoundError(target or "missing svg")
        exe = INKSCAPE.find_executable()
        if not exe:
            self._open_path_in_system(target)
            return
        kwargs = {
            "args": [exe, target],
            "cwd": os.path.dirname(target) or None,
            "env": INKSCAPE.clean_launch_env(isolated_profile=False),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        subprocess.Popen(**kwargs)

    def _set_request(self, req: AppRequest):
        self._request_serial += 1
        serial = self._request_serial
        self.template_var.set(_normalize_path(req.template))
        self._refresh_window_title()
        self.sheet_id_var.set(req.sheet_id or "")
        self.sheet_range_var.set(req.sheet_range or "")
        self._refresh_source_mode(req.template, req.dataset_source_mode)
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
        if self._source_mode_value() == "local_csv":
            return
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

                self._queue_ui_activity("Checking Google Sheets session...")
                self._queue_ui_log("Checking Google Sheets session...")
                ok = GS.warm_session()
                if ok:
                    self.root.after(0, lambda: self.status_var.set("Google Sheets session ready"))
                    self._queue_ui_activity("Google Sheets session ready")
                    self._queue_ui_log("Google Sheets session ready")
            except Exception:
                pass

        threading.Thread(target=worker, name="pnpink-gsheets-auth-warmup", daemon=True).start()

    def _drain_queue(self):
        try:
            while True:
                self._set_request(self._queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self._drain_queue)

    def _drain_ui_queue(self):
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "activity":
                    self._set_activity(payload)
                elif kind == "progress":
                    label, current, total = payload
                    self._handle_progress_update(label, current, total)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_ui_queue)

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
        source_mode = self._detect_source_mode(template, self.sheet_id_var.get().strip(), self._source_mode_value())
        sheet_id = "" if source_mode == "local_csv" else self.sheet_id_var.get().strip()
        req = AppRequest(
            template=template,
            sheet_id=sheet_id,
            sheet_range=self.sheet_range_var.get().strip(),
            dataset_source_mode=source_mode,
        )
        _l.i(f"[deckmaker_app] run clicked template='{req.template}' sheet_id={'yes' if req.sheet_id else 'no'} range='{req.sheet_range}'")
        self._run_started_at = time.perf_counter()
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("Generating...")
        self._set_activity("Generating output...")
        self._log(("Auto generate" if autorun else "Generate") + " started")
        self._render_thread = threading.Thread(target=self._render_worker, args=(req,), daemon=True)
        self._render_thread.start()

    def _render_worker(self, req: AppRequest):
        try:
            import dataset_state as DSTATE
            import engine as ENG

            TEMPPATHS.cleanup_old_runs()
            self._start_progress_listener()
            self._start_web_activity_monitor()
            self._queue_ui_activity("Loading template and dataset...")
            self._queue_ui_log("Loading template and dataset...")
            effect = RUNNER.EngineEffect(req.template, req.sheet_id, req.sheet_range, req.log_level, req.dataset_source_mode)
            self._queue_ui_activity("Rendering output SVG...")
            self._queue_ui_log("Rendering output SVG...")
            ENG.run(effect, APP_VERSION)
            try:
                access_mode = str(getattr(effect.options, "_dataset_access_mode", "") or "").strip().lower()
                if req.sheet_id:
                    DSTATE.set_gsheet_for_svg(req.template, req.sheet_id, req.sheet_range, access_mode)
            except Exception:
                _l.w("[deckmaker_app] dataset state save failed\n" + traceback.format_exc())
            self._queue_ui_activity("Analyzing generated images...")
            dpi_report = PREFLIGHT.effective_image_dpi_report(DMPATHS.output_svg(req.template))
            self.root.after(0, lambda dpi_report=dpi_report: self._log_image_dpi_preflight(dpi_report))
            elapsed = (time.perf_counter() - self._run_started_at) if self._run_started_at else 0.0
            self.root.after(0, lambda: self._render_done(f"Done ({elapsed:.2f}s)"))
            self.root.after(0, self._after_create_success)
        except Exception as ex:
            _l.w("[deckmaker_app] render failed:\n" + traceback.format_exc())
            self.root.after(0, lambda: self._render_done(f"Error: {ex}"))
        finally:
            self._stop_progress_listener()
            self._stop_web_activity_monitor()

    def _render_done(self, status: str):
        self.progress.stop()
        self.run_btn.configure(state="normal")
        self.open_btn.configure(state="normal")
        self._refresh_export_button_state()
        self.status_var.set(status)
        self._commit_activity_to_log()
        self._set_activity("")
        self._log(status)

    def _after_create_success(self):
        if self._post_create_busy:
            return
        self._post_create_busy = True
        auto_open = bool(self.auto_open_var.get())
        auto_export = bool(self.auto_export_var.get()) and bool(self._selected_export_formats())
        output_svg_path = self._output_svg_path()
        template = self.template_var.get()
        export_options = self._export_options_snapshot()

        def worker():
            try:
                if auto_open:
                    try:
                        open_target = output_svg_path
                        self._open_svg_in_inkscape(open_target)
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
        label = ", ".join(fmt.upper() for fmt in formats)
        self.status_var.set(f"Exporting {label}...")
        self._set_activity("Preparing export...")
        self._log(("Auto export" if auto else "Export") + f" started: {label}")
        _l.i(
            "[export.ui] start auto=%s formats=%s profiles=%s",
            "yes" if auto else "no",
            ",".join(formats),
            ",".join(self._selected_pdf_profiles_for_export(options)),
        )
        self._log(
            "Export options: "
            f"profiles={','.join(self._selected_pdf_profiles_for_export(options))}, "
            f"pdf_raster_mode={options.pdf_raster_mode}, "
            f"dpi={int(options.export_dpi)}, "
            f"jpeg_quality={int(options.jpeg_quality)}, "
            f"other_format={options.other_format}, pages={options.other_pages or 'all'}"
        )

    def _export_clicked(self):
        if not self._selected_export_formats():
            self.status_var.set("No export outputs selected")
            self._log("No export outputs selected")
            return
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
            TEMPPATHS.cleanup_old_runs()
            self._start_web_activity_monitor()
            svg_path = DMPATHS.output_svg(template)
            formats = list(options.formats)
            started = time.perf_counter()
            _l.i(
                "[export.worker] start template='%s' svg='%s' formats=%s",
                template,
                svg_path,
                ",".join(formats),
            )
            self._queue_ui_activity("Resolving generated SVG output...")
            self.root.after(0, lambda: self._log(f"Export source: {os.path.basename(svg_path)}"))
            source_info = EXPORT.resolve_chunked_output_source(svg_path)
            if source_info.get("chunk_paths"):
                self.root.after(0, lambda count=len(source_info.get("chunk_paths") or []): self._log(
                    f"Using existing parted SVG output ({count} part(s))"
                ))

            failures: list[str] = []
            failure_details: list[str] = []
            if "pdf" in formats:
                pdf_path = DMPATHS.output_pdf(template)
                selected_profiles = self._selected_pdf_profiles_for_export(options)
                self._queue_ui_activity("Preparing PDF export...")
                self.root.after(0, lambda: self._log(f"PDF profiles: {', '.join(selected_profiles)}"))
                self.root.after(
                    0,
                    lambda mode=str(options.pdf_raster_mode or "png"): self._log(
                        f"PDF filter mode: {mode}"
                    ),
                )

                def _page_pdf_created(path: str):
                    self.root.after(0, lambda path=path: self._log(f"Created temp part PDF: {os.path.basename(path)}"))

                def _raster_progress(done: int, total: int):
                    try:
                        d = int(done or 0)
                        t = int(total or 0)
                    except Exception:
                        return
                    mode_label = str(options.pdf_raster_mode or "png").strip().lower()
                    if mode_label == "png_alpha":
                        mode_label = "png_alfa"
                    self._set_activity_progress(
                        f"Creating {mode_label} rasters for complex filters",
                        min(d, t),
                        t,
                    )

                ok, info = EXPORTPDF.export_pdf_via_inkscape(
                    svg_path,
                    pdf_path,
                    pdf_profiles=selected_profiles,
                    raster_filter_mode=str(options.pdf_raster_mode or "png"),
                    cmyk_icc=options.pdf_cmyk_icc,
                    cmyk_pure_black_text=bool(options.pdf_cmyk_pure_black_text),
                    pdfx_version=options.pdfx_version,
                    export_dpi=int(options.export_dpi or 300),
                    on_page_pdf_created=_page_pdf_created,
                    on_raster_progress=_raster_progress,
                    on_ghostscript_output=self._make_process_output_activity("Ghostscript"),
                )
                chunk_dir = str((info or {}).get("chunk_dir") or "") if isinstance(info, dict) else ""
                work_dir = str((info or {}).get("work_dir") or "") if isinstance(info, dict) else ""
                if chunk_dir:
                    self.root.after(0, lambda chunk_dir=chunk_dir: self._log(f"SVG parts dir: {chunk_dir}"))
                if work_dir:
                    self.root.after(0, lambda work_dir=work_dir: self._log(f"PDF temp dir: {work_dir}"))
                for raster_dir in list((info or {}).get("raster_dirs") or []):
                    if raster_dir:
                        self.root.after(0, lambda raster_dir=raster_dir: self._log(f"Raster cache dir: {raster_dir}"))
                if ok:
                    elapsed = float((info or {}).get("elapsed_s") or 0.0)
                    page_count = int((info or {}).get("page_count") or 0)
                    used_chunks = int((info or {}).get("chunk_count") or 1)
                    self.root.after(0, lambda elapsed=elapsed, page_count=page_count, used_chunks=used_chunks: self._log(
                        f"PDF export done in {elapsed:.2f}s across {page_count} page(s) using {used_chunks} SVG part(s)"
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
                    failure_details.append(f"PDF: {err}")
                    elapsed = float((info or {}).get("elapsed_s") or 0.0) if isinstance(info, dict) else 0.0
                    if elapsed > 0:
                        self._queue_ui_log(f"PDF export failed after {elapsed:.2f}s")
                    self._queue_ui_log(err)
                    _l.w("[export.worker] detail PDF: %s", err)

            other_formats = [fmt for fmt in formats if fmt != "pdf"]
            for export_type in other_formats:
                out_path = DMPATHS.output_other(template, export_type)
                self._queue_ui_activity(f"Preparing {export_type.upper()} export...")

                def _page_other_created(path: str, label: str = export_type.upper()):
                    self.root.after(0, lambda path=path, label=label: self._log(f"Created page {label}: {os.path.basename(path)}"))

                ok, info = EXPORT.export_other_pages_via_inkscape(
                    svg_path,
                    out_path,
                    export_type=export_type,
                    page_spec=options.other_pages,
                    export_dpi=int(options.export_dpi or 300),
                    jpeg_quality=int(options.jpeg_quality or 90),
                    on_page_created=_page_other_created,
                )
                if ok:
                    elapsed = float((info or {}).get("elapsed_s") or 0.0)
                    page_count = int((info or {}).get("page_count") or 0)
                    used_chunks = int((info or {}).get("chunk_count") or 1)
                    self.root.after(0, lambda elapsed=elapsed, page_count=page_count, used_chunks=used_chunks, export_type=export_type: self._log(
                        f"{export_type.upper()} export done in {elapsed:.2f}s across {page_count} page(s) using {used_chunks} SVG part(s)"
                    ))
                else:
                    failures.append(export_type.upper())
                    err = str((info or {}).get("error") or f"{export_type.upper()} export failed")
                    failure_details.append(f"{export_type.upper()}: {err}")
                    elapsed = float((info or {}).get("elapsed_s") or 0.0) if isinstance(info, dict) else 0.0
                    if elapsed > 0:
                        self._queue_ui_log(f"{export_type.upper()} export failed after {elapsed:.2f}s")
                    self._queue_ui_log(err)
                    _l.w("[export.worker] detail %s: %s", export_type.upper(), err)

            total_elapsed = time.perf_counter() - started
            if failures:
                _l.w(
                    "[export.worker] failed failures=%s elapsed=%.2fs",
                    ",".join(failures),
                    float(total_elapsed),
                )
                if failure_details:
                    self._queue_ui_log("Export details: " + " | ".join(failure_details))
                self.root.after(0, lambda failures=failures: self._render_done(f"Export failed: {', '.join(failures)}"))
                return
            _l.i("[export.worker] ok elapsed=%.2fs", float(total_elapsed))
            self.root.after(0, lambda total_elapsed=total_elapsed: self._render_done(f"Export ({total_elapsed:.2f}s)"))
        except Exception as ex:
            _l.w("[export.worker] exception %s\n%s", str(ex), traceback.format_exc())
            self.root.after(0, lambda ex=ex: self._render_done(f"Export failed: {ex}"))
        finally:
            self._stop_web_activity_monitor()
            self.root.after(0, self._refresh_icc_profile_choices)

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
    ap.add_argument("--dataset-source-mode", default="")
    ap.add_argument("--log-level", default="global")
    ns = ap.parse_args(argv)

    initial = None
    if ns.template:
        initial = AppRequest(
            template=_normalize_path(ns.template),
            sheet_id=ns.sheet_id,
            sheet_range=ns.sheet_range,
            dataset_source_mode=ns.dataset_source_mode,
            log_level=ns.log_level,
        )
    app = DeckMakerApp(initial)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

