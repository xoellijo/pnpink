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
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass
from math import floor
from pathlib import Path
from time import perf_counter
from tkinter import ttk

import inkex

import const as CONST
import log as LOG
import svg as SVG
import svg

_l = LOG


def mm_to_px(mm: float, svgdoc) -> float:
    return float(inkex.units.convert_unit(f"{mm}mm", "px", svgdoc))


def px_to_mm(px: float, svgdoc) -> float:
    return float(inkex.units.convert_unit(f"{px}px", "mm", svgdoc))


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


def _layout_expr(rows, cols, mt, ml, mb, mr, gv, gh):
    # pattern uses cols x rows
    parts = [f"p={cols}x{rows}"]
    # gaps: x(horizontal)=gh, y(vertical)=gv
    if abs(gh) > 1e-9 or abs(gv) > 1e-9:
        if abs(gh - gv) < 1e-9:
            parts.append(f"g={_fmt_num(gh)}")
        else:
            parts.append(f"g=[{_fmt_num(gh)} {_fmt_num(gv)}]")
    # border: only when non-zero
    if any(abs(v) > 1e-9 for v in (mt, ml, mb, mr)):
        parts.append(f"b=[{_fmt_num(mt)} {_fmt_num(ml)} {_fmt_num(mb)} {_fmt_num(mr)}]")
    return ".Layout{" + " ".join(parts) + "}"


def _compute_grid(spec, bw, bh, svgdoc):
    mt_mm, ml_mm, mb_mm, mr_mm = _expand_margin_spec(spec["margin"])
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


def _find_inkscape_exe() -> str | None:
    exe = shutil.which("inkscape")
    if exe:
        p = Path(exe)
        if p.suffix.lower() == ".com":
            alt = p.with_suffix(".exe")
            if alt.is_file():
                return str(alt)
        return exe
    pyexe = os.path.abspath(sys.executable)
    bin_dir = os.path.dirname(pyexe)
    candidates = [
        os.path.join(bin_dir, "inkscape.exe"),
        os.path.join(os.path.dirname(bin_dir), "inkscape.exe"),
        os.path.join(os.path.dirname(bin_dir), "bin", "inkscape.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _run_quiet(cmd):
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": False,
        "text": True,
        "timeout": 25,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = si
    return subprocess.run(cmd, **kwargs)


def _try_export_png_area(input_svg: str, bx, by, bw, bh, inkscape_exe: str | None) -> str | None:
    if not input_svg or not os.path.isfile(input_svg) or not inkscape_exe:
        return None
    fd, out_png = tempfile.mkstemp(prefix="pnpink_sprite_area_", suffix=".png")
    os.close(fd)
    x0, y0 = bx, by
    x1, y1 = bx + bw, by + bh
    cmd = [
        inkscape_exe,
        input_svg,
        f"--export-area={x0}:{y0}:{x1}:{y1}",
        "--export-type=png",
        f"--export-filename={out_png}",
    ]
    try:
        p = _run_quiet(cmd)
        if p.returncode == 0 and os.path.isfile(out_png):
            return out_png
    except Exception:
        pass
    try:
        os.remove(out_png)
    except Exception:
        pass
    return None


def _try_export_png_id(input_svg: str, node_id: str | None, inkscape_exe: str | None) -> str | None:
    if not input_svg or not os.path.isfile(input_svg) or not inkscape_exe or not node_id:
        return None
    fd, out_png = tempfile.mkstemp(prefix="pnpink_sprite_id_", suffix=".png")
    os.close(fd)
    cmd = [
        inkscape_exe,
        input_svg,
        f"--export-id={node_id}",
        "--export-id-only",
        "--export-type=png",
        f"--export-filename={out_png}",
    ]
    try:
        p = _run_quiet(cmd)
        if p.returncode == 0 and os.path.isfile(out_png):
            return out_png
    except Exception:
        pass
    try:
        os.remove(out_png)
    except Exception:
        pass
    return None


def _try_selection_image_file(selection, input_svg: str) -> str | None:
    if len(selection) != 1:
        return None
    node = selection[0]
    if not (getattr(node, "tag", "") or "").endswith("image"):
        return None
    href = node.get(inkex.addNS("href", "xlink")) or node.get("href") or ""
    href = (href or "").strip()
    if not href or href.startswith("data:") or href.startswith("http://") or href.startswith("https://"):
        return None
    p = Path(href)
    if p.is_file():
        return str(p)
    if input_svg:
        cand = (Path(input_svg).resolve().parent / href).resolve()
        if cand.is_file():
            return str(cand)
    return None


def _write_temp_svg(doc_tree) -> str | None:
    try:
        fd, out_svg = tempfile.mkstemp(prefix="pnpink_tmp_doc_", suffix=".svg")
        os.close(fd)
        doc_tree.write(out_svg, encoding="utf-8", xml_declaration=True)
        return out_svg
    except Exception:
        return None


@dataclass
class GuiResult:
    layout_text: str | None


class SpriteSheetGui:
    def __init__(self, bw, bh, initial_spec, svgdoc, initial_image: str | None = None, preview_loader=None):
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
        self._last_spec_key = None
        self._last_grid = None
        self._pending_redraw = None
        self._preview_loader = preview_loader
        self._cleanup_paths = []

        self.root = tk.Tk()
        self.root.title("PnPInk Spritesheet")
        self.root.geometry("980x620")
        self.root.minsize(820, 520)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.vars = {
            "card_mode": tk.StringVar(value=initial_spec["card_mode"]),
            "cols": tk.StringVar(value=str(initial_spec["cols"])),
            "rows": tk.StringVar(value=str(initial_spec["rows"])),
            "card_preset": tk.StringVar(value=initial_spec["card_preset"]),
            "card_w_mm": tk.StringVar(value=str(initial_spec["card_w_mm"])),
            "card_h_mm": tk.StringVar(value=str(initial_spec["card_h_mm"])),
            "margin": tk.StringVar(value=initial_spec["margin"]),
            "gap": tk.StringVar(value=initial_spec["gap"]),
            "layout_text": tk.StringVar(value=""),
            "status": tk.StringVar(value="Ready"),
        }

        self._build_ui()
        if initial_image:
            self._load_image(initial_image)
        if self._preview_loader and not initial_image:
            self.vars["status"].set("Loading preview image...")
            self.root.after(40, self._start_preview_loader)
        self.root.after(80, self.request_redraw)

    def _build_ui(self):
        wrap = ttk.Frame(self.root, padding=6)
        wrap.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(wrap, padding=4)
        left.pack(side=tk.LEFT, fill=tk.Y)
        right = ttk.Frame(wrap, padding=4)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        ttk.Label(left, text="Layout mode").pack(anchor="w")
        mode = ttk.Combobox(left, state="readonly", textvariable=self.vars["card_mode"], values=["auto", "preset", "custom"], width=14)
        mode.pack(anchor="w", pady=(0, 6))
        mode.bind("<<ComboboxSelected>>", lambda e: self.request_redraw())

        ttk.Label(left, text="cols").pack(anchor="w")
        ttk.Entry(left, textvariable=self.vars["cols"], width=10).pack(anchor="w")
        ttk.Label(left, text="rows").pack(anchor="w")
        ttk.Entry(left, textvariable=self.vars["rows"], width=10).pack(anchor="w")

        ttk.Label(left, text="preset").pack(anchor="w", pady=(6, 0))
        preset = ttk.Combobox(left, state="readonly", textvariable=self.vars["card_preset"], values=sorted(CONST.CARD_SIZES_MM.keys()), width=16)
        preset.pack(anchor="w")
        preset.bind("<<ComboboxSelected>>", lambda e: self.request_redraw())

        ttk.Label(left, text="custom w (mm)").pack(anchor="w", pady=(6, 0))
        ttk.Entry(left, textvariable=self.vars["card_w_mm"], width=10).pack(anchor="w")
        ttk.Label(left, text="custom h (mm)").pack(anchor="w")
        ttk.Entry(left, textvariable=self.vars["card_h_mm"], width=10).pack(anchor="w")

        ttk.Label(left, text="border b (mm, space-separated)").pack(anchor="w", pady=(8, 0))
        ttk.Entry(left, textvariable=self.vars["margin"], width=18).pack(anchor="w")
        ttk.Label(left, text="b: `2` | `2 3` | `2 3 4 5`").pack(anchor="w")
        ttk.Label(left, text="gaps g (mm, space-separated)").pack(anchor="w", pady=(4, 0))
        ttk.Entry(left, textvariable=self.vars["gap"], width=18).pack(anchor="w")
        ttk.Label(left, text="g: `2` | `2 3`").pack(anchor="w")

        ttk.Label(left, text="Layout").pack(anchor="w", pady=(10, 0))
        ttk.Entry(left, textvariable=self.vars["layout_text"], width=38).pack(anchor="w")
        ttk.Button(left, text="Copy", command=self._copy_layout).pack(anchor="w", pady=(4, 0))
        ttk.Label(left, textvariable=self.vars["status"], foreground="#444").pack(anchor="w", pady=(10, 0))

        self.canvas = tk.Canvas(right, background="#f7f7f7", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._on_wheel_linux(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._on_wheel_linux(e, -1))
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)

        for k, v in self.vars.items():
            if k not in ("status", "layout_text") and isinstance(v, tk.StringVar):
                v.trace_add("write", lambda *_: self.request_redraw())

    def _load_image(self, path: str | None):
        if not path or not os.path.isfile(path):
            return
        try:
            from PIL import Image
            self._pil_base = Image.open(path).convert("RGBA")
            self._photo = None
            self._last_pil_size = None
            return
        except Exception:
            pass
        try:
            self._photo = tk.PhotoImage(file=path)
            self._pil_base = None
        except Exception:
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
        self._did_initial_fit = True

    def _on_canvas_configure(self, _event):
        if not self._did_initial_fit and self.canvas.winfo_width() > 100 and self.canvas.winfo_height() > 100:
            self._fit_view()
        self.request_redraw()

    def _spec(self):
        return {k: v.get().strip() for k, v in self.vars.items() if k not in ("status", "layout_text")}

    def _parse_numeric(self, s, default):
        try:
            return float(s)
        except Exception:
            return default

    def _normalized_spec(self):
        s = self._spec()
        return {
            "card_mode": s["card_mode"] or "auto",
            "cols": max(1, int(self._parse_numeric(s["cols"], 6))),
            "rows": max(1, int(self._parse_numeric(s["rows"], 4))),
            "card_preset": s["card_preset"] or "Standard",
            "card_w_mm": max(1.0, self._parse_numeric(s["card_w_mm"], 63.0)),
            "card_h_mm": max(1.0, self._parse_numeric(s["card_h_mm"], 88.0)),
            "margin": s["margin"],
            "gap": s["gap"],
        }

    def _draw_background(self):
        if self._pil_base is not None:
            from PIL import ImageTk
            w = max(1, int(self.bw * self.zoom))
            h = max(1, int(self.bh * self.zoom))
            if self._pil_image is None or self._last_pil_size != (w, h):
                # Use a faster resampling to improve zoom responsiveness.
                resampling = getattr(Image, "Resampling", Image)
                resized = self._pil_base.resize((w, h), resample=getattr(resampling, "BILINEAR", 2))
                self._pil_image = ImageTk.PhotoImage(resized)
                self._last_pil_size = (w, h)
            if self._img_id is None:
                self._img_id = self.canvas.create_image(self.pan_x, self.pan_y, anchor="nw", image=self._pil_image)
            else:
                self.canvas.coords(self._img_id, self.pan_x, self.pan_y)
                self.canvas.itemconfigure(self._img_id, image=self._pil_image)
            return
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
                grid["ml_mm"],
                grid["mb_mm"],
                grid["mr_mm"],
                grid["gv_mm"],
                grid["gh_mm"],
            )
        )
        self.vars["status"].set(f"{grid['cols']}x{grid['rows']} slots")
        dt = int((perf_counter() - t0) * 1000)
        if dt >= 25:
            _l.d("[spritesheet_gui] redraw_ms=", dt, "slots=", len(grid["rects"]))

    def _zoom_at(self, factor, x, y):
        old = self.zoom
        new = max(0.10, min(12.0, old * factor))
        wx = (x - self.pan_x) / old
        wy = (y - self.pan_y) / old
        self.zoom = new
        self.pan_x = x - wx * new
        self.pan_y = y - wy * new
        self.request_redraw()

    def _on_wheel(self, event):
        self._zoom_at(1.1 if event.delta > 0 else 1 / 1.1, event.x, event.y)

    def _on_wheel_linux(self, event, direction):
        self._zoom_at(1.1 if direction > 0 else 1 / 1.1, event.x, event.y)

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

    def _copy_layout(self):
        txt = self.vars["layout_text"].get().strip()
        if not txt:
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            self.vars["status"].set("Layout copied to clipboard")
        except Exception:
            self.vars["status"].set("Copy failed")

    def _start_preview_loader(self):
        def _worker():
            t0 = perf_counter()
            try:
                path, cleanup = self._preview_loader()
            except Exception:
                path, cleanup = (None, [])
            _l.d("[spritesheet_gui] async_preview_ms=", int((perf_counter() - t0) * 1000), "path=", path or "none")
            self.root.after(0, lambda: self._on_preview_ready(path, cleanup))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_preview_ready(self, path, cleanup):
        if cleanup:
            self._cleanup_paths.extend(cleanup)
        if path and os.path.isfile(path):
            self._load_image(path)
            self._did_initial_fit = False
            self._fit_view()
            self.request_redraw()
        else:
            self.vars["status"].set("Preview image not available")

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
        if not selection:
            raise inkex.AbortExtension("Select at least one element (image, group or node).")

        l = t = r = b = None
        for n in selection:
            x, y, w, h = svg.visual_bbox(n)
            if l is None:
                l, t, r, b = x, y, x + w, y + h
            else:
                l = min(l, x)
                t = min(t, y)
                r = max(r, x + w)
                b = max(b, y + h)
        bx, by, bw, bh = l, t, (r - l), (b - t)
        if bw <= 0 or bh <= 0:
            raise inkex.AbortExtension("Selection has invalid visual bbox.")

        input_svg = getattr(self.options, "input_file", None) or ""
        temp_doc_svg = None
        if not input_svg or not os.path.isfile(input_svg):
            temp_doc_svg = _write_temp_svg(self.document)
            input_svg = temp_doc_svg or ""

        # Fast path: selection is linked image file.
        direct_img = _try_selection_image_file(selection, input_svg)
        single_id = (selection[0].get("id") or "").strip() if len(selection) == 1 else None

        def _preview_loader():
            if direct_img and os.path.isfile(direct_img):
                _l.d("[spritesheet_gui] using direct image file:", direct_img)
                return direct_img, []
            cleanup = []
            inkscape_exe = _find_inkscape_exe()
            # More robust with text/groups than area bbox.
            out = _try_export_png_id(input_svg, single_id, inkscape_exe)
            if not out:
                out = _try_export_png_area(input_svg, bx, by, bw, bh, inkscape_exe)
            if out:
                cleanup.append(out)
            _l.d("[spritesheet_gui] preview_png=", out or "none", "inkscape=", inkscape_exe or "none")
            return out, cleanup

        initial = {
            "card_mode": "auto",
            "cols": 6,
            "rows": 4,
            "card_preset": "Standard",
            "card_w_mm": 63.0,
            "card_h_mm": 88.0,
            "margin": "5",
            "gap": "2",
        }

        gui = SpriteSheetGui(
            bw=bw,
            bh=bh,
            initial_spec=initial,
            svgdoc=svgdoc,
            initial_image=direct_img if direct_img else None,
            preview_loader=_preview_loader if not direct_img else None,
        )
        result = gui.run()

        if temp_doc_svg:
            try:
                os.remove(temp_doc_svg)
            except Exception:
                pass

        # No SVG changes by design.
        if result.layout_text:
            inkex.utils.errormsg("Spritesheet layout (copied): " + result.layout_text)
        _l.d("[spritesheet_gui] closed_no_svg_changes=1 total_effect_ms=", int((perf_counter() - t_effect) * 1000))


if __name__ == "__main__":
    SpriteSheetGUIExtension().run()
