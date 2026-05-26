#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spritesheet GUI calculator for PnPInk.

This tool does NOT modify the SVG. It only:
- previews grid over an image snapshot of the selection
- builds a copyable `.Layout{...}` expression
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import tkinter as tk
from dataclasses import dataclass
from math import floor
from time import perf_counter
from tkinter import ttk

import inkex

import const as CONST
import deckmaker_ipc as DMIPC
import inkscape_cli as INKSCAPE
import log as LOG
import deckmaker_paths as DMPATHS
import svg as SVG
import svg

_l = LOG
INKSCAPE_DOC_DPI = 96.0
PREVIEW_EXPORT_DPI = 300
MM_SPIN_STEP = 0.01
MM_CUSTOM_STEP = 0.1


def mm_to_px(mm: float, svgdoc) -> float:
    return float(inkex.units.convert_unit(f"{mm}mm", "px", svgdoc))


def px_to_mm(px: float, svgdoc) -> float:
    return float(inkex.units.convert_unit(f"{px}px", "mm", svgdoc))


DEFAULT_GUI_SPEC = {
    "card_mode": "auto",
    "cols": 6,
    "rows": 4,
    "card_preset": "Standard",
    "card_w_mm": 63.0,
    "card_h_mm": 88.0,
    "margin": "0",
    "gap": "0",
    "gap_h": 0.0,
    "gap_v": 0.0,
    "border_t": 0.0,
    "border_r": 0.0,
    "border_b": 0.0,
    "border_l": 0.0,
}


def _parse_mm_token(tok, default=None):
    s = "" if tok is None else str(tok).strip()
    if not s:
        return default
    try:
        return float(SVG.measure_to_mm(s, base_mm=None))
    except Exception:
        try:
            return float(s)
        except Exception:
            return default


def _split_tokens(spec: str):
    s = (spec or "").strip()
    if not s:
        return []
    return [p for p in re.split(r"[\s,]+", s) if p]


def _expand_margin_spec(spec: str):
    vals = [_parse_mm_token(t, default=0.0) for t in _split_tokens(spec)]
    vals = [0.0 if v is None else float(v) for v in vals]
    if not vals:
        return (0.0, 0.0, 0.0, 0.0)
    if len(vals) == 1:
        a = vals[0]
        return (a, a, a, a)
    if len(vals) == 2:
        v, h = vals[0], vals[1]
        return (v, h, v, h)
    if len(vals) >= 4:
        return (vals[0], vals[1], vals[2], vals[3])
    return (vals[0], vals[1], vals[2], vals[1])


def _expand_gap_spec(spec: str):
    vals = [_parse_mm_token(t, default=0.0) for t in _split_tokens(spec)]
    vals = [0.0 if v is None else float(v) for v in vals]
    if not vals:
        return (0.0, 0.0)
    if len(vals) == 1:
        g = vals[0]
        return (g, g)
    return (vals[0], vals[1])


def _fmt_num(v: float) -> str:
    try:
        f = float(v)
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _layout_expr(rows, cols, mt, mr, mb, ml, gv, gh):
    # pattern uses cols x rows
    parts = [f"p={cols}x{rows}"]
    # gaps: x(horizontal)=gh, y(vertical)=gv
    if abs(gh) > 1e-9 or abs(gv) > 1e-9:
        if abs(gh - gv) < 1e-9:
            parts.append(f"g={_fmt_num(gh)}")
        else:
            parts.append(f"g=[{_fmt_num(gh)} {_fmt_num(gv)}]")
    # border: only when non-zero
    if any(abs(v) > 1e-9 for v in (mt, mr, mb, ml)):
        if abs(mt - mr) < 1e-9 and abs(mt - mb) < 1e-9 and abs(mt - ml) < 1e-9:
            parts.append(f"b={_fmt_num(mt)}")
        elif abs(mt - mb) < 1e-9 and abs(mr - ml) < 1e-9:
            parts.append(f"b=[{_fmt_num(mt)} {_fmt_num(mr)}]")
        else:
            parts.append(f"b=[{_fmt_num(mt)} {_fmt_num(mr)} {_fmt_num(mb)} {_fmt_num(ml)}]")
    return ".Layout{" + " ".join(parts) + "}"


def _compute_grid(spec, bw, bh, svgdoc):
    # CSS border order: top, right, bottom, left.
    mt_mm, mr_mm, mb_mm, ml_mm = _expand_margin_spec(spec["margin"])
    gv_mm, gh_mm = _expand_gap_spec(spec["gap"])
    mt_mm = max(0.0, float(mt_mm or 0.0))
    ml_mm = max(0.0, float(ml_mm or 0.0))
    mb_mm = max(0.0, float(mb_mm or 0.0))
    mr_mm = max(0.0, float(mr_mm or 0.0))
    gv_mm = max(0.0, float(gv_mm or 0.0))
    gh_mm = max(0.0, float(gh_mm or 0.0))

    mt = mm_to_px(mt_mm, svgdoc)
    ml = mm_to_px(ml_mm, svgdoc)
    mb = mm_to_px(mb_mm, svgdoc)
    mr = mm_to_px(mr_mm, svgdoc)
    gv = mm_to_px(gv_mm, svgdoc)
    gh = mm_to_px(gh_mm, svgdoc)

    content_w = max(0.0, bw - (ml + mr))
    content_h = max(0.0, bh - (mt + mb))
    if content_w <= 0 or content_h <= 0:
        return None

    mode = (spec["card_mode"] or "auto").strip().lower()
    if mode == "preset":
        cw_mm, ch_mm = CONST.get_card_size_preset(spec["card_preset"]) or CONST.get_card_size_preset("Standard")
        tw = mm_to_px(cw_mm, svgdoc)
        th = mm_to_px(ch_mm, svgdoc)
        cols = max(0, floor((content_w + gh) / (tw + gh)))
        rows = max(0, floor((content_h + gv) / (th + gv)))
    elif mode == "custom":
        cw_mm = max(1.0, float(spec["card_w_mm"]))
        ch_mm = max(1.0, float(spec["card_h_mm"]))
        tw = mm_to_px(cw_mm, svgdoc)
        th = mm_to_px(ch_mm, svgdoc)
        cols = max(0, floor((content_w + gh) / (tw + gh)))
        rows = max(0, floor((content_h + gv) / (th + gv)))
    else:
        cols = max(1, int(spec["cols"]))
        rows = max(1, int(spec["rows"]))
        tw = (content_w - (cols - 1) * gh) / cols
        th = (content_h - (rows - 1) * gv) / rows
        if tw <= 0 or th <= 0:
            return None

    if rows <= 0 or cols <= 0:
        return None

    rects = []
    for r in range(rows):
        y = mt + r * (th + gv)
        for c in range(cols):
            x = ml + c * (tw + gh)
            rects.append((x, y, tw, th))

    return {
        "rects": rects,
        "rows": rows,
        "cols": cols,
        "mt_mm": mt_mm,
        "ml_mm": ml_mm,
        "mb_mm": mb_mm,
        "mr_mm": mr_mm,
        "gv_mm": gv_mm,
        "gh_mm": gh_mm,
    }


def _try_export_png_id(input_svg: str, node_id: str | None, inkscape_exe: str | None) -> str | None:
    if not inkscape_exe or not node_id or not input_svg:
        _l.w(
            "[spritesheet_gui] export id skipped "
            f"inkscape='{inkscape_exe or ''}' id='{node_id or ''}' svg='{input_svg or ''}'"
        )
        return None
    fd, out_png = tempfile.mkstemp(prefix="pnpink_sprite_id_", suffix=".png")
    os.close(fd)
    commands = [
        "active-window-start",
        "export-type:png",
        f"export-dpi:{int(PREVIEW_EXPORT_DPI)}",
        f"export-filename:{out_png}",
        f"export-id:{node_id}",
        "export-id-only",
        "export-do",
    ]
    _l.i(f"[spritesheet_gui] export id shell start svg='{input_svg}' id='{node_id}' out='{out_png}'")
    try:
        rc, msg = INKSCAPE.run_shell_commands(
            inkscape_exe,
            commands,
            exe_dir=INKSCAPE.executable_dir(inkscape_exe),
            env=INKSCAPE.clean_launch_env(isolated_profile=False),
            timeout_s=8.0,
            shell_args=["-q", "-g"],
        )
        exists = os.path.isfile(out_png)
        size = os.path.getsize(out_png) if exists else 0
        _l.i(f"[spritesheet_gui] export id shell done id='{node_id}' rc={rc} exists={exists} size={size}")
        if rc == 0 and exists and size > 0:
            return out_png
        _l.w(f"[spritesheet_gui] export id shell failed id='{node_id}' rc={rc} msg={str(msg or '')[:500]}")
    except Exception:
        pass
    try:
        os.remove(out_png)
    except Exception:
        pass
    return None


def _write_temp_svg(doc_tree) -> str | None:
    try:
        fd, out_svg = tempfile.mkstemp(prefix="pnpink_tmp_doc_", suffix=".svg")
        os.close(fd)
        doc_tree.write(out_svg, encoding="utf-8", xml_declaration=True)
        _l.i(f"[spritesheet_gui] temp svg written '{out_svg}' size={os.path.getsize(out_svg) if os.path.isfile(out_svg) else 0}")
        return out_svg
    except Exception as ex:
        _l.w(f"[spritesheet_gui] temp svg write failed: {ex}")
        return None


def _copy_svg_for_worker(src_svg: str) -> str | None:
    src = os.path.abspath(str(src_svg or ""))
    if not src or not os.path.isfile(src):
        return None
    try:
        fd, out_svg = tempfile.mkstemp(prefix="pnpink_sprite_worker_", suffix=".svg")
        os.close(fd)
        with open(src, "rb") as fin, open(out_svg, "wb") as fout:
            fout.write(fin.read())
        _l.i(f"[spritesheet_gui] worker svg copy='{out_svg}' size={os.path.getsize(out_svg)}")
        return out_svg
    except Exception as ex:
        _l.w(f"[spritesheet_gui] worker svg copy failed: {ex}")
        return None

@dataclass
class GuiResult:
    layout_text: str | None


class SpriteSheetGui:
    def __init__(self, bw, bh, initial_spec, svgdoc, initial_image: str | None = None):
        self.bw = float(bw)
        self.bh = float(bh)
        self.svgdoc = svgdoc
        self.result = GuiResult(None)
        self.zoom = 1.0
        self.pan_x = 10.0
        self.pan_y = 10.0
        self._drag_last = None
        self._photo = None
        self._pil_base = None
        self._pil_image = None
        self._img_id = None
        self._did_initial_fit = False
        self._last_pil_size = None
        self._last_view_key = None
        self._last_spec_key = None
        self._last_grid = None
        self._pending_redraw = None
        self._syncing_controls = False
        self._icon_image = None
        self._place_cache = {}
        self._mm_step = MM_SPIN_STEP
        self._gap_max_mm = 20.0
        self._border_max_mm = 40.0
        self._cleanup_paths = []

        self.root = tk.Tk()
        self.root.title("PnPInk Spritesheet")
        self.root.geometry("980x560+80+40")
        self.root.minsize(820, 460)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_window_icon()

        self.vars = {
            "card_mode": tk.StringVar(value=initial_spec["card_mode"]),
            "ui_mode": tk.StringVar(value="Manual"),
            "cols": tk.IntVar(value=int(initial_spec["cols"])),
            "rows": tk.IntVar(value=int(initial_spec["rows"])),
            "card_preset": tk.StringVar(value=initial_spec["card_preset"]),
            "card_w_mm": tk.StringVar(value=str(initial_spec["card_w_mm"])),
            "card_h_mm": tk.StringVar(value=str(initial_spec["card_h_mm"])),
            "margin": tk.StringVar(value=initial_spec["margin"]),
            "gap": tk.StringVar(value=initial_spec["gap"]),
            "gap_h": tk.DoubleVar(value=float(initial_spec.get("gap_h", 2.0))),
            "gap_v": tk.DoubleVar(value=float(initial_spec.get("gap_v", 2.0))),
            "gap_link": tk.BooleanVar(value=False),
            "border_t": tk.DoubleVar(value=float(initial_spec.get("border_t", 5.0))),
            "border_r": tk.DoubleVar(value=float(initial_spec.get("border_r", 5.0))),
            "border_b": tk.DoubleVar(value=float(initial_spec.get("border_b", 5.0))),
            "border_l": tk.DoubleVar(value=float(initial_spec.get("border_l", 5.0))),
            "border_tb_link": tk.BooleanVar(value=False),
            "border_lr_link": tk.BooleanVar(value=False),
            "layout_text": tk.StringVar(value=""),
            "status": tk.StringVar(value="Ready"),
        }

        self._build_ui()
        if initial_image:
            self._load_image(initial_image)
        self.root.after(80, self.request_redraw)

    def _build_ui(self):
        self.viewport = ttk.Frame(self.root, padding=0)
        self.viewport.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(self.viewport, background="#f7f7f7", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self._build_overlay_controls(self.viewport)
        self._build_bottom_controls(self.viewport)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._on_wheel_linux(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._on_wheel_linux(e, -1))
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)

        for k, v in self.vars.items():
            if k not in ("status", "layout_text"):
                if k == "ui_mode":
                    v.trace_add("write", lambda *_: self._on_ui_mode_changed())
                else:
                    v.trace_add("write", lambda *_: self.request_redraw())
        self._on_ui_mode_changed()

    def _build_bottom_controls(self, parent):
        choices = ["Manual", "Custom", "Preset"]
        self.mode_combo = ttk.Combobox(parent, state="readonly", textvariable=self.vars["ui_mode"], values=choices, width=16)
        self.mode_combo.place(x=4, rely=1.0, y=-2, anchor="sw")

        self.custom_w_spin = ttk.Spinbox(parent, from_=1, to=500, increment=MM_CUSTOM_STEP, width=6, textvariable=self.vars["card_w_mm"], command=self.request_redraw)
        self.custom_h_spin = ttk.Spinbox(parent, from_=1, to=500, increment=MM_CUSTOM_STEP, width=6, textvariable=self.vars["card_h_mm"], command=self.request_redraw)
        self.custom_w_spin.place(x=134, rely=1.0, y=-2, anchor="sw")
        self.custom_h_spin.place(x=194, rely=1.0, y=-2, anchor="sw")
        self.preset_combo = ttk.Combobox(parent, state="readonly", textvariable=self.vars["card_preset"], values=sorted(CONST.CARD_SIZES_MM.keys()), width=18)
        self.preset_combo.place(x=134, rely=1.0, y=-2, anchor="sw")
        self.preset_combo.bind("<<ComboboxSelected>>", lambda _e: self.request_redraw())

        self.layout_entry = ttk.Entry(parent, textvariable=self.vars["layout_text"], width=48, justify="center")
        self.layout_entry.place(relx=0.5, rely=1.0, y=-2, anchor="s")

    def _build_overlay_controls(self, parent):
        self.manual_controls = []

        def remember(widget):
            self.manual_controls.append(widget)
            return widget

        self.cols_scale = ttk.Scale(parent, from_=1, to=30, orient=tk.HORIZONTAL, variable=self.vars["cols"], length=96, command=lambda _v: self._round_grid_vars())
        remember(self.cols_scale).place(relx=1.0, y=0, x=-4, anchor="ne")
        remember(ttk.Spinbox(parent, from_=1, to=30, width=3, textvariable=self.vars["cols"], command=self._round_grid_vars)).place(relx=1.0, y=0, x=-104, anchor="ne")

        self.rows_scale = ttk.Scale(parent, from_=1, to=30, orient=tk.VERTICAL, variable=self.vars["rows"], length=96, command=lambda _v: self._round_grid_vars())
        remember(self.rows_scale).place(relx=1.0, y=28, x=-4, anchor="ne")
        remember(ttk.Spinbox(parent, from_=1, to=30, width=3, textvariable=self.vars["rows"], command=self._round_grid_vars)).place(relx=1.0, y=128, x=-4, anchor="ne")

        self.gap_h_scale = ttk.Scale(parent, from_=0, to=30, orient=tk.HORIZONTAL, variable=self.vars["gap_h"], length=130, command=lambda _v: self._sync_linked_controls())
        remember(self.gap_h_scale).place(relx=0.5, y=0, anchor="n")
        self.gap_h_spin = ttk.Spinbox(parent, from_=0, to=30, increment=0.1, width=4, textvariable=self.vars["gap_h"], command=self._sync_linked_controls)
        remember(self.gap_h_spin).place(relx=0.5, y=0, x=70, anchor="nw")
        remember(ttk.Checkbutton(parent, text="", variable=self.vars["gap_link"], command=self._sync_linked_controls)).place(x=4, rely=0.5, y=-80, anchor="nw")
        self.gap_v_scale = ttk.Scale(parent, from_=0, to=30, orient=tk.VERTICAL, variable=self.vars["gap_v"], length=112)
        remember(self.gap_v_scale).place(x=4, rely=0.5, y=-54, anchor="nw")
        self.gap_v_spin = ttk.Spinbox(parent, from_=0, to=30, increment=0.1, width=4, textvariable=self.vars["gap_v"], command=self._sync_linked_controls)
        remember(self.gap_v_spin).place(x=4, rely=0.5, y=64, anchor="nw")

        self.border_t_scale = ttk.Scale(parent, from_=0, to=50, orient=tk.HORIZONTAL, variable=self.vars["border_t"], length=118, command=lambda _v: self._sync_linked_controls())
        remember(self.border_t_scale).place(x=4, y=0, anchor="nw")
        self.border_t_spin = ttk.Spinbox(parent, from_=0, to=50, increment=0.1, width=4, textvariable=self.vars["border_t"], command=self._sync_linked_controls)
        remember(self.border_t_spin).place(x=126, y=0, anchor="nw")
        remember(ttk.Checkbutton(parent, text="", variable=self.vars["border_tb_link"], command=self._sync_linked_controls)).place(x=4, y=28, anchor="nw")
        self.border_l_scale = ttk.Scale(parent, from_=0, to=50, orient=tk.VERTICAL, variable=self.vars["border_l"], length=96, command=lambda _v: self._sync_linked_controls())
        remember(self.border_l_scale).place(x=4, y=54, anchor="nw")
        self.border_l_spin = ttk.Spinbox(parent, from_=0, to=50, increment=0.1, width=4, textvariable=self.vars["border_l"], command=self._sync_linked_controls)
        remember(self.border_l_spin).place(x=4, y=154, anchor="nw")

        remember(ttk.Checkbutton(parent, text="", variable=self.vars["border_lr_link"], command=self._sync_linked_controls)).place(relx=1.0, rely=1.0, x=-4, y=-4, anchor="se")
        self.border_b_scale = ttk.Scale(parent, from_=50, to=0, orient=tk.HORIZONTAL, variable=self.vars["border_r"], length=118)
        remember(self.border_b_scale).place(relx=1.0, rely=1.0, x=-32, y=-4, anchor="se")
        self.border_b_spin = ttk.Spinbox(parent, from_=0, to=50, increment=0.1, width=4, textvariable=self.vars["border_b"], command=self._sync_linked_controls)
        remember(self.border_b_spin).place(relx=1.0, rely=1.0, x=-154, y=-4, anchor="se")
        self.border_r_scale = ttk.Scale(parent, from_=50, to=0, orient=tk.VERTICAL, variable=self.vars["border_b"], length=96)
        remember(self.border_r_scale).place(relx=1.0, rely=1.0, x=-4, y=-32, anchor="se")
        self.border_r_spin = ttk.Spinbox(parent, from_=0, to=50, increment=0.1, width=4, textvariable=self.vars["border_r"], command=self._sync_linked_controls)
        remember(self.border_r_spin).place(relx=1.0, rely=1.0, x=-4, y=-132, anchor="se")

        self._refresh_mm_step_controls(force=True)
        self._sync_linked_controls()

    def _apply_window_icon(self):
        icon_path = DMPATHS.app_icon(os.path.dirname(__file__))
        if not os.path.isfile(icon_path):
            return
        try:
            self._icon_image = tk.PhotoImage(file=icon_path)
            self.root.iconphoto(True, self._icon_image)
        except Exception:
            self._icon_image = None

    def _on_ui_mode_changed(self):
        mode = str(self.vars["ui_mode"].get() or "Manual").strip()
        manual = mode == "Manual"
        custom = mode == "Custom"
        preset = mode == "Preset"
        try:
            self.vars["card_mode"].set("auto" if manual else ("custom" if custom else "preset"))
        except Exception:
            pass
        for widget in getattr(self, "manual_controls", []):
            try:
                if manual:
                    info = self._place_cache.get(widget)
                    if info:
                        widget.place(**info)
                else:
                    info = widget.place_info()
                    if info:
                        self._place_cache[widget] = info
                    widget.place_forget()
            except Exception:
                pass
        for widget in (getattr(self, "custom_w_spin", None), getattr(self, "custom_h_spin", None)):
            try:
                if custom:
                    info = self._place_cache.get(widget)
                    if info:
                        widget.place(**info)
                else:
                    info = widget.place_info()
                    if info:
                        self._place_cache[widget] = info
                    widget.place_forget()
            except Exception:
                pass
        widget = getattr(self, "preset_combo", None)
        if widget is not None:
            try:
                if preset:
                    info = self._place_cache.get(widget)
                    if info:
                        widget.place(**info)
                else:
                    info = widget.place_info()
                    if info:
                        self._place_cache[widget] = info
                    widget.place_forget()
            except Exception:
                pass
        self.request_redraw()

    def _round_grid_vars(self):
        if self._syncing_controls:
            return
        try:
            self._syncing_controls = True
            self.vars["cols"].set(max(1, min(30, int(round(float(self.vars["cols"].get()))))))
            self.vars["rows"].set(max(1, min(30, int(round(float(self.vars["rows"].get()))))))
        except Exception:
            pass
        finally:
            self._syncing_controls = False
        self.request_redraw()

    def _mm_step_for_zoom(self) -> float:
        return MM_SPIN_STEP

    def _refresh_mm_step_controls(self, *, force: bool = False) -> None:
        new_step = self._mm_step_for_zoom()
        if not force and abs(new_step - self._mm_step) < 1e-9:
            return
        self._mm_step = float(new_step)
        try:
            w_mm = max(1.0, px_to_mm(self.bw, self.svgdoc))
            h_mm = max(1.0, px_to_mm(self.bh, self.svgdoc))
            min_mm = min(w_mm, h_mm)
            self._gap_max_mm = max(0.5, min(100.0, min_mm * 0.10))
            self._border_max_mm = max(1.0, min(150.0, min_mm * 0.20))
        except Exception:
            pass
        spinboxes = (
            getattr(self, "gap_h_spin", None),
            getattr(self, "gap_v_spin", None),
            getattr(self, "border_t_spin", None),
            getattr(self, "border_r_spin", None),
            getattr(self, "border_b_spin", None),
            getattr(self, "border_l_spin", None),
            getattr(self, "custom_w_spin", None),
            getattr(self, "custom_h_spin", None),
        )
        for widget in spinboxes:
            if widget is None:
                continue
            try:
                step = MM_CUSTOM_STEP if widget in (getattr(self, "custom_w_spin", None), getattr(self, "custom_h_spin", None)) else self._mm_step
                widget.configure(increment=step)
            except Exception:
                pass
        try:
            self.gap_h_scale.configure(to=self._gap_max_mm)
            self.gap_v_scale.configure(to=self._gap_max_mm)
            self.border_t_scale.configure(to=self._border_max_mm)
            self.border_l_scale.configure(to=self._border_max_mm)
            self.border_b_scale.configure(from_=self._border_max_mm, to=0)
            self.border_r_scale.configure(from_=self._border_max_mm, to=0)
            self.gap_h_spin.configure(to=self._gap_max_mm)
            self.gap_v_spin.configure(to=self._gap_max_mm)
            self.border_t_spin.configure(to=self._border_max_mm)
            self.border_r_spin.configure(to=self._border_max_mm)
            self.border_b_spin.configure(to=self._border_max_mm)
            self.border_l_spin.configure(to=self._border_max_mm)
        except Exception:
            pass

    def _sync_linked_controls(self):
        if self._syncing_controls:
            return
        try:
            self._syncing_controls = True
            self.vars["gap_h"].set(min(self._gap_max_mm, max(0.0, float(self.vars["gap_h"].get()))))
            self.vars["gap_v"].set(min(self._gap_max_mm, max(0.0, float(self.vars["gap_v"].get()))))
            self.vars["border_t"].set(min(self._border_max_mm, max(0.0, float(self.vars["border_t"].get()))))
            self.vars["border_r"].set(min(self._border_max_mm, max(0.0, float(self.vars["border_r"].get()))))
            self.vars["border_b"].set(min(self._border_max_mm, max(0.0, float(self.vars["border_b"].get()))))
            self.vars["border_l"].set(min(self._border_max_mm, max(0.0, float(self.vars["border_l"].get()))))
            gap_v_enabled = bool(self.vars["gap_link"].get())
            if not gap_v_enabled:
                self.vars["gap_v"].set(float(self.vars["gap_h"].get()))
            gap_state = "normal" if gap_v_enabled else "disabled"
            self.gap_v_scale.configure(state=gap_state)
            vertical_borders = bool(self.vars["border_tb_link"].get())
            independent_borders = bool(self.vars["border_lr_link"].get())
            if not vertical_borders and not independent_borders:
                # Top horizontal is the master border control.
                self.vars["border_r"].set(float(self.vars["border_t"].get()))
                self.vars["border_b"].set(float(self.vars["border_t"].get()))
                self.vars["border_l"].set(float(self.vars["border_t"].get()))
            elif vertical_borders and not independent_borders:
                # Checkbox2 ON:
                # - slider1 (top, horizontal) controls left/right
                # - slider2 (left, vertical) controls top/bottom
                horizontal = float(self.vars["border_t"].get())  # left/right
                vertical = float(self.vars["border_l"].get())    # top/bottom
                self.vars["border_r"].set(horizontal)
                self.vars["border_b"].set(vertical)
            border_l_state = "normal" if (vertical_borders or independent_borders) else "disabled"
            border_extra_state = "normal" if independent_borders else "disabled"
            self.border_l_scale.configure(state=border_l_state)
            self.border_b_scale.configure(state=border_extra_state)
            self.border_r_scale.configure(state=border_extra_state)
        except Exception:
            pass
        finally:
            self._syncing_controls = False
        self.request_redraw()

    def _load_image(self, path: str | None):
        if not path or not os.path.isfile(path):
            _l.w(f"[spritesheet_gui] image path not found: {path or 'none'}")
            return
        try:
            from PIL import Image
            self._pil_base = Image.open(path).convert("RGBA")
            self._photo = None
            self._last_pil_size = None
            _l.d("[spritesheet_gui] image loaded PIL:", path, "size=", self._pil_base.size)
            return
        except Exception as ex:
            _l.w(f"[spritesheet_gui] PIL image load failed path='{path}': {ex}")
        try:
            self._photo = tk.PhotoImage(file=path)
            self._pil_base = None
            _l.d("[spritesheet_gui] image loaded Tk:", path)
        except Exception as ex:
            _l.w(f"[spritesheet_gui] Tk image load failed path='{path}': {ex}")
            self._photo = None
            self._pil_base = None

    def _fit_view(self):
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        zx = (cw - 30) / max(1.0, self.bw)
        zy = (ch - 30) / max(1.0, self.bh)
        self.zoom = max(0.15, min(10.0, min(zx, zy)))
        self.pan_x = (cw - self.bw * self.zoom) * 0.5
        self.pan_y = (ch - self.bh * self.zoom) * 0.5
        self._refresh_mm_step_controls()
        self._did_initial_fit = True

    def _on_canvas_configure(self, _event):
        if not self._did_initial_fit and self.canvas.winfo_width() > 100 and self.canvas.winfo_height() > 100:
            self._fit_view()
        self.request_redraw()

    def _spec(self):
        return {k: str(v.get()).strip() for k, v in self.vars.items() if k not in ("status", "layout_text")}

    def _parse_numeric(self, s, default):
        try:
            return float(s)
        except Exception:
            return default

    def _normalized_spec(self):
        s = self._spec()
        gh = float(self.vars["gap_h"].get())
        gv = float(self.vars["gap_v"].get())
        bt = float(self.vars["border_t"].get())
        bl = float(self.vars["border_l"].get())
        bb = float(self.vars["border_b"].get())
        br = float(self.vars["border_r"].get())
        if bool(self.vars["border_lr_link"].get()):
            mt, ml, mb, mr = bt, bl, bb, br
        elif bool(self.vars["border_tb_link"].get()):
            # Checkbox2 ON:
            # slider1 (border_t) -> left/right, slider2 (border_l) -> top/bottom
            mt, ml, mb, mr = bl, bt, bl, bt
        else:
            mt = ml = mb = mr = bt
        return {
            "card_mode": s["card_mode"] or "auto",
            "cols": max(1, int(self._parse_numeric(s["cols"], 6))),
            "rows": max(1, int(self._parse_numeric(s["rows"], 4))),
            "card_preset": s["card_preset"] or "Standard",
            "card_w_mm": max(1.0, self._parse_numeric(s["card_w_mm"], 63.0)),
            "card_h_mm": max(1.0, self._parse_numeric(s["card_h_mm"], 88.0)),
            "margin": f"{_fmt_num(mt)} {_fmt_num(mr)} {_fmt_num(mb)} {_fmt_num(ml)}",
            "gap": f"{_fmt_num(gv)} {_fmt_num(gh)}",
        }

    def _draw_background(self):
        if self._pil_base is not None:
            try:
                from PIL import Image, ImageTk

                cw = max(1, int(self.canvas.winfo_width()))
                ch = max(1, int(self.canvas.winfo_height()))
                # Crop to the visible logical area first. Resizing a huge full
                # spritesheet on every wheel event makes zoom feel laggy.
                vx0 = max(0.0, (-self.pan_x) / max(1e-9, self.zoom))
                vy0 = max(0.0, (-self.pan_y) / max(1e-9, self.zoom))
                vx1 = min(self.bw, (cw - self.pan_x) / max(1e-9, self.zoom))
                vy1 = min(self.bh, (ch - self.pan_y) / max(1e-9, self.zoom))
                if vx1 <= vx0 or vy1 <= vy0:
                    return

                src_w, src_h = self._pil_base.size
                sx = src_w / max(1.0, self.bw)
                sy = src_h / max(1.0, self.bh)
                crop_box = (
                    max(0, min(src_w - 1, int(vx0 * sx))),
                    max(0, min(src_h - 1, int(vy0 * sy))),
                    max(1, min(src_w, int(vx1 * sx) + 1)),
                    max(1, min(src_h, int(vy1 * sy) + 1)),
                )
                draw_w = max(1, int((vx1 - vx0) * self.zoom))
                draw_h = max(1, int((vy1 - vy0) * self.zoom))
                view_key = (crop_box, draw_w, draw_h)
                if self._pil_image is None or self._last_view_key != view_key:
                    resampling = getattr(Image, "Resampling", Image)
                    cropped = self._pil_base.crop(crop_box)
                    resized = cropped.resize((draw_w, draw_h), resample=getattr(resampling, "BILINEAR", 2))
                    self._pil_image = ImageTk.PhotoImage(resized)
                    self._last_pil_size = (draw_w, draw_h)
                    self._last_view_key = view_key
                ix = self.pan_x + vx0 * self.zoom
                iy = self.pan_y + vy0 * self.zoom
                if self._img_id is None:
                    self._img_id = self.canvas.create_image(ix, iy, anchor="nw", image=self._pil_image)
                else:
                    self.canvas.coords(self._img_id, ix, iy)
                    self.canvas.itemconfigure(self._img_id, image=self._pil_image)
                return
            except Exception as ex:
                _l.w("[spritesheet_gui] PIL draw failed: ", ex)
                self._pil_base = None
                self._pil_image = None
                self._last_pil_size = None
                self._last_view_key = None
        if self._photo is not None:
            if self._img_id is None:
                self._img_id = self.canvas.create_image(self.pan_x, self.pan_y, anchor="nw", image=self._photo)
            else:
                self.canvas.coords(self._img_id, self.pan_x, self.pan_y)
                self.canvas.itemconfigure(self._img_id, image=self._photo)
            return
        if self._img_id is None:
            self._img_id = self.canvas.create_rectangle(
                self.pan_x,
                self.pan_y,
                self.pan_x + self.bw * self.zoom,
                self.pan_y + self.bh * self.zoom,
                fill="#ffffff",
                outline="#cccccc",
            )
        else:
            self.canvas.coords(
                self._img_id,
                self.pan_x,
                self.pan_y,
                self.pan_x + self.bw * self.zoom,
                self.pan_y + self.bh * self.zoom,
            )

    def request_redraw(self):
        if self._pending_redraw is not None:
            try:
                self.root.after_cancel(self._pending_redraw)
            except Exception:
                pass
        self._pending_redraw = self.root.after(14, self.redraw)

    def redraw(self):
        self._pending_redraw = None
        try:
            t0 = perf_counter()
            spec = self._normalized_spec()
            spec_key = (
                spec["card_mode"],
                spec["cols"],
                spec["rows"],
                spec["card_preset"],
                spec["card_w_mm"],
                spec["card_h_mm"],
                spec["margin"],
                spec["gap"],
            )
            if spec_key != self._last_spec_key:
                self._last_grid = _compute_grid(spec, self.bw, self.bh, self.svgdoc)
                self._last_spec_key = spec_key
            grid = self._last_grid

            self.canvas.delete("grid")
            self._draw_background()

            if grid is None:
                self.vars["status"].set("Invalid params or no fit")
                self.vars["layout_text"].set("")
                return
            for (x, y, w, h) in grid["rects"]:
                x0 = self.pan_x + x * self.zoom
                y0 = self.pan_y + y * self.zoom
                x1 = self.pan_x + (x + w) * self.zoom
                y1 = self.pan_y + (y + h) * self.zoom
                self.canvas.create_rectangle(x0, y0, x1, y1, outline="#1a73e8", width=1, tags=("grid",))

            self.vars["layout_text"].set(
                _layout_expr(
                    grid["rows"],
                    grid["cols"],
                    grid["mt_mm"],
                    grid["mr_mm"],
                    grid["mb_mm"],
                    grid["ml_mm"],
                    grid["gv_mm"],
                    grid["gh_mm"],
                )
            )
            self.vars["status"].set("")
            dt = int((perf_counter() - t0) * 1000)
            if dt >= 25:
                _l.d("[spritesheet_gui] redraw_ms=", dt, "slots=", len(grid["rects"]))
        except Exception as ex:
            self.vars["status"].set("Preview redraw failed")
            _l.w("[spritesheet_gui] redraw failed: ", ex)

    def _zoom_at(self, factor, x, y):
        old = self.zoom
        new = max(0.05, min(80.0, old * factor))
        wx = (x - self.pan_x) / old
        wy = (y - self.pan_y) / old
        self.zoom = new
        self.pan_x = x - wx * new
        self.pan_y = y - wy * new
        self.request_redraw()

    def _on_wheel(self, event):
        self._zoom_at(1.25 if event.delta > 0 else 1 / 1.25, event.x, event.y)

    def _on_wheel_linux(self, event, direction):
        self._zoom_at(1.25 if direction > 0 else 1 / 1.25, event.x, event.y)

    def _on_pan_start(self, event):
        self._drag_last = (event.x, event.y)

    def _on_pan_move(self, event):
        if self._drag_last is None:
            return
        dx = event.x - self._drag_last[0]
        dy = event.y - self._drag_last[1]
        self._drag_last = (event.x, event.y)
        self.pan_x += dx
        self.pan_y += dy
        self.canvas.move("all", dx, dy)

    def _on_close(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()
        for p in self._cleanup_paths:
            try:
                os.remove(p)
            except Exception:
                pass
        return GuiResult(self.vars["layout_text"].get().strip() or None)


class SpriteSheetGUIExtension(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab")

    def effect(self):
        t_effect = perf_counter()
        svgdoc = self.svg
        selection = list(svgdoc.selection or [])
        if len(selection) != 1:
            raise inkex.AbortExtension("Select exactly one group.")
        selected_node = selection[0]
        selected_tag = str(getattr(selected_node, "tag", "") or "")
        if not selected_tag.endswith("g"):
            raise inkex.AbortExtension("Select exactly one SVG group (<g>).")
        selected_id = (selected_node.get("id") or "").strip()
        if not selected_id:
            raise inkex.AbortExtension("Selected group has no id.")
        try:
            _l.i(
                "[spritesheet_gui] selection "
                f"count={len(selection)} ids={[((n.get('id') or '').strip()) for n in selection]} "
                f"tags={[str(getattr(n, 'tag', '')) for n in selection]}"
            )
        except Exception:
            pass

        l = t = r = b = None
        for n in selection:
            x, y, w, h = svg.visual_bbox(n)
            _l.i(f"[spritesheet_gui] visual_bbox id='{(n.get('id') or '').strip()}' bbox={(x, y, w, h)}")
            if l is None:
                l, t, r, b = x, y, x + w, y + h
            else:
                l = min(l, x)
                t = min(t, y)
                r = max(r, x + w)
                b = max(b, y + h)
        bx, by, bw, bh = l, t, (r - l), (b - t)
        _l.i(f"[spritesheet_gui] union visual bbox={(bx, by, bw, bh)}")
        if bw <= 0 or bh <= 0:
            raise inkex.AbortExtension("Selection has invalid visual bbox.")

        input_svg = getattr(self.options, "input_file", None) or ""
        temp_doc_svg = None
        # Use the original SVG as the primary export input. Copying it to %TEMP%
        # breaks relative image hrefs, which is exactly what grouped image
        # selections need for the preview.
        if input_svg and os.path.isfile(input_svg):
            preview_svg = input_svg
        else:
            temp_doc_svg = _write_temp_svg(self.document)
            preview_svg = temp_doc_svg or ""
        _l.i(
            "[spritesheet_gui] preview input "
            f"input_svg='{input_svg}' temp_doc='{temp_doc_svg or ''}' preview_svg='{preview_svg or ''}' "
            f"preview_exists={bool(preview_svg and os.path.isfile(preview_svg))}"
        )
        _l.i(f"[spritesheet_gui] selected group id='{selected_id}'")

        worker_svg = _copy_svg_for_worker(preview_svg)
        script = os.path.abspath(__file__)
        if not _launch_detached_worker(script, worker_svg or preview_svg, selected_id, bw, bh):
            raise inkex.AbortExtension("Could not launch Spritesheet GUI worker.")
        if temp_doc_svg and os.path.isfile(temp_doc_svg):
            try:
                os.remove(temp_doc_svg)
            except Exception:
                pass
        _l.i("[spritesheet_gui] detached worker launched, extension returns immediately")
        _l.d("[spritesheet_gui] closed_no_svg_changes=1 total_effect_ms=", int((perf_counter() - t_effect) * 1000))


def _launch_detached_worker(script_path: str, svg_path: str, selected_id: str, bw: float, bh: float) -> bool:
    script = os.path.abspath(str(script_path or ""))
    svg_file = os.path.abspath(str(svg_path or ""))
    if not script or not os.path.isfile(script):
        return False
    if not svg_file or not os.path.isfile(svg_file):
        return False
    sid = str(selected_id or "").strip()
    if not sid:
        return False
    args_tail = [
        script,
        "--pnpink-spritesheet-worker",
        "--svg-path",
        svg_file,
        "--selected-id",
        sid,
        "--bbox-w",
        str(float(bw)),
        "--bbox-h",
        str(float(bh)),
    ]
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    for py in DMIPC.candidate_python_launchers():
        try:
            proc = subprocess.Popen(
                [py] + args_tail,
                cwd=os.path.dirname(script),
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
            proc.returncode = 0
            _l.i(f"[spritesheet_gui] worker launched python='{py}' svg='{svg_file}' id='{sid}'")
            return True
        except Exception:
            continue
    return False


def _run_detached_worker(argv: list[str]) -> int:
    def _arg(name: str, default: str = "") -> str:
        try:
            idx = argv.index(name)
            if idx + 1 < len(argv):
                return str(argv[idx + 1] or "")
        except ValueError:
            pass
        return default

    svg_path = os.path.abspath(_arg("--svg-path", ""))
    selected_id = _arg("--selected-id", "").strip()
    bw = float(_arg("--bbox-w", "1") or "1")
    bh = float(_arg("--bbox-h", "1") or "1")
    if not svg_path or not os.path.isfile(svg_path):
        _l.w(f"[spritesheet_gui] worker invalid svg='{svg_path}'")
        return 2
    if not selected_id:
        _l.w("[spritesheet_gui] worker missing selected id")
        return 2

    try:
        with open(svg_path, "rb") as fh:
            raw = fh.read()
        doc = inkex.load_svg(raw)
        svgdoc = doc.getroot()
    except Exception as ex:
        _l.w(f"[spritesheet_gui] worker load svg failed: {ex}")
        return 2

    inkscape_exe = INKSCAPE.find_executable()
    shell_exe = INKSCAPE.shell_executable(inkscape_exe)
    _l.i(f"[spritesheet_gui] worker preview inkscape='{inkscape_exe or ''}' shell='{shell_exe or ''}'")
    preview_png = _try_export_png_id(svg_path, selected_id, shell_exe)
    if not preview_png:
        _l.w(f"[spritesheet_gui] worker export failed id='{selected_id}'")
        return 3

    # Convert exported PNG pixels back to Inkscape user units (96 dpi) so
    # preset mm values still match the real selection dimensions.
    try:
        from PIL import Image

        with Image.open(preview_png) as im:
            wpx, hpx = im.size
        if int(wpx) > 0 and int(hpx) > 0:
            scale = float(INKSCAPE_DOC_DPI) / float(PREVIEW_EXPORT_DPI)
            bw = float(wpx) * scale
            bh = float(hpx) * scale
            _l.i(f"[spritesheet_gui] worker png size={wpx}x{hpx} logical={bw:.3f}x{bh:.3f}")
    except Exception:
        pass

    try:
        gui = SpriteSheetGui(
            bw=max(1.0, bw),
            bh=max(1.0, bh),
            initial_spec=DEFAULT_GUI_SPEC,
            svgdoc=svgdoc,
            initial_image=preview_png,
        )
        gui._cleanup_paths.append(preview_png)
        gui._cleanup_paths.append(svg_path)
        gui.run()
        return 0
    except Exception as ex:
        _l.w(f"[spritesheet_gui] worker gui failed: {ex}")
        try:
            os.remove(preview_png)
        except Exception:
            pass
        try:
            os.remove(svg_path)
        except Exception:
            pass
        return 4


if __name__ == "__main__":
    if "--pnpink-spritesheet-worker" in sys.argv:
        raise SystemExit(_run_detached_worker(sys.argv[1:]))
    SpriteSheetGUIExtension().run()
