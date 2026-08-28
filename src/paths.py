# -*- coding: utf-8 -*-
"""paths.py - shared helpers for path-style templates and grid path generation."""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

import inkex

import log as LOG
_l = LOG
import layouts as LYT
import svg as SVG
import prefs
import style_templates as STPL

_PT = Tuple[float, float]
_SIDE_ORDER = ('a', 'b', 'c', 'd', 'e', 'f')
_SIDE_POINTS = {
    'a': ('8', '9'),
    'b': ('9', '3'),
    'c': ('3', '2'),
    'd': ('2', '1'),
    'e': ('1', '7'),
    'f': ('7', '8'),
}


def _is_g(node) -> bool:
    try:
        t = node.tag
    except Exception:
        return False
    return isinstance(t, str) and t.endswith('g')


def _is_path(node) -> bool:
    try:
        t = node.tag
    except Exception:
        return False
    return isinstance(t, str) and t.endswith('path')


def _pt_mid(a: _PT, b: _PT) -> _PT:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _angle_diff_deg(a: float, b: float) -> float:
    d = float(a) - float(b)
    while d <= -180.0:
        d += 360.0
    while d > 180.0:
        d -= 360.0
    return abs(d)


def _detect_hex_orientation(target_el, orient_hint: Optional[str] = None) -> str:
    orient = str(orient_hint or '').strip().lower()
    if orient in ('pointy', 'flat'):
        return orient
    try:
        if _is_path(target_el):
            pts = SVG.path_characteristic_points(target_el.get('d') or '', SVG.composed_transform(target_el))
            if pts and len(pts) == 6:
                ang = SVG.base_angle_deg(pts)
                if ang is not None:
                    if abs(ang) <= 5.0:
                        return 'flat'
                    if abs(abs(ang) - 30.0) <= 5.0:
                        return 'pointy'
    except Exception:
        pass
    return 'pointy'


def _label_hex_points_by_angle(raw_pts: List[_PT], orient: str, cx: float, cy: float) -> Optional[Dict[str, _PT]]:
    if not raw_pts or len(raw_pts) != 6:
        return None
    if orient == 'flat':
        targets = {'8': -120.0, '9': -60.0, '3': 0.0, '2': 60.0, '1': 120.0, '7': 180.0}
    else:
        targets = {'8': -90.0, '9': -30.0, '3': 30.0, '2': 90.0, '1': 150.0, '7': -150.0}
    remaining = list(raw_pts)
    out: Dict[str, _PT] = {}
    for lab, want in targets.items():
        best = None
        best_idx = None
        best_d = None
        for i, p in enumerate(remaining):
            ang = math.degrees(math.atan2(float(p[1]) - cy, float(p[0]) - cx))
            dd = _angle_diff_deg(ang, want)
            if best is None or dd < best_d:
                best = p
                best_idx = i
                best_d = dd
        if best is None:
            return None
        out[lab] = best
        remaining.pop(best_idx)
    return out


def hex_geometry_for_target(target_el, orient_hint: Optional[str] = None) -> Optional[dict]:
    orient = _detect_hex_orientation(target_el, orient_hint)
    pts = None
    geom_source = None
    x0 = y0 = x1 = y1 = cx = cy = None
    try:
        if _is_path(target_el):
            raw_pts = SVG.path_characteristic_points(target_el.get('d') or '', SVG.composed_transform(target_el))
            if raw_pts and len(raw_pts) == 6:
                xs = [float(p[0]) for p in raw_pts]
                ys = [float(p[1]) for p in raw_pts]
                x0 = min(xs)
                y0 = min(ys)
                x1 = max(xs)
                y1 = max(ys)
                cx = (x0 + x1) * 0.5
                cy = (y0 + y1) * 0.5
                pts = _label_hex_points_by_angle(raw_pts, orient, cx, cy)
                if pts:
                    geom_source = 'path'
    except Exception:
        pts = None
    if not pts:
        bb = SVG.visual_bbox(target_el)
        if not bb:
            return None
        x, y, w, h = bb
        x0 = float(x)
        y0 = float(y)
        x1 = x0 + float(w)
        y1 = y0 + float(h)
        cx = x0 + float(w) * 0.5
        cy = y0 + float(h) * 0.5
        if orient == 'flat':
            pts = {
                '8': (cx - float(w) * 0.25, y0),
                '9': (cx + float(w) * 0.25, y0),
                '3': (x1, cy),
                '2': (cx + float(w) * 0.25, y1),
                '1': (cx - float(w) * 0.25, y1),
                '7': (x0, cy),
            }
        else:
            pts = {
                '8': (cx, y0),
                '9': (x1, cy - float(h) * 0.25),
                '3': (x1, cy + float(h) * 0.25),
                '2': (cx, y1),
                '1': (x0, cy + float(h) * 0.25),
                '7': (x0, cy - float(h) * 0.25),
            }
        geom_source = 'bbox'
    pts['5'] = (cx, cy)
    sides = {}
    for s, (pa, pb) in _SIDE_POINTS.items():
        a = pts[pa]
        b = pts[pb]
        m = _pt_mid(a, b)
        sides[s] = {'a': a, 'b': b, 'mid': m, 'inward': _norm(cx - m[0], cy - m[1])}
    return {
        'orient': orient,
        'points': pts,
        'sides': sides,
        'center': (cx, cy),
        'bbox': (x0, y0, float(x1 - x0), float(y1 - y0)),
        'source': geom_source,
        'el': target_el,
    }


def resolve_style_templates(root, style_id: Optional[str]):
    """Resolve a style template id into one or more path template elements.

    If style_id points to a group, all descendant paths are used in document order.
    If it points to a path, use that single path.
    """
    sid = str(style_id or '').strip()
    out = STPL.resolve_path_templates(root, sid)
    if not out and sid:
        _l.w(f"[paths] style id '{sid}' not found")
    return out


def _document_order_index(root, node) -> int:
    if root is None or node is None:
        return 10**9
    try:
        for i, el in enumerate(root.iter()):
            if el is node:
                return i
    except Exception:
        pass
    return 10**9


def instantiate_styled_path(template_el, d_attr: str):
    """Create a new <path> with style cloned from template_el and geometry d_attr."""
    return STPL.instantiate_path(template_el, d_attr)


def build_path_items_for_target(
    target_el,
    paths_spec_raw: str,
    *,
    orient_hint: Optional[str] = None,
    style_scope_node=None,
    grid_ctx=None,
):
    """Build (document_order, local_order, path) tuples for a target."""
    geom = hex_geometry_for_target(target_el, orient_hint)
    if not geom:
        _l.w(f"[paths] target id='{target_el.get('id') if target_el is not None else ''}' has no usable bbox")
        return []
    blocks = parse_paths_block(paths_spec_raw)
    built = []
    seq = 0
    style_scope = style_scope_node
    for blk in blocks:
        layers = resolve_style_templates(style_scope, blk.get('style_id'))
        if not layers:
            _l.w(f"[paths] style '{blk.get('style_id')}' produced no path templates")
            continue
        for tok in (blk.get('tokens') or []):
            d_attr = token_to_path_d(tok, geom, target_el=target_el, grid_ctx=grid_ctx)
            if not d_attr:
                try:
                    _l.d(f"[paths] empty d token='{tok}' target='{target_el.get('id') if target_el is not None else ''}'")
                except Exception:
                    pass
                continue
            for lay in layers:
                built.append((_document_order_index(style_scope, lay), seq, instantiate_styled_path(lay, d_attr)))
                seq += 1
    return built


def build_paths_for_target(
    target_el,
    paths_spec_raw: str,
    *,
    orient_hint: Optional[str] = None,
    style_scope_node=None,
    grid_ctx=None,
):
    """Build styled path elements for a target, sorted by template document order."""
    built = build_path_items_for_target(
        target_el,
        paths_spec_raw,
        orient_hint=orient_hint,
        style_scope_node=style_scope_node,
        grid_ctx=grid_ctx,
    )
    built.sort(key=lambda item: (item[0], item[1]))
    return [p for _, _, p in built]


def _default_marks_style_template():
    p = SVG.etree.Element(inkex.addNS('path', 'svg'))
    st = prefs.get_marks_style_dict()
    items = [f"{k}:{v}" for k, v in sorted(st.items()) if v is not None and str(v).strip() != '']
    p.set('style', ';'.join(items) + (';' if items else ''))
    p.set('fill', 'none')
    return p


def resolve_style_templates_for_marks(root, style_id: Optional[str]):
    out = resolve_style_templates(root, style_id)
    if out:
        return out
    return [_default_marks_style_template()]


def parse_paths_block(text: str) -> List[dict]:
    """Parse .P{ style1 [a b] t=style2 [5a ef] } into blocks."""
    s = str(text or '').strip()
    if not s:
        return []
    if s.startswith('.P'):
        s = s[2:].strip()
    elif s.startswith('P'):
        s = s[1:].strip()
    if not (s.startswith('{') and s.endswith('}')):
        raise ValueError('invalid Paths block')
    body = s[1:-1].strip()
    if not body:
        return []
    toks = []
    cur = []
    depth = 0
    for ch in body:
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth = max(0, depth - 1)
        if ch.isspace() and depth == 0:
            tok = ''.join(cur).strip()
            if tok:
                toks.append(tok)
            cur = []
            continue
        cur.append(ch)
    tok = ''.join(cur).strip()
    if tok:
        toks.append(tok)
    out = []
    i = 0
    while i < len(toks):
        style_tok = toks[i]
        style_id = style_tok[2:].strip() if style_tok.startswith('t=') else style_tok.strip()
        if not style_id:
            raise ValueError('missing path style id')
        if i + 1 >= len(toks):
            raise ValueError('missing [tokens] after path style id')
        grp = toks[i + 1].strip()
        if not (grp.startswith('[') and grp.endswith(']')):
            raise ValueError('expected [tokens] after path style id')
        inner = grp[1:-1].strip()
        segs = [t for t in re.split(r'[\s,]+', inner) if t]
        out.append({'style_id': style_id, 'tokens': segs})
        i += 2
    return out


def _dist(a: _PT, b: _PT) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _norm(vx: float, vy: float) -> _PT:
    ln = math.hypot(vx, vy)
    if ln <= 1e-9:
        return (0.0, 0.0)
    return (vx / ln, vy / ln)


def _path_line(a: _PT, b: _PT) -> str:
    return f"M {a[0]:.6f},{a[1]:.6f} L {b[0]:.6f},{b[1]:.6f}"


def _path_poly(points: List[_PT]) -> str:
    if not points:
        return ''
    head = points[0]
    tail = ' '.join([f"L {p[0]:.6f},{p[1]:.6f}" for p in points[1:]])
    return f"M {head[0]:.6f},{head[1]:.6f} {tail}".strip()


def _path_quad(a: _PT, c: _PT, b: _PT) -> str:
    return f"M {a[0]:.6f},{a[1]:.6f} Q {c[0]:.6f},{c[1]:.6f} {b[0]:.6f},{b[1]:.6f}"


def _line_intersection(p1: _PT, d1: _PT, p2: _PT, d2: _PT) -> Optional[_PT]:
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    dx1, dy1 = float(d1[0]), float(d1[1])
    dx2, dy2 = float(d2[0]), float(d2[1])
    det = dx1 * dy2 - dy1 * dx2
    if abs(det) <= 1e-9:
        return None
    qx = x2 - x1
    qy = y2 - y1
    t = (qx * dy2 - qy * dx2) / det
    return (x1 + t * dx1, y1 + t * dy1)


def _path_cubic_tangent(a: _PT, c1: _PT, c2: _PT, b: _PT) -> str:
    return (
        f"M {a[0]:.6f},{a[1]:.6f} "
        f"C {c1[0]:.6f},{c1[1]:.6f} {c2[0]:.6f},{c2[1]:.6f} {b[0]:.6f},{b[1]:.6f}"
    )


def _path_side_arc(sides: dict, s1: str, s2: str, fallback_center: _PT) -> str:
    p1 = sides[s1]['mid']
    p2 = sides[s2]['mid']
    n1 = sides[s1]['inward']
    n2 = sides[s2]['inward']
    center = _line_intersection(p1, n1, p2, n2)
    # We keep the implementation simple and robust: cubic bezier with handles
    # constrained to the inward tangents of each side. This keeps curves inside
    # the hex and lets us tune short vs long connections separately.
    if center is None:
        center = fallback_center
    d1 = _dist(p1, center)
    d2 = _dist(p2, center)
    if d1 <= 1e-9 or d2 <= 1e-9:
        return _path_quad(p1, fallback_center, p2)

    i1 = _SIDE_ORDER.index(s1)
    i2 = _SIDE_ORDER.index(s2)
    step = min((i2 - i1) % 6, (i1 - i2) % 6)
    # step=1: short/tight curve (adjacent sides)
    # step=2: long/gentle curve (one side in between)
    if step <= 1:
        k = 0.42
    else:
        k = 0.82
    c1 = (float(p1[0]) + float(n1[0]) * d1 * k, float(p1[1]) + float(n1[1]) * d1 * k)
    c2 = (float(p2[0]) + float(n2[0]) * d2 * k, float(p2[1]) + float(n2[1]) * d2 * k)
    return _path_cubic_tangent(p1, c1, c2, p2)


def _split_poly_refs(token: str) -> List[str]:
    s = str(token or '').strip()
    out: List[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isspace() or ch == ',':
            i += 1
            continue
        if ch in 'a-f':
            out.append(ch)
            i += 1
            continue
        if ch in '1235789':
            out.append(ch)
            i += 1
            continue
        if ch in 'ABCDEF':
            j = i
            while j < len(s) and s[j] in 'ABCDEF':
                j += 1
            if j >= len(s) or s[j] not in '1235789abcdef':
                return []
            out.append(s[i:j + 1])
            i = j + 1
            continue
        return []
    return out


def _translate_geom(geom: dict, dx: float, dy: float) -> dict:
    pts = {k: (float(p[0]) + dx, float(p[1]) + dy) for k, p in (geom.get('points') or {}).items()}
    sides = {}
    for k, s in (geom.get('sides') or {}).items():
        sides[k] = {
            'a': (float(s['a'][0]) + dx, float(s['a'][1]) + dy),
            'b': (float(s['b'][0]) + dx, float(s['b'][1]) + dy),
            'mid': (float(s['mid'][0]) + dx, float(s['mid'][1]) + dy),
            'inward': s.get('inward'),
        }
    bx, by, bw, bh = geom.get('bbox') or (0.0, 0.0, 0.0, 0.0)
    cx, cy = geom.get('center') or pts.get('5') or (0.0, 0.0)
    out = dict(geom)
    out['points'] = pts
    out['sides'] = sides
    out['center'] = (float(cx) + dx, float(cy) + dy)
    out['bbox'] = (float(bx) + dx, float(by) + dy, float(bw), float(bh))
    return out


def _virtual_neighbor_geom(geom: dict, side: str) -> Optional[dict]:
    try:
        c = geom.get('center')
        mid = (geom.get('sides') or {}).get(side, {}).get('mid')
        if c is None or mid is None:
            return None
        return _translate_geom(geom, 2.0 * (float(mid[0]) - float(c[0])), 2.0 * (float(mid[1]) - float(c[1])))
    except Exception:
        return None


def _grid_translate_for_chain(geom: dict, chain: str, grid_ctx) -> tuple[float, float] | None:
    if not grid_ctx or geom.get('orient') != 'pointy':
        return None
    try:
        gx, gy, w1, h1, w2, h2 = [float(x or 0.0) for x in (grid_ctx.get('gaps_px6') or [])]
        cell_w = float(grid_ctx.get('cell_w') or (geom.get('bbox') or (0, 0, 0, 0))[2])
        cell_h = float(grid_ctx.get('cell_h') or (geom.get('bbox') or (0, 0, 0, 0))[3])
        row = int(grid_ctx.get('row0') or 0)
        col = int(grid_ctx.get('col0') or 0)
        row0 = row
        col0 = col
        for side in str(chain or '').lower():
            if side == 'b':
                col += 1
            elif side == 'e':
                col -= 1
            elif side in 'ac':
                col += 1 if row % 2 else 0
                row += -1 if side == 'a' else 1
            elif side in 'df':
                col -= 0 if row % 2 else 1
                row += -1 if side == 'f' else 1
        x0 = col0 * (cell_w + gx) + LYT.grid_row_dx(row0, w1, w2)
        y0 = row0 * (cell_h + gy) + LYT.grid_col_dy(col0, h1, h2)
        x1 = col * (cell_w + gx) + LYT.grid_row_dx(row, w1, w2)
        y1 = row * (cell_h + gy) + LYT.grid_col_dy(col, h1, h2)
        return x1 - x0, y1 - y0
    except Exception:
        return None


def _geom_for_chain(geom: dict, chain: str, grid_ctx=None):
    vec = _grid_translate_for_chain(geom, chain, grid_ctx)
    if vec is not None:
        return _translate_geom(geom, vec[0], vec[1])
    cur_geom = geom
    for side in str(chain or '').lower():
        cur_geom = _virtual_neighbor_geom(cur_geom, side)
        if cur_geom is None:
            return None
    return cur_geom


def _resolve_ref_point(ref: str, target_el, geom: dict, grid_ctx=None):
    t = str(ref or '').strip()
    if not t:
        return None, target_el, geom
    pts = geom['points']
    sides = geom['sides']
    if t == '5':
        return pts['5'], target_el, geom
    if re.fullmatch(r'[1235789]', t):
        return pts.get(t), target_el, geom
    if re.fullmatch(r'[a-f]', t):
        return sides[t]['mid'], target_el, geom
    m = re.fullmatch(r'([A-F]+)([1235789a-f])', t)
    if not m:
        return None, target_el, geom
    chain = m.group(1).lower()
    tail = m.group(2)
    cur_geom = _geom_for_chain(geom, chain, grid_ctx)
    if cur_geom is None:
        return None, target_el, geom
    if tail == '5':
        return cur_geom['points']['5'], target_el, cur_geom
    if tail in '1235789':
        return cur_geom['points'].get(tail), target_el, cur_geom
    if tail in 'abcdef':
        return cur_geom['sides'][tail]['mid'], target_el, cur_geom
    return None, target_el, geom


def _path_poly_refs(token: str, target_el, geom: dict, grid_ctx=None) -> str:
    refs = _split_poly_refs(token)
    if len(refs) < 2:
        return ''
    pts: List[_PT] = []
    cur_geom = geom
    for ref in refs:
        p, _, cur_geom = _resolve_ref_point(ref, target_el, cur_geom, grid_ctx)
        if p is None:
            try:
                _l.d(f"[paths] poly ref unresolved token='{token}' ref='{ref}' target='{target_el.get('id') if target_el is not None else ''}'")
            except Exception:
                pass
            return ''
        pts.append(p)
    try:
        _l.d(
            f"[paths] poly token='{token}' target='{target_el.get('id') if target_el is not None else ''}' "
            f"pts={[tuple(round(v,2) for v in p) for p in pts]}"
        )
    except Exception:
        pass
    return _path_poly(pts)


def token_to_path_d(token: str, geom: dict, *, target_el=None, grid_ctx=None) -> str:
    t = str(token or '').strip()
    if not t:
        return ''
    pts = geom['points']
    sides = geom['sides']
    center = pts['5']
    if re.fullmatch(r'[a-f]', t):
        s = sides[t]
        return _path_line(s['a'], s['b'])
    m = re.fullmatch(r'5([a-f])', t)
    if m:
        return _path_line(center, sides[m.group(1)]['mid'])
    m = re.fullmatch(r'5([A-F])5', t)
    if m:
        side = m.group(1).lower()
        local_mid = sides[side]['mid']
        pts_poly = [center, local_mid]
        neigh_geom = _geom_for_chain(geom, side, grid_ctx)
        if neigh_geom is not None:
            pts_poly.append(neigh_geom['center'])
        return _path_poly(pts_poly)
    m = re.fullmatch(r'([127893])([127893])', t)
    if m:
        a = pts.get(m.group(1)); b = pts.get(m.group(2))
        if a and b:
            return _path_line(a, b)
        return ''
    m = re.fullmatch(r'([a-f])([a-f])', t)
    if m:
        s1 = m.group(1); s2 = m.group(2)
        i1 = _SIDE_ORDER.index(s1); i2 = _SIDE_ORDER.index(s2)
        d = (i2 - i1) % 6
        p1 = sides[s1]['mid']; p2 = sides[s2]['mid']
        if d == 3:
            return _path_line(p1, p2)
        if d in (1, 2, 4, 5):
            return _path_side_arc(sides, s1, s2, center)
        return ''
    if any(ch in t for ch in 'ABCDEF') or len(t) >= 3:
        return _path_poly_refs(t, target_el, geom, grid_ctx)
    return ''


def render_paths_for_target(
    target_el,
    paths_spec_raw: str,
    *,
    orient_hint: Optional[str] = None,
    style_scope_node=None,
    insert_parent=None,
    insert_after_elem=None,
    grid_ctx=None,
) -> int:
    parent = insert_parent if insert_parent is not None else target_el.getparent()
    if parent is None:
        return 0
    try:
        anchor = insert_after_elem if (insert_after_elem is not None and insert_after_elem.getparent() is parent) else target_el
        insert_at = parent.index(anchor) + 1
    except Exception:
        insert_at = len(parent)
    created = build_paths_for_target(
        target_el,
        paths_spec_raw,
        orient_hint=orient_hint,
        style_scope_node=style_scope_node,
        grid_ctx=grid_ctx,
    )
    for p in created:
        parent.insert(insert_at, p)
        insert_at += 1
    return len(created)
