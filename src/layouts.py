# -*- coding: utf-8 -*-
"""
# Changelog: allow grid flip on both axes with 'hv'.
# Changelog: split layout gaps into gaps + offset properties.
# Changelog: remove legacy 6-value gaps handling.
layouts.py — v3.3.0

Objetivo (PnPInk):
- DSL "dumb": gaps/shift/border/etc are carried as raw text tokens.
- The logic (mm, %, units, offsets w1/h1/w2/h2) lives here.
- Maintain compatibility with current deckmaker.py.

API usada por deckmaker.py:
  - PageSpec, CardSpec, LayoutSpec, GapsMM, Resolved
  - resolve(page, card, layout, gaps, doc_page_mm) -> Resolved
  - resolve_card_size_px(card, tmpl_w_px, tmpl_h_px, px_per_mm) -> (w_px, h_px)
  - parse_and_resolve_page(text, current_page, doc_page_mm) -> PageSpec
  - apply_layout_spec((page, card, layout, gaps), ls) -> (page, card, layout, gaps)
  - plan_grid(..., gaps_px=(gh,gv), gaps_px6=None, layout=..., content_origin_px=..., content_wh_px=...)
  - gaps6_to_px(seq, base_w_px, base_h_px, px_per_mm) -> (gx,gy,w1,h1,w2,h2)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Any
import re

import log as LOG
_l = LOG
import dsl as DSL
import svg as SVG
import const as CONST


# ----------------------------- Data model -------------------------------------

@dataclass
class PageSpec:
    name: Optional[str] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    landscape: bool = False
    # top,right,bottom,left (negative => inward margin)
    border_mm: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    def resolved_size_mm(self, doc_page_mm: Tuple[float, float]) -> Tuple[float, float]:
        w, h = self.width_mm, self.height_mm
        if (w is None or h is None) and self.name:
            p = CONST.get_page_size_preset(self.name)
            if p:
                w, h = p
        if w is None or h is None:
            w, h = doc_page_mm
        if self.landscape and h > w:
            w, h = h, w
        return float(w), float(h)

    def margins_mm(self):
        """Return (top,right,bottom,left) in mm *with sign*.

        PnPInk convention (existing):
          - negative border: reduces usable area (inward margin).
          - positive border: expands usable area (outward bleed).

        This function returns the offsets used to compute the content rectangle. Therefore:
          - border=-10  -> offset=+10 (inset)
          - border=+10  -> offset=-10 (outset)
        """
        t, r, b, l = (self.border_mm + [0, 0, 0, 0])[:4]

        def _off(v: float) -> float:
            try:
                v = float(v)
            except Exception:
                v = 0.0
            # signo invertido: negativo reduce (offset +), positivo expande (offset -)
            return -v

        # Note: svg.coerce_margins_mm expects (left, top, right, bottom).
        return (_off(l), _off(t), _off(r), _off(b))


@dataclass
class CardSpec:
    name: Optional[str] = None
    width_mm: Optional[float] = None
    height_mm: Optional[float] = None
    landscape: bool = False


@dataclass
class LayoutSpec:
    cols: Optional[int] = None
    rows: Optional[int] = None
    sweep_rows_first: bool = True
    invert_cols: bool = False
    invert_rows: bool = False
    # tokens raw: ["3","2%","-25%","50"] etc.
    gaps: List[str] = field(default_factory=list)
    # offset raw tokens: ["w1","h1","w2","h2"]
    offset: List[str] = field(default_factory=list)
    # Smart shapes (MVP): do not alter card sizing; only adjust gaps additively in deckmaker.
    # Values: 'hexgrid' | 'hextile' | 'hextiles'
    smart_shape: Optional[str] = None


@dataclass
class GapsMM:
    # basic gaps (compat): horizontal/vertical in mm
    h: float = 0.0
    v: float = 0.0


@dataclass
class Resolved:
    page: PageSpec
    card: CardSpec
    layout: LayoutSpec
    gaps: GapsMM


# ----------------------------- Utils ------------------------------------------

def _as_token_list(v: Any) -> List[str]:
    """
    Normaliza gaps recibido desde DSL:
      - None/"": []
      - "3 8" / "[3 8]" / "3,8" => ["3","8"]
      - lista/tupla => ["...","..."]
      - numbers => ["3"]
    """
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip() != ""]
    if isinstance(v, (int, float)):
        return [str(v)]
    s = str(v).strip()
    if not s:
        return []
    s = s.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1].strip()
    # split by comma or whitespace
    parts = [p.strip() for p in re.split(r"[,\s]+", s) if p.strip()]
    return parts


def _merge_gaps_offset_tokens(gaps_seq: List[str], offset_seq: List[str]) -> List[str]:
    seq = list(gaps_seq or [])
    off = list(offset_seq or [])
    if not off:
        return seq
    if len(seq) == 0:
        seq = ["0", "0"]
    elif len(seq) == 1:
        seq = [seq[0], seq[0]]
    elif len(seq) > 2:
        seq = seq[:2]
    return seq + off


def layout_gaps_tokens(layout: LayoutSpec) -> List[str]:
    return _merge_gaps_offset_tokens(getattr(layout, "gaps", None) or [],
                                     getattr(layout, "offset", None) or [])


def _gaps6_mm(seq: List[str], card: CardSpec) -> Tuple[float, float, float, float, float, float]:
    """
    gaps tokens -> (x,y,w1,h1,w2,h2) en mm.
    - len=0 => todo 0
    - len=1 => y=x
    - w2/h2 por defecto -w1/-h1
    - % is evaluated against (card.width_mm/card.height_mm)
    """
    if not seq:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    base_w = float(card.width_mm) if card and card.width_mm is not None else None
    base_h = float(card.height_mm) if card and card.height_mm is not None else None
    if base_w is None or base_h is None:
        # Strict contract: percentages require a resolvable base size.
        if any("%" in str(t) for t in seq):
            raise ValueError(
                f"gaps: '%' requires a base card/shape size, but card.width_mm/height_mm are missing. gaps={seq}"
            )
        # No % involved: allow absolute units; base is irrelevant.
        base_w = float(base_w or 0.0)
        base_h = float(base_h or 0.0)

    x = seq[0]
    y = seq[1] if len(seq) >= 2 else seq[0]
    w1 = seq[2] if len(seq) >= 3 else "0"
    h1 = seq[3] if len(seq) >= 4 else "0"
    w2 = seq[4] if len(seq) >= 5 else None
    h2 = seq[5] if len(seq) >= 6 else None

    gx = SVG.measure_to_mm(x, base_mm=base_w)
    gy = SVG.measure_to_mm(y, base_mm=base_h)
    w1m = SVG.measure_to_mm(w1, base_mm=base_w)
    h1m = SVG.measure_to_mm(h1, base_mm=base_h)
    w2m = -w1m if w2 is None else SVG.measure_to_mm(w2, base_mm=base_w)
    h2m = -h1m if h2 is None else SVG.measure_to_mm(h2, base_mm=base_h)

    _l.d(f"[gaps] seq={seq} base=({base_w:.3f},{base_h:.3f})mm -> "
         f"gx={gx:.3f} gy={gy:.3f} w1={w1m:.3f} h1={h1m:.3f} w2={w2m:.3f} h2={h2m:.3f}")
    return float(gx), float(gy), float(w1m), float(h1m), float(w2m), float(h2m)


def gaps6_to_px(seq: List[str], base_w_px: float, base_h_px: float, px_per_mm: float) -> Tuple[float, float, float, float, float, float]:
    """
    gaps tokens -> (gx,gy,w1,h1,w2,h2) en px (doc units).
    - % relativo a base_w_px/base_h_px
    - unidades absolutas via SVG.measure_to_mm(...)*px_per_mm
    """
    if not seq:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    # base_mm to evaluate %, if the token uses %
    base_w_mm = float(base_w_px) / float(px_per_mm) if px_per_mm else None
    base_h_mm = float(base_h_px) / float(px_per_mm) if px_per_mm else None

    def _mm(tok: str, base_mm: Optional[float]) -> float:
        return float(SVG.measure_to_mm(tok, base_mm=base_mm))

    x = seq[0]
    y = seq[1] if len(seq) >= 2 else seq[0]
    w1 = seq[2] if len(seq) >= 3 else "0"
    h1 = seq[3] if len(seq) >= 4 else "0"
    w2 = seq[4] if len(seq) >= 5 else None
    h2 = seq[5] if len(seq) >= 6 else None

    gx = _mm(x, base_w_mm) * px_per_mm
    gy = _mm(y, base_h_mm) * px_per_mm
    w1x = _mm(w1, base_w_mm) * px_per_mm
    h1y = _mm(h1, base_h_mm) * px_per_mm
    w2x = (-w1x) if w2 is None else _mm(w2, base_w_mm) * px_per_mm
    h2y = (-h1y) if h2 is None else _mm(h2, base_h_mm) * px_per_mm

    return (float(gx), float(gy), float(w1x), float(h1y), float(w2x), float(h2y))


# ----------------------------- Resolve / apply --------------------------------

def resolve(page: PageSpec, card: CardSpec, layout: LayoutSpec, gaps: GapsMM, doc_page_mm: Tuple[float, float]) -> Resolved:
    page = page or PageSpec()
    card = card or CardSpec()
    layout = layout or LayoutSpec()
    gaps = gaps or GapsMM()

    # page size
    if (page.width_mm is None or page.height_mm is None) and page.name:
        sz = CONST.get_page_size_preset(page.name)
        if sz:
            page.width_mm, page.height_mm = sz
    if page.width_mm is None or page.height_mm is None:
        page.width_mm, page.height_mm = doc_page_mm

    # card size (preset)
    if card.name:
        sz = CONST.get_card_size_preset(card.name)
        if sz and (card.width_mm is None or card.height_mm is None):
            card.width_mm, card.height_mm = sz

    # basic gaps: gaps+offset (if present) wins
    _seq = layout_gaps_tokens(layout)
    if _seq:
        gx, gy, *_ = _gaps6_mm(_seq, card)
        gaps.h = gx
        gaps.v = gy
    else:
        gaps.h = float(gaps.h or 0.0)
        gaps.v = float(gaps.v if gaps.v is not None else gaps.h)

    return Resolved(page=page, card=card, layout=layout, gaps=gaps)


def resolve_card_size_px(card: CardSpec, tmpl_w_px: float, tmpl_h_px: float, px_per_mm: float) -> Tuple[float, float]:
    """
    Return final size in px (doc units).
    If card.width_mm/height_mm are defined → mm*px_per_mm.
    Si no → template.
    """
    if card and card.width_mm is not None and card.height_mm is not None:
        w_px = float(card.width_mm) * float(px_per_mm)
        h_px = float(card.height_mm) * float(px_per_mm)
    else:
        w_px, h_px = float(tmpl_w_px), float(tmpl_h_px)

    if card and getattr(card, "landscape", False) and h_px > w_px:
        w_px, h_px = h_px, w_px

    _l.d(f"[resolve_card_size_px] w={w_px:.2f}px h={h_px:.2f}px (px_per_mm={px_per_mm:.6f})")
    return w_px, h_px


def parse_and_resolve_page(text: str, current_page: PageSpec, doc_page_mm: Tuple[float, float]) -> PageSpec:
    """
    Soporta:
      - "{A4 b=[-10 -40 -5 -5]}" etc.
      - "{3}" / "{}" (page breaks) -> keep current_page
    """
    t = (text or "").strip()
    cmd = DSL.maybe_parse(t if t.startswith('{') else f'Page{{{t}}}')
    if cmd is None or cmd.name != "Page":
        # "{3}" / "{}" (pagebreak only)
        if re.fullmatch(r"\{\s*\d*\s*\}", t):
            return current_page
        raise ValueError(f"Preset de página inválido: {text}")

    ps: DSL.PageSpec = cmd.args.get("page")
    if ps is None or getattr(ps, "pagebreak_only", False):
        return current_page

    out = PageSpec()
    if getattr(ps, "size", None):
        out.name = ps.size
        sz = CONST.get_page_size_preset(ps.size)
        if sz:
            out.width_mm, out.height_mm = sz
    out.landscape = bool(getattr(ps, "landscape", False))

    # We need a base size to interpret % in border.
    # Rule (user):
    #   - If border has 1 token with '%': % refers to the total of both sides,
    #     so it is split /2 per side (vertical uses height; horizontal uses width).
    #   - If border has 2 tokens and has '%': same, first vertical (height), second horizontal (width).
    #   - With 3/4 tokens: % is per-side (no /2).
    #   - If there is NO '%', keep standard CSS shorthand.
    base_w_mm, base_h_mm = out.resolved_size_mm(doc_page_mm)

    b = getattr(ps, "border", None)
    if b:
        # Defensive: in some legacy/bridge paths the border list may arrive with items already
        # coerced to numbers (thus losing the '%' suffix). If the raw text contains a '%' in the
        # border declaration, recover the raw tokens from the text to preserve percentage semantics.
        toks_now = _as_token_list(b)
        if (not any('%' in str(x) for x in toks_now)) and ('%' in t):
            m = re.search(r"(?:^|\s)(?:b|border)\s*=\s*(?P<val>\[[^\]]*\]|[^\s\}]+)", t)
            if m:
                raw_val = (m.group('val') or '').strip()
                if raw_val.startswith('[') and raw_val.endswith(']'):
                    raw_val = raw_val[1:-1].strip()
                if '%' in raw_val:
                    toks_now = [p for p in re.split(r"\s+", raw_val) if p]

        out.border_mm = SVG.border_tokens_to_mm4(toks_now, base_w_mm=base_w_mm, base_h_mm=base_h_mm)

    return out


def apply_layout_spec(state_tuple, ls):
    """
    Aplica un LayoutSpec DSL (devuelto por DSL.parse_layout_block) sobre el estado.
    Importante:
      - If the shape preset changes, clear width_mm/height_mm so resolve() recalculates from presets.
      - Kerf is stored as raw list[str] in layout.gaps.
    """
    page, card, layout, gaps = state_tuple
    if ls is None:
        return page, card, layout, gaps

    page = page or PageSpec()
    card = card or CardSpec()
    layout = layout or LayoutSpec()
    gaps = gaps or GapsMM()

    # Note: page size/orientation and global cursor live in Page{}, not in Layout{}.

    g = getattr(ls, "grid", None)
    if g is not None:
        try:
            c = int(getattr(g, "cols", 0) or 0)
            r = int(getattr(g, "rows", 0) or 0)
            layout.cols = c or layout.cols
            layout.rows = r or layout.rows
        except Exception:
            pass
        # GridSpec.order semantics (from DSL):
        #   - 'lr-tb' : left→right within a row, then top→bottom (row-major)
        #   - 'tb-lr' : top→bottom within a column, then left→right (column-major)
        # The planner uses `sweep_rows_first=True` for row-major.
        layout.sweep_rows_first = (getattr(g, "order", None) or "lr-tb") == "lr-tb"
        _flip = getattr(g, "flip", None)
        layout.invert_cols = (_flip in ("h", "hv"))
        layout.invert_rows = (_flip in ("v", "hv"))

    # gaps: grid.gaps > ls.k/ls.gaps > grid.props["k"]
    k_any = None
    if g is not None and getattr(g, "gaps", None) not in (None, ""):
        k_any = getattr(g, "gaps")
    if k_any is None and getattr(ls, "k", None) not in (None, ""):
        k_any = getattr(ls, "k")
    if k_any is None and getattr(ls, "gaps", None) not in (None, ""):
        k_any = getattr(ls, "gaps")
    if k_any is None and g is not None:
        props = getattr(g, "props", None)
        if isinstance(props, dict) and "k" in props:
            k_any = props.get("k")

    # offset: grid.offset > ls.o/ls.offset > grid.props["o"]
    o_any = None
    if g is not None and getattr(g, "offset", None) not in (None, ""):
        o_any = getattr(g, "offset")
    if o_any is None and getattr(ls, "o", None) not in (None, ""):
        o_any = getattr(ls, "o")
    if o_any is None and getattr(ls, "offset", None) not in (None, ""):
        o_any = getattr(ls, "offset")
    if o_any is None and g is not None:
        props = getattr(g, "props", None)
        if isinstance(props, dict) and "o" in props:
            o_any = props.get("o")

    seq = _as_token_list(k_any)
    off = _as_token_list(o_any)
    if seq:
        layout.gaps = seq
        layout.offset = off
    else:
        layout.gaps = []
        layout.offset = off

    # Any change in raw gaps invalidates smart-shape application state.
    for _k in ("_smart_applied_key", "_smart_user_gaps"):
        if hasattr(layout, _k):
            try:
                delattr(layout, _k)
            except Exception:
                pass

    # shape
    shape = getattr(ls, "shape", None)
    if shape:
        preset = getattr(shape, "preset", None)
        if preset:
            # Smart shapes are *not* card size presets. They only trigger gaps auto-adjustment
            # in deckmaker, while keeping the underlying card size driven by the template.
            sp = str(preset).strip().lower()
            if sp in ("hexgrid", "hextile", "hextiles"):
                layout.smart_shape = sp
                # Do NOT touch card sizing.
            else:
                layout.smart_shape = None
                card.name = str(preset)
                card.width_mm = None
                card.height_mm = None
        elif getattr(shape, "kind", None) == "rect" and getattr(shape, "args", None):
            m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", str(shape.args[0]), re.I)
            if m:
                card.name = None
                card.width_mm = float(m.group(1))
                card.height_mm = float(m.group(2))
                layout.smart_shape = None

    return page, card, layout, gaps


# ----------------------------- Planning ---------------------------------------

def plan_grid(page_w_px, page_h_px, card_w_px, card_h_px, *,
              gaps_px=(0.0, 0.0),
              gaps_px6=None,
              layout=None,
              content_origin_px=(0.0, 0.0),
              content_wh_px=None):
    """
    Compute slots (x,y,w,h) for a grid.

    - gaps_px: compat (gap_h, gap_v)
    - gaps_px6: optional (gap_x, gap_y, w1, h1, w2, h2) for staggered/hex-like grids.
    - content_origin_px / content_wh_px: usable area (page - margins), in px.
    """
    gh = float(gaps_px[0] or 0.0)
    gv = float(gaps_px[1] or 0.0)

    if gaps_px6 is not None:
        gx, gy, w1, h1, w2, h2 = [float(x or 0.0) for x in gaps_px6]
        gh, gv = gx, gy
    else:
        w1 = h1 = w2 = h2 = 0.0

    cx, cy = float(content_origin_px[0]), float(content_origin_px[1])
    if content_wh_px is None:
        cw, ch = float(page_w_px), float(page_h_px)
    else:
        cw, ch = float(content_wh_px[0]), float(content_wh_px[1])

    def _max_fit(L, cell, gap):
        if cell <= 0.0 or L <= 0.0:
            return 0
        return max(0, int((L + gap) // (cell + gap)))

    want_cols = int(getattr(layout, "cols", 0) or 0)
    want_rows = int(getattr(layout, "rows", 0) or 0)
    cols = want_cols if want_cols > 0 else _max_fit(cw, float(card_w_px), gh)
    rows = want_rows if want_rows > 0 else _max_fit(ch, float(card_h_px), gv)

    if cols <= 0 or rows <= 0:
        class _Plan:
            def __init__(self):
                self.slots = []
                self.cols = cols
                self.rows = rows
                self.per_page = 0
                self.page_w_px = page_w_px
                self.page_h_px = page_h_px
        return _Plan()

    sweep_rows_first = bool(getattr(layout, "sweep_rows_first", True))
    invert_cols = bool(getattr(layout, "invert_cols", False))
    invert_rows = bool(getattr(layout, "invert_rows", False))

    def _row_dx_px(rr: int) -> float:
        """Horizontal stagger offset as delta between consecutive rows: w1, w2, w1, w2..."""
        if w1 == 0.0 and w2 == 0.0:
            return 0.0
        k = rr // 2
        base_x = k * (w1 + w2)
        if rr % 2 == 1:
            base_x += w1
        return base_x

    def _col_dy_px(cc: int) -> float:
        """Vertical stagger offset as delta between consecutive columns: h1, h2, h1, h2..."""
        if h1 == 0.0 and h2 == 0.0:
            return 0.0
        k = cc // 2
        base_y = k * (h1 + h2)
        if cc % 2 == 1:
            base_y += h1
        return base_y

    # slots not centered yet, to measure real extents with offsets
    raw = []
    for r in range(rows):
        rr = (rows - 1 - r) if invert_rows else r
        ox = _row_dx_px(rr)
        for c in range(cols):
            cc = (cols - 1 - c) if invert_cols else c
            x = cc * (float(card_w_px) + gh) + ox
            y = rr * (float(card_h_px) + gv) + _col_dy_px(cc)
            raw.append((x, y, float(card_w_px), float(card_h_px)))

    minx = min(s[0] for s in raw)
    miny = min(s[1] for s in raw)
    maxx = max(s[0] + s[2] for s in raw)
    maxy = max(s[1] + s[3] for s in raw)
    span_w = maxx - minx
    span_h = maxy - miny

    _l.d(
        f"[plan.grid] L={cw:.2f}×{ch:.2f} cell={float(card_w_px):.2f}×{float(card_h_px):.2f} "
        f"gap={gh:.2f}×{gv:.2f} row_dx12=({w1:.2f},{w2:.2f}) col_dy12=({h1:.2f},{h2:.2f}) "
        f"want={want_cols}×{want_rows} → cols×rows={cols}×{rows} span={span_w:.2f}×{span_h:.2f}"
    )

    if span_w - cw > 0.5 or span_h - ch > 0.5:
        _l.w(
            f"[plan.grid] OVERFLOW: span=({span_w:.2f}×{span_h:.2f}) > L=({cw:.2f}×{ch:.2f}) "
            f"gap=({gh:.2f}×{gv:.2f}) off12=({w1:.2f},{h1:.2f})+({w2:.2f},{h2:.2f})"
        )

    left = cx + (cw - span_w) * 0.5 - minx
    top = cy + (ch - span_h) * 0.5 - miny

    slots = []
    if sweep_rows_first:
        for r in range(rows):
            rr = (rows - 1 - r) if invert_rows else r
            ox = _row_dx_px(rr)
            for c in range(cols):
                cc = (cols - 1 - c) if invert_cols else c
                slots.append((
                    left + cc * (float(card_w_px) + gh) + ox,
                    top + rr * (float(card_h_px) + gv) + _col_dy_px(cc),
                    float(card_w_px),
                    float(card_h_px),
                ))
    else:
        for c in range(cols):
            cc = (cols - 1 - c) if invert_cols else c
            for r in range(rows):
                rr = (rows - 1 - r) if invert_rows else r
                ox = _row_dx_px(rr)
                slots.append((
                    left + cc * (float(card_w_px) + gh) + ox,
                    top + rr * (float(card_h_px) + gv) + _col_dy_px(cc),
                    float(card_w_px),
                    float(card_h_px),
                ))

    per_page = cols * rows
    _l.d(f"[plan.grid] left,top=({left:.2f},{top:.2f}) per_page={per_page}")

    class _Plan:
        def __init__(self):
            self.slots = slots
            self.cols = cols
            self.rows = rows
            self.per_page = per_page
            self.page_w_px = page_w_px
            self.page_h_px = page_h_px

    return _Plan()
