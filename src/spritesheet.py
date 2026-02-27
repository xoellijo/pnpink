#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
"""
spritesheet.py ? PnPInk (v0.4.2)

- Draw in doc-space: overlay group uses transform = (layer CTM)^-1.
- mm?px with inkex.units.convert_unit(..., svgdoc).
- preset/custom: fit with floor; if it does not fit, do not draw.
- Fix linked images before bbox.
- show_text: OFF = draw; ON = print .Layout.
"""

import inkex
import svg as SVG
from inkex import Transform
import svg
import layouts
import log as LOG
_l = LOG
from math import floor

NS = inkex.NSS

LOG_PREFIX = "[spritesheet]"
PREVIEW_GROUP_ID = "pnpink_spritesheet_preview"
PREVIEW_LABEL    = "Spritesheet Preview"

PREVIEW_STYLE = (
    "fill:#ff0000;fill-opacity:0.30;"
    "stroke:#ff0000;stroke-width:0.3mm;"
    "stroke-linejoin:miter;stroke-linecap:butt;"
    "shape-rendering:crispEdges;"
)
PREVIEW_GROUP_STYLE = "mix-blend-mode:multiply; pointer-events:none;"

# ---------- conversion helpers (document-aware) ----------
def mm_to_px(mm: float, svgdoc) -> float:
    return float(inkex.units.convert_unit(f"{mm}mm", "px", svgdoc))

def px_to_mm(px: float, svgdoc) -> float:
    return float(inkex.units.convert_unit(f"{px}px", "mm", svgdoc))

# ---------- overlay helpers ----------
def _remove_old_preview(root):
    removed = 0
    for g in root.xpath(f".//svg:g[@id='{PREVIEW_GROUP_ID}']", namespaces=NS):
        p = g.getparent()
        if p is not None:
            p.remove(g); removed += 1
    for g in root.xpath(f".//svg:g[@inkscape:label='{PREVIEW_LABEL}']", namespaces=NS):
        if g.get("id") != PREVIEW_GROUP_ID:
            p = g.getparent()
            if p is not None:
                p.remove(g); removed += 1
    _l.d(f"{LOG_PREFIX} removed overlay groups: {removed}")

def _current_layer(svgdoc, selection):
    layer = svgdoc.get_current_layer()
    if layer is None and selection:
        cur = selection[0]
        while cur is not None:
            if cur.tag == inkex.addNS('g','svg') and cur.get(inkex.addNS('groupmode','inkscape')) == 'layer':
                layer = cur; break
            cur = cur.getparent()
    if layer is None:
        layer = svgdoc.document.getroot()
    return layer

def _ensure_preview_group(svgdoc, selection):
    """
    Create the overlay group on the layer, with transform = (layer CTM)^-1 to draw in doc-space.
    In this inkex version there is no Transform.to_svg(), so we use str(Minv).
    """
    layer = _current_layer(svgdoc, selection)
    # Layer CTM composed and inverted
    try:
        M_layer = layer.composed_transform()
    except Exception:
        M_layer = Transform()
    try:
        Minv = M_layer.inverse()
    except Exception:
        Minv = Transform()  # identidad si no hay inversa

    g = SVG.etree.SubElement(layer, inkex.addNS('g','svg'))
    g.set('id', PREVIEW_GROUP_ID)
    g.set(inkex.addNS('label','inkscape'), PREVIEW_LABEL)
    g.set('style', PREVIEW_GROUP_STYLE)
    # Always apply the inverse; if identity, it will be "matrix(1,0,0,1,0,0)"
    try:
        g.set('transform', str(Minv))
    except Exception:
        g.set('transform', str(Transform()))  # identidad
    return g

# ---------- layout strings (mm) ----------
def _fmt_pair_mm(x_mm, y_mm): return f"{x_mm:.1f}×{y_mm:.1f}"
def _fmt_quad_mm(t,l,b,r):    return f"{t:.1f},{l:.1f},{b:.1f},{r:.1f}"

def _layout_strings_mm(rows, cols, tile_w_mm, tile_h_mm, mt, ml, mb, mr, gv, gh):
    long_s = (".Layout{grid, "
              f"rows={rows}, cols={cols}, "
              f"tile={_fmt_pair_mm(tile_w_mm, tile_h_mm)}, "
              f"margin_top={mt:.1f}mm, margin_left={ml:.1f}mm, "
              f"margin_bottom={mb:.1f}mm, margin_right={mr:.1f}mm, "
              f"gap_v={gv:.1f}mm, gap_h={gh:.1f}mm, "
              "origin=NW, order=LR-TB, shape=rect}")
    short_s = (".Layout{grid rows=%d cols=%d tile=%s "
               "mt=%.1fmm ml=%.1fmm mb=%.1fmm mr=%.1fmm gv=%.1fmm gh=%.1fmm NW LR-TB rect}" %
               (rows, cols, _fmt_pair_mm(tile_w_mm, tile_h_mm),
                mt, ml, mb, mr, gv, gh))
    mini_s = (".Layout{%s %s m=%s g=%s NW LR-TB rect}" %
              (f"{rows}×{cols}",
               _fmt_pair_mm(tile_w_mm, tile_h_mm),
               _fmt_quad_mm(mt, ml, mb, mr),
               _fmt_pair_mm(gv, gh)))
    return long_s, short_s, mini_s

# ---------- main effect ----------
class SpriteSheet(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab", type=str, default="run")
        # margins (mm)
        pars.add_argument("--margin_top",    type=float, default=5.0)
        pars.add_argument("--margin_left",   type=float, default=5.0)
        pars.add_argument("--margin_bottom", type=float, default=5.0)
        pars.add_argument("--margin_right",  type=float, default=5.0)
        # gaps (mm)
        pars.add_argument("--gap_vertical",   type=float, default=2.0)   # between rows
        pars.add_argument("--gap_horizontal", type=float, default=2.0)   # between cols
        # card mode
        pars.add_argument("--card_mode", type=str, default="auto")  # auto|preset|custom
        pars.add_argument("--cols", type=int, default=6)            # only auto
        pars.add_argument("--rows", type=int, default=4)            # only auto
        pars.add_argument("--card_preset", type=str, default="Standard")
        pars.add_argument("--card_w_mm", type=float, default=63.0)  # only custom
        pars.add_argument("--card_h_mm", type=float, default=88.0)  # only custom
        # output
        pars.add_argument("--show_text", type=inkex.Boolean, default=False)

    def effect(self):
        svgdoc = self.svg
        root = self.document.getroot()

        # 1) Fix linked images (so visual_bbox works on <image>)
        try:
            svg_path = self.options.input_file or getattr(self.svg, "path", None)
            fixed = svg.absolutize_all_linked_images(self.svg, svg_path)
            if fixed:
                _l.d(f"{LOG_PREFIX} absolutized {fixed} linked image(s)")
        except Exception as e:
            _l.w(f"{LOG_PREFIX} absolutize_all_linked_images failed: {e}")

        # 2) Selection
        selection = list(svgdoc.selection or [])
        if not selection:
            raise inkex.AbortExtension("Select at least one element (image, group or node).")

        # 3) Combined visual bbox (px) in document space
        L=T=R=B=None
        for n in selection:
            x, y, w, h = svg.visual_bbox(n)
            if L is None:
                L, T, R, B = x, y, x+w, y+h
            else:
                L = min(L, x); T = min(T, y); R = max(R, x+w); B = max(B, y+h)
        bx, by, bw, bh = L, T, (R-L), (B-T)

        # 4) Params (mm)
        mt_mm = max(0.0, float(self.options.margin_top))
        ml_mm = max(0.0, float(self.options.margin_left))
        mb_mm = max(0.0, float(self.options.margin_bottom))
        mr_mm = max(0.0, float(self.options.margin_right))
        gv_mm = max(0.0, float(self.options.gap_vertical))    # between rows
        gh_mm = max(0.0, float(self.options.gap_horizontal))  # between cols

        # 5) mm -> px (document-aware)
        mt = mm_to_px(mt_mm, svgdoc); ml = mm_to_px(ml_mm, svgdoc)
        mb = mm_to_px(mb_mm, svgdoc); mr = mm_to_px(mr_mm, svgdoc)
        gv = mm_to_px(gv_mm, svgdoc); gh = mm_to_px(gh_mm, svgdoc)

        # 6) Usable area (px) in doc space
        content_w = max(0.0, bw - (ml + mr))
        content_h = max(0.0, bh - (mt + mb))
        if content_w <= 0 or content_h <= 0:
            _remove_old_preview(root)
            _l.w(f"{LOG_PREFIX} nothing to draw: margins too large for bbox")
            return

        mode = (self.options.card_mode or "auto").strip().lower()

        # 7) Compute tile and rows/cols (all in doc-space px)
        if mode == "preset":
            name = self.options.card_preset or "Standard"
            if name not in layouts.CARD_SIZES_MM:
                name = "Standard"
            cw_mm, ch_mm = layouts.CARD_SIZES_MM[name]
            tw = mm_to_px(cw_mm, svgdoc)
            th = mm_to_px(ch_mm, svgdoc)
            cols = max(0, floor((content_w + gh) / (tw + gh)))
            rows = max(0, floor((content_h + gv) / (th + gv)))
            tile_w_mm, tile_h_mm = cw_mm, ch_mm

        elif mode == "custom":
            cw_mm = max(1.0, float(self.options.card_w_mm))
            ch_mm = max(1.0, float(self.options.card_h_mm))
            tw = mm_to_px(cw_mm, svgdoc)
            th = mm_to_px(ch_mm, svgdoc)
            cols = max(0, floor((content_w + gh) / (tw + gh)))
            rows = max(0, floor((content_h + gv) / (th + gv)))
            tile_w_mm, tile_h_mm = cw_mm, ch_mm

        else:  # auto
            cols = max(1, int(self.options.cols))
            rows = max(1, int(self.options.rows))
            tw = (content_w - (cols - 1) * gh) / cols
            th = (content_h - (rows - 1) * gv) / rows
            if tw <= 0 or th <= 0:
                _remove_old_preview(root)
                _l.w(f"{LOG_PREFIX} auto: non-positive tile; adjust params")
                return
            tile_w_mm = px_to_mm(tw, svgdoc)
            tile_h_mm = px_to_mm(th, svgdoc)

        # 8) .Layout strings (mm)
        L_long, L_short, L_mini = _layout_strings_mm(
            rows, cols, tile_w_mm, tile_h_mm,
            mt_mm, ml_mm, mb_mm, mr_mm, gv_mm, gh_mm
        )

        # 9) Action
        _remove_old_preview(root)
        if bool(self.options.show_text):
            inkex.errormsg("Spritesheet .Layout (long):  " + L_long)
            inkex.errormsg("Spritesheet .Layout (short): " + L_short)
            inkex.errormsg("Spritesheet .Layout (mini):  " + L_mini)
            return

        # If nothing fits (preset/custom), skip drawing
        if rows <= 0 or cols <= 0:
            _l.w(f"{LOG_PREFIX} nothing fits with given card size/margins/gaps; drawing skipped.")
            return

        # 10) Draw in doc-space thanks to Minv of the layer
        gprev = _ensure_preview_group(svgdoc, selection)
        for r in range(rows):
            y = by + mt + r * (th + gv)   # doc-space
            for c in range(cols):
                x = bx + ml + c * (tw + gh)  # doc-space
                SVG.etree.SubElement(gprev, inkex.addNS('rect','svg'), {
                    'x': f"{x:.6f}",
                    'y': f"{y:.6f}",
                    'width':  f"{max(0.0, tw):.6f}",
                    'height': f"{max(0.0, th):.6f}",
                    'style': PREVIEW_STYLE
                })

if __name__ == "__main__":
    SpriteSheet().run()
