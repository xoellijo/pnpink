#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
"""marks.py — PnPInk Marks{} (cut/crosshair marks)

__version__ = "v0.2.1-hextiles-extremes-by-c"

Dev note (v0.1):
  - Slot-based geometry: marks are generated per placed slot bbox (post-layout).
  - Style stack: if s points to a <g>, each direct child contributes one style layer
    and the same geometry is rendered once per child.
  - Scope is intentionally NOT implemented in this delivery (always front pass).
"""

import os, sys
from typing import Dict, List, Optional, Tuple
import math

sys.path.append(os.path.dirname(__file__))

import log as LOG
_l = LOG
import inkex
import svg as SVG
import prefs


_STROKE_KEYS = (
    "stroke",
    "stroke-width",
    "stroke-dasharray",
    "opacity",
    "stroke-opacity",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
)


def _parse_style_attr(style: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not style:
        return out
    for part in str(style).split(';'):
        part = part.strip()
        if not part or ':' not in part:
            continue
        k, v = part.split(':', 1)
        k = k.strip()
        v = v.strip()
        if k:
            out[k] = v
    return out


def _style_dict_to_attr(d: Dict[str, str]) -> str:
    # Stable key order for deterministic output
    keys = list(d.keys())
    keys.sort()
    return ';'.join([f"{k}:{d[k]}" for k in keys if d.get(k) is not None and str(d.get(k)).strip() != ""]) + ';'


def _extract_stroke_style(el) -> Dict[str, str]:
    """Extract stroke-related style from an SVG element.

    We read from both presentation attributes and inline 'style'.
    """
    d = _parse_style_attr(el.get('style') or '')
    out: Dict[str, str] = {}
    # presentation attributes override style() in Inkscape UI; keep style-based then override.
    for k in _STROKE_KEYS:
        if k in d:
            out[k] = d[k]
    # presentation attributes
    for k in _STROKE_KEYS:
        v = el.get(k)
        if v is not None and str(v).strip() != "":
            out[k] = str(v)

    # Ensure marks are not filled
    out['fill'] = 'none'
    return out


def _resolve_style_layers(root, style_id: Optional[str]) -> List[Dict[str, str]]:
    """Return a list of style dicts to render.

    Style selection model (v0.1.8):
      - Always look up by id.
      - If the id refers to a <g>, use *all descendant <path>* elements in document
        order as a style stack (one layer per path).
      - If the id refers to a non-<g> element, use its stroke style as a single layer.

    If the id is missing/not found, fall back to prefs defaults.
    """
    if not style_id:
        return [prefs.get_marks_style_dict()]

    sid = str(style_id).strip()
    if not sid:
        return [prefs.get_marks_style_dict()]

    el = root.find(f".//*[@id='{sid}']")
    if el is None:
        _l.w(f"[marks] style id '{sid}' not found; using prefs defaults")
        return [prefs.get_marks_style_dict()]

    # Style stack resolution (user-facing semantics):
    #   - The user provides an id that typically points to a *path*.
    #   - If that element is inside a <g>, we treat that ancestor group as a
    #     "style stack" and use all descendant paths of that <g> in document
    #     order.
    #   - If the id itself points to a <g>, we also treat it as a style stack.
    #   - Otherwise, we use the single element's stroke style.

    def _is_g(node) -> bool:
        try:
            t = node.tag
        except Exception:
            return False
        return isinstance(t, str) and t.endswith('g')

    group = None
    if _is_g(el):
        # The user may still point directly to a <g> (supported).
        group = el
    else:
        # Main intended workflow: user points to a *path* that may live inside a
        # <g> "style stack" group. We only accept the *direct parent* <g> to
        # avoid accidentally capturing an entire layer or other higher-level
        # grouping.
        try:
            parent = el.getparent()
        except Exception:
            parent = None
        if parent is not None and _is_g(parent):
            group = parent

    if group is not None:
        layers: List[Dict[str, str]] = []
        try:
            # Namespace-safe selection (works with lxml/inkex etree)
            paths = group.findall(".//{%s}path" % inkex.NSS['svg'])
            if not paths:
                # fallback if namespaces are stripped
                paths = group.findall('.//path')
            for p in paths:
                try:
                    layers.append(_extract_stroke_style(p))
                except Exception:
                    continue
        except Exception:
            layers = []
        if layers:
            return layers

    return [_extract_stroke_style(el)]


def _norm_trbl_tokens(tokens: Optional[List[str]], default: List[str]) -> List[str]:
    vals = list(tokens or [])
    if not vals:
        vals = list(default)
    # normalize to 4 tokens like CSS: 1->TRBL, 2->TB RL, 3->T RL B, 4->TRBL
    if len(vals) == 1:
        return [vals[0], vals[0], vals[0], vals[0]]
    if len(vals) == 2:
        return [vals[0], vals[1], vals[0], vals[1]]
    if len(vals) == 3:
        return [vals[0], vals[1], vals[2], vals[1]]
    return (vals + ["0", "0", "0", "0"])[:4]


def _tokens_to_mm4(tokens4: List[str]) -> List[float]:
    out = []
    for t in tokens4[:4]:
        out.append(float(SVG.measure_to_mm(t, base_mm=None)))
    return out


def _len_to_mm2(
    length_tokens: Optional[List[Optional[str]]],
    *,
    default_out: str = "3mm",
    gaps_has_offsets: bool = False,
) -> Tuple[float, float]:
    """Resolve length tokens to (out_mm, in_mm).

    Rules (as agreed):
      - l can be a scalar or [out in]
      - l=5  => out=5mm, in=0mm (i.e. [5 0])
      - l=[5 2] => out=5mm, in=2mm
      - Special case: ONLY when gaps has offsets (layout gaps params 3..6 != 0)
        we interpret a *scalar* l=5 as [5 5] (internal marks enabled), because
        the grid is not aligned.
    """
    if not length_tokens:
        return float(SVG.measure_to_mm(default_out, base_mm=None)), 0.0

    toks = [t for t in list(length_tokens) if t is not None and str(t).strip() != ""]
    if not toks:
        return float(SVG.measure_to_mm(default_out, base_mm=None)), 0.0

    if len(toks) == 1:
        out = float(SVG.measure_to_mm(toks[0], base_mm=None))
        inn = out if gaps_has_offsets else 0.0
        return out, inn

    return float(SVG.measure_to_mm(toks[0], base_mm=None)), float(SVG.measure_to_mm(toks[1], base_mm=None))


def _d_tokens_to_ext_int_mm(
    d_tokens: Optional[List[str]],
    *,
    default_ext: str = "2mm",
) -> Tuple[float, float]:
    """Resolve distance tokens to (d_ext_mm, d_int_mm).

    IMPORTANT (v0.1.8): internal distance is no longer a user-controlled knob.
    The preferred behavior is that internal marks cross "in short" between cards,
    so the effective internal distance is derived from internal length:

        d_internal = -len_internal/2

    Therefore this helper only provides a robust default for *external* distance,
    and returns a placeholder for internal which will be overridden at render time.

    Accepted forms:
      - d omitted -> 2mm
      - d=2 -> 2mm
      - d=[ext int] -> ext is used, int is ignored (kept for backward compatibility)
      - d=[t r b l] -> top is used as ext
    """
    if not d_tokens:
        ext = float(SVG.measure_to_mm(default_ext, base_mm=None))
        return ext, 0.0

    toks = [t for t in list(d_tokens) if t is not None and str(t).strip() != ""]
    if not toks:
        ext = float(SVG.measure_to_mm(default_ext, base_mm=None))
        return ext, 0.0

    if len(toks) == 1:
        ext = float(SVG.measure_to_mm(toks[0], base_mm=None))
        return ext, 0.0

    if len(toks) >= 2:
        # prefer the first two as [ext int]
        if len(toks) == 4:
            _l.w("[marks] d=TRBL is deprecated; use d=<mm>. Using top as external distance; internal is derived from len_in.")
            ext = float(SVG.measure_to_mm(toks[0], base_mm=None))
            return ext, 0.0
        ext = float(SVG.measure_to_mm(toks[0], base_mm=None))
        return ext, 0.0

    ext = float(SVG.measure_to_mm(default_ext, base_mm=None))
    return ext, 0.0


def _build_edge_paths(
    x0: float, y0: float, x1: float, y1: float,
    d_trbl_px: List[float],
    len_top_px: float, len_right_px: float, len_bottom_px: float, len_left_px: float,
) -> str:
    """Return a path 'd' with segments for the four sides, per-corner.

    Key model (as clarified in the conversation):
      - Each card has 8 mark segments:
          - 2 vertical at the top corners (VU)
          - 2 vertical at the bottom corners (VD)
          - 2 horizontal at the left corners (HL)
          - 2 horizontal at the right corners (HR)
      - Whether a segment is considered "external" or "internal" depends on the
        card position in the grid (perimeter vs shared edge with another card),
        not on "inside/outside" of the card artwork.

    Geometry:
      - (x0,y0,x1,y1) is the cut bbox.
      - d is a *gap* measured from the cut corner along the outward direction.
        If d=0, segments meet exactly at the cut corner.
        If d>0, they stop short leaving a gap.
      - len_* are the segment lengths for each side (top/right/bottom/left).

    Coordinates are in document px.
    """
    dt, dr, db, dl = d_trbl_px

    segs: List[str] = []
    def seg(xa, ya, xb, yb):
        segs.append(f"M {xa:.3f},{ya:.3f} L {xb:.3f},{yb:.3f}")

    # TOP (vertical, upwards) at both top corners
    if len_top_px > 0:
        seg(x0, y0 - dt - len_top_px, x0, y0 - dt)
        seg(x1, y0 - dt - len_top_px, x1, y0 - dt)

    # BOTTOM (vertical, downwards) at both bottom corners
    if len_bottom_px > 0:
        seg(x0, y1 + db, x0, y1 + db + len_bottom_px)
        seg(x1, y1 + db, x1, y1 + db + len_bottom_px)

    # LEFT (horizontal, leftwards) at both left corners
    if len_left_px > 0:
        seg(x0 - dl - len_left_px, y0, x0 - dl, y0)
        seg(x0 - dl - len_left_px, y1, x0 - dl, y1)

    # RIGHT (horizontal, rightwards) at both right corners
    if len_right_px > 0:
        seg(x1 + dr, y0, x1 + dr + len_right_px, y0)
        seg(x1 + dr, y1, x1 + dr + len_right_px, y1)

    return " ".join(segs)


def render_slot_marks(
    root,
    *,
    slot_bbox_px: Tuple[float, float, float, float],
    px_per_mm: float,
    style_id: Optional[str] = None,
    layer_label: Optional[str] = None,
    b_tokens: Optional[List[str]] = None,
    d_tokens: Optional[List[str]] = None,
    length_tokens: Optional[List[str]] = None,
    gaps_has_offsets: bool = False,
    edge_top: bool = False,
    edge_right: bool = False,
    edge_bottom: bool = False,
    edge_left: bool = False,
) -> int:
    """Render marks for a single slot bbox.

    Returns number of generated path elements.
    """
    x, y, w, h = slot_bbox_px
    x0, y0, x1, y1 = float(x), float(y), float(x) + float(w), float(y) + float(h)

    # Defaults (per requirements)
    layer_label = (layer_label or "marks").strip() or "marks"

    # b: inset/outset offset of the *reference bbox* (same TRBL grammar as border).
    # Default is 0 (flush to the slot bbox). Negative values move marks inward.
    b4 = _norm_trbl_tokens(b_tokens, default=["0"])
    b_mm = _tokens_to_mm4(b4)
    b_px = [v * px_per_mm for v in b_mm]
    bt, br, bb, bl = b_px
    x0 = x0 - bl
    x1 = x1 + br
    y0 = y0 - bt
    y1 = y1 + bb

    # d: distance to card for *external* marks.
    # Internal distance is derived from internal length for a tight cross.
    #
    # Rule (as requested): d_internal = -l_internal/2
    d_ext_mm, _d_int_mm_unused = _d_tokens_to_ext_int_mm(d_tokens, default_ext="2mm")
    d_ext_px = d_ext_mm * px_per_mm

    # len default 3mm (out) and 0 (in)
    len_out_mm, len_in_mm = _len_to_mm2(
        length_tokens,
        default_out="3mm",
        gaps_has_offsets=bool(gaps_has_offsets),
    )
    len_out_px = len_out_mm * px_per_mm
    len_in_px = len_in_mm * px_per_mm

    # Derived internal distance for "short" crossing between adjacent cards.
    d_int_px = -0.5 * len_in_px

    # Select per-side lengths and per-side distances based on occupancy
    # (perimeter vs shared edge with another card).
    # External sides -> (len_out, d_ext)
    # Internal sides -> (len_in, d_int)
    len_top_px = len_out_px if edge_top else len_in_px
    len_right_px = len_out_px if edge_right else len_in_px
    len_bottom_px = len_out_px if edge_bottom else len_in_px
    len_left_px = len_out_px if edge_left else len_in_px

    d_top_px = d_ext_px if edge_top else d_int_px
    d_right_px = d_ext_px if edge_right else d_int_px
    d_bottom_px = d_ext_px if edge_bottom else d_int_px
    d_left_px = d_ext_px if edge_left else d_int_px

    # Pattern selector (reserved): b previously served as the pattern token in the DSL design.
    # In practice users already expect b to work like a border/inset; we keep pattern support
    # for future versions behind a different key.

    layer = SVG.find_or_create_layer(root, layer_label)

    d_attr = _build_edge_paths(
        x0, y0, x1, y1,
        [d_top_px, d_right_px, d_bottom_px, d_left_px],
        len_top_px, len_right_px, len_bottom_px, len_left_px,
    )
    if not d_attr:
        return 0

    styles = _resolve_style_layers(root, style_id)

    created = 0
    for st in styles:
        p = SVG.etree.Element(inkex.addNS('path', 'svg'))
        p.set('d', d_attr)
        p.set('style', _style_dict_to_attr(st))
        layer.append(p)
        created += 1

    return created


# -------------------------- Hextiles (MVP) -----------------------------------

def _parse_scalar_mm(tokens: Optional[List[str]], default_mm: float = 0.0) -> float:
    """Parse a single scalar (first non-empty token) to mm."""
    try:
        if not tokens:
            return float(default_mm)
        for t in list(tokens):
            if t is None:
                continue
            s = str(t).strip()
            if not s:
                continue
            return float(SVG.measure_to_mm(s, base_mm=None))
    except Exception:
        pass
    return float(default_mm)

def render_hextiles_page_marks(
    root,
    *,
    jobs: List[dict],
    px_per_mm: float,
    style_id: Optional[str] = None,
    layer_label: Optional[str] = None,
    b_tokens: Optional[List[str]] = None,
    length_tokens: Optional[List[str]] = None,
    d_tokens: Optional[List[str]] = None,
) -> int:
    """Render exterior cut lines for hextile/hextiles pages.

    Contract (deterministic, geometry-first; no (r,c) neighborhood inference):
      - Lines are tangent to real hex edges (never radial).
      - pointy-top: only {90°, +30°, -30°}; horizontals (0°) are forbidden.
      - Only exterior marks: we build *continuous* perimeter lines per family,
        then keep only their two exterior ends.
      - b is a normal offset (b<0 cuts inside).
      - d is a tangent inset from the perimeter extremes (d>=0), applied before
        taking the l-length exterior segment.
      - gaps gap: if a positive gap exists between tiles, draw two parallel
        lines (both sides of the gap), keeping the same exterior-end cropping.
      - l is the true geometric length of each exterior mark segment.

    Implementation strategy (geometry-first; avoids any explicit neighbor/occupancy inference):
      - Sample all candidate supporting lines (c = dot(p,n)) for the 6 normal directions
        and accumulate their tangent spans [s_min, s_max] (s = dot(p,t)).
      - For each supporting line (parallel) accumulate its global tangent span [s_min, s_max]
        across all tiles that contribute to that line.
      - Emit ONLY two segments per supporting line:
          left :  [s_min - d_eff - l , s_min - d_eff]
          right:  [s_max + d_eff     , s_max + d_eff + l]
        where d_eff = d + 0 (d is tangent inset; b is already in the line's c).
    """
    if not jobs:
        return 0

    layer_label = (layer_label or "marks").strip() or "marks"
    layer = SVG.find_or_create_layer(root, layer_label)

    # Parameters
    b_mm = _parse_scalar_mm(b_tokens, default_mm=0.0)
    b_px = float(b_mm) * float(px_per_mm)

    # d: tangent inset from the global extreme (NOT normal). Scalar only.
    d_mm = _parse_scalar_mm(d_tokens, default_mm=0.0)
    d_px = float(d_mm) * float(px_per_mm)

    # l: accept scalar or [out in]; for hextiles we use OUT as the actual segment length.
    len_out_mm, _len_in_mm = _len_to_mm2(length_tokens, default_out="3mm", gaps_has_offsets=True)
    l_px = float(len_out_mm) * float(px_per_mm)
    if l_px <= 0:
        return 0

    # Collect centers and sizes
    pts: List[Tuple[float, float]] = []
    whs: List[Tuple[float, float]] = []
    for j in jobs:
        x, y, w, h = j["bbox"]
        pts.append((float(x) + 0.5 * float(w), float(y) + 0.5 * float(h)))
        whs.append((float(w), float(h)))

    n = len(pts)
    if n < 2:
        return 0


    # Determine orientation ONLY from DeckMaker/Layouts (avoid duplicate inference here).
    cnt_pointy = 0
    cnt_flat = 0
    for j in jobs:
        o = (j.get("smart_hex_orient") or "").strip().lower()
        if o == "pointy":
            cnt_pointy += 1
        elif o == "flat":
            cnt_flat += 1

    if cnt_pointy or cnt_flat:
        orient = "pointy" if cnt_pointy >= cnt_flat else "flat"
    else:
        orient = "pointy"
        _l.w("[marks][hextiles] smart_hex_orient missing in all jobs; defaulting orient=pointy")


    # Expected neighbor directions (outward normals) by orientation.
    # pointy: neighbors at phase 0°  (0,60,...)
    # flat:   neighbors at phase 30° (30,90,...)
    phase = 0.0 if orient == "pointy" else 30.0
    normal_angles_deg = [phase + 60.0 * k for k in range(6)]
    _l.i(
        f"[marks][hextiles] jobs={len(jobs)} orient={orient} phase_deg={phase:.1f} "
        f"b_px={b_px:.4f} d_px={d_px:.4f} l_px={l_px:.4f} normals_deg={[round(x,1) for x in normal_angles_deg]}"
    )

    # Apothem from bbox (regular hex):
    #   - pointy-top: distance center→vertical side = w/2
    #   - flat-top:   distance center→horizontal side = h/2
    ws = sorted([w for (w, h) in whs if w > 1e-9])
    hs = sorted([h for (w, h) in whs if h > 1e-9])
    wmed = ws[len(ws) // 2] if ws else 0.0
    hmed = hs[len(hs) // 2] if hs else 0.0
    ap = 0.5 * (wmed if orient == "pointy" else hmed)
    if ap <= 1e-9:
        ap = 0.5 * float(max(wmed, hmed, 1.0))

    # Establish lattice distance (nearest neighbor) for neighbor existence checks.
    dmin = None
    for i in range(n):
        xi, yi = pts[i]
        best = None
        for j in range(n):
            if j == i:
                continue
            xj, yj = pts[j]
            dx = xj - xi
            dy = yj - yi
            d = (dx * dx + dy * dy) ** 0.5
            if d <= 1e-9:
                continue
            if best is None or d < best:
                best = d
        if best is not None:
            dmin = best if dmin is None else min(dmin, best)

    if dmin is None or dmin <= 1e-9:
        return 0

    tol = 0.22 * float(dmin)
    # NOTE: We intentionally do NOT perform neighbor inference to decide exterior sides.
    # We keep *all* supporting lines (all parallels). Exterior-ness is achieved by cropping
    # to the two tangent extremes of each continuous line (emit only the two end segments).

    # Estimate gap along normals: gap = proj(center_to_neighbor, n) - 2*apothem (clamped >=0).
    # Robust median over observed neighbors close to dmin.
    gap = 0.0
    try:
        proj_samples: List[float] = []
        d_nei = 1.10 * float(dmin)
        for i in range(n):
            xi, yi = pts[i]
            for a_deg in normal_angles_deg:
                a = math.radians(a_deg)
                nx = math.cos(a)
                ny = math.sin(a)
                best_proj = None
                for j in range(n):
                    if j == i:
                        continue
                    xj, yj = pts[j]
                    dx = xj - xi
                    dy = yj - yi
                    d = (dx * dx + dy * dy) ** 0.5
                    if d <= 1e-9 or d > d_nei:
                        continue
                    if abs((dx / d) - nx) > 0.25 or abs((dy / d) - ny) > 0.25:
                        continue
                    proj = dx * nx + dy * ny
                    if best_proj is None or abs(proj - float(dmin)) < abs(best_proj - float(dmin)):
                        best_proj = proj
                if best_proj is not None:
                    proj_samples.append(float(best_proj))
        if proj_samples:
            proj_samples.sort()
            proj_med = proj_samples[len(proj_samples) // 2]
            gap = max(0.0, float(proj_med) - 2.0 * float(ap))
    except Exception:
        gap = 0.0

    # Offsets to draw: one or two parallels depending on gap.
    # Note: ap is the tangent position (edge), b is extra normal offset.
    offsets = [float(ap) + float(b_px)]
    if gap > 1e-6:
        offsets.append(float(ap) + float(gap) + float(b_px))

    _l.i(f"[marks][hextiles] dmin={dmin:.4f} tol={tol:.4f} ap={ap:.4f} gap={gap:.6f} offsets={[round(o,4) for o in offsets]}")
    def _canon_line(nx, ny, tx, ty, c, mn, mx):
        """Canonicalize so opposite normals merge into same line key.

        Flips (n,t,c,s) when (nx,ny) points to the negative half-plane.
        """
        eps = 1e-9
        if (nx < -eps) or (abs(nx) <= eps and ny < -eps):
            nx, ny = (-nx), (-ny)
            tx, ty = (-tx), (-ty)
            c = -c
            mn, mx = (-mx), (-mn)
        return nx, ny, tx, ty, c, mn, mx

    # Accumulate continuous lines by supporting line coordinate (dot(p, n)) and family.
    # Key: (family_idx_mod3, offset_idx, rounded_c)
    acc: Dict[Tuple[int, int, int], Tuple[float, float]] = {}  # s_min, s_max
    rep: Dict[Tuple[int, int, int], Tuple[float, float, float, float, float]] = {}  # nx,ny, tx,ty, c

    # Quantization for c to merge boundary samples into the same line.
    c_q = max(1.0, 0.15 * float(dmin))

    def _q(v: float) -> int:
        return int(round(v / c_q))

    # Side half-span along tangent (half of the hex edge length).
    # pointy: edge length = h/2 ; flat: edge length = w/2
    edge_len = (0.5 * hmed) if orient == 'pointy' else (0.5 * wmed)
    local_half = 0.5 * float(edge_len)
    if local_half <= 1e-9:
        local_half = 0.25 * float(dmin)

    for i in range(n):
        cx, cy = pts[i]
        for dir_idx, a_deg in enumerate(normal_angles_deg):
            a = math.radians(a_deg)
            nx = math.cos(a)
            ny = math.sin(a)

            # No neighbor filtering here (see extremes-by-c filter below).

            # Tangent direction = normal rotated +90°
            tx = -ny
            ty = nx

            for oi, off in enumerate(offsets):
                px = cx + off * nx
                py = cy + off * ny

                # Supporting line coordinate
                c = px * nx + py * ny

                # Local span along tangent contributed by this tile edge.
                # In the basis p = c*n + s*t, each tile provides s in [s0-local_half, s0+local_half].
                s0 = px * tx + py * ty
                mn = s0 - local_half
                mx = s0 + local_half
                # Canonicalize representation so opposite normals (dir and dir+180) merge.
                nx_c, ny_c, tx_c, ty_c, c_c, mn_c, mx_c = _canon_line(nx, ny, tx, ty, c, mn, mx)

                # Family index among 3 orientations after canonicalization (0/60/120 degrees).
                ang_n = (math.degrees(math.atan2(ny_c, nx_c)) % 180.0)
                fam = int(round(ang_n / 60.0)) % 3
                key = (fam, oi, _q(c_c))

                if key in acc:
                    old_mn, old_mx = acc[key]
                    acc[key] = (min(old_mn, mn_c), max(old_mx, mx_c))
                else:
                    acc[key] = (mn_c, mx_c)
                    rep[key] = (nx_c, ny_c, tx_c, ty_c, c_c)

    styles = _resolve_style_layers(root, style_id)
    if not styles:
        styles = [{}]

    def _emit_segment(*, key, end: str, nx, ny, tx, ty, c, s_a: float, s_b: float) -> int:
        # Reconstruct endpoints: p = c*n + s*t
        x1 = c * nx + s_a * tx
        y1 = c * ny + s_a * ty
        x2 = c * nx + s_b * tx
        y2 = c * ny + s_b * ty

        dx = x2 - x1
        dy = y2 - y1
        ang = math.degrees(math.atan2(dy, dx)) if (abs(dx) > 1e-12 or abs(dy) > 1e-12) else 0.0
        qang = round(ang / 30.0) * 30.0

        _l.d(
            f"[marks][hextiles] emitting end={end} key={key} angle={ang:.2f} q={qang:.1f} "
            f"p1=({x1:.3f},{y1:.3f}) p2=({x2:.3f},{y2:.3f})"
        )
        # Note: in pointy-top we should not get horizontals; if it happens we still trace it.
        if orient == 'pointy' and abs(dy) <= 1e-6 and abs(dx) > 1e-6:
            _l.e(
                f"[marks][hextiles] WARNING horizontal segment in pointy (no debería ocurrir). end={end} key={key} "
                f"dx={dx:.6f} dy={dy:.6f} angle={ang:.3f} phase={phase}"
            )

        d_attr = f"M {x1:.3f},{y1:.3f} L {x2:.3f},{y2:.3f}"
        emitted = 0
        for st in styles:
            p = SVG.etree.Element(inkex.addNS('path', 'svg'))
            p.set('d', d_attr)
            if st:
                p.set('style', _style_dict_to_attr(st))
            layer.append(p)
            emitted += 1
        return emitted

    # Emit only exterior ends per family line.
    count = 0
    for key, (s_min, s_max) in acc.items():
        nx, ny, tx, ty, c = rep[key]

        # Tangent and normal angles (for traceability and guard-rails)
        ang_t = math.degrees(math.atan2(ty, tx))
        ang_n = math.degrees(math.atan2(ny, nx))
        ang_t_q = round(ang_t / 30.0) * 30.0
        ang_n_q = round(ang_n / 30.0) * 30.0

        # Trace family summary (key is stable for debugging)
        _l.i(
            f"[marks][hextiles] family key={key} n=({nx:.4f},{ny:.4f}) t=({tx:.4f},{ty:.4f}) "
            f"ang_n_q={ang_n_q:.1f} ang_t_q={ang_t_q:.1f} c={c:.3f} "
            f"s_min={s_min:.3f} s_max={s_max:.3f} d_px={d_px:.3f} l_px={l_px:.3f} gap={gap:.6f}"
        )

        # left end: [s_min - d - l, s_min - d]
        count += _emit_segment(
            key=key,
            end='left',
            nx=nx,
            ny=ny,
            tx=tx,
            ty=ty,
            c=c,
            s_a=float(s_min) - float(d_px) - float(l_px),
            s_b=float(s_min) - float(d_px),
        )

        # right end: [s_max + d, s_max + d + l]
        count += _emit_segment(
            key=key,
            end='right',
            nx=nx,
            ny=ny,
            tx=tx,
            ty=ty,
            c=c,
            s_a=float(s_max) + float(d_px),
            s_b=float(s_max) + float(d_px) + float(l_px),
        )

    _l.i(f"[marks][hextiles] families={len(acc)} emitted_paths={count}")
    return count
