# -*- coding: utf-8 -*-
"""paths.py - shared helpers for path-style templates and hex path generation."""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Dict, List, Optional, Tuple

import inkex

import log as LOG
_l = LOG
import svg as SVG
import prefs

_PT = Tuple[float, float]
_SIDE_ORDER = ('a', 'b', 'c', 'd', 'e', 'f')
_POINT_ORDER = ('8', '9', '3', '2', '1', '7')
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


def resolve_style_templates(root, style_id: Optional[str]):
    """Resolve a style template id into one or more path template elements.

    If style_id points to a group, all descendant paths are used in document order.
    If it points to a path, use that single path.
    """
    sid = str(style_id or '').strip()
    if not sid:
        return []
    el = root.find(f".//*[@id='{sid}']")
    if el is None:
        _l.w(f"[paths] style id '{sid}' not found")
        return []
    if _is_g(el):
        out = []
        try:
            paths = el.findall(".//{%s}path" % inkex.NSS['svg']) or el.findall('.//path')
            for p in paths:
                if _is_path(p):
                    out.append(p)
        except Exception:
            out = []
        return out
    return [el] if _is_path(el) else []


def instantiate_styled_path(template_el, d_attr: str):
    """Create a new <path> with style cloned from template_el and geometry d_attr."""
    p = SVG.etree.Element(inkex.addNS('path', 'svg'))
    if template_el is None:
        p.set('d', d_attr)
        p.set('style', 'fill:none;stroke:#000000;stroke-width:1;')
        return p
    for k, v in dict(template_el.attrib or {}).items():
        if k in ('id', 'd', 'transform', inkex.addNS('label', 'inkscape')):
            continue
        try:
            p.set(k, v)
        except Exception:
            pass
    p.set('d', d_attr)
    return p


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


def _detect_hex_orientation(target_el, orient_hint: Optional[str] = None) -> str:
    orient = str(orient_hint or '').strip().lower()
    if orient in ('pointy', 'flat'):
        return orient
    try:
        if _is_path(target_el):
            d = target_el.get('d') or ''
            pts = SVG.path_characteristic_points(d, target_el.get('transform'))
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


def _pt_mid(a: _PT, b: _PT) -> _PT:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _dist(a: _PT, b: _PT) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _norm(vx: float, vy: float) -> _PT:
    ln = math.hypot(vx, vy)
    if ln <= 1e-9:
        return (0.0, 0.0)
    return (vx / ln, vy / ln)


def hex_geometry_for_target(target_el, orient_hint: Optional[str] = None) -> Optional[dict]:
    bb = SVG.visual_bbox(target_el)
    if not bb:
        return None
    x, y, w, h = bb
    x0 = float(x); y0 = float(y); x1 = x0 + float(w); y1 = y0 + float(h)
    cx = x0 + float(w) * 0.5; cy = y0 + float(h) * 0.5
    orient = _detect_hex_orientation(target_el, orient_hint)
    if orient == 'flat':
        pts = {
            '8': (cx - float(w) * 0.25, y0),
            '9': (cx + float(w) * 0.25, y0),
            '3': (x1, cy),
            '2': (cx + float(w) * 0.25, y1),
            '1': (cx - float(w) * 0.25, y1),
            '7': (x0, cy),
            '5': (cx, cy),
        }
    else:
        pts = {
            '8': (cx, y0),
            '9': (x1, cy - float(h) * 0.25),
            '3': (x1, cy + float(h) * 0.25),
            '2': (cx, y1),
            '1': (x0, cy + float(h) * 0.25),
            '7': (x0, cy - float(h) * 0.25),
            '5': (cx, cy),
        }
    sides = {}
    for s, (pa, pb) in _SIDE_POINTS.items():
        a = pts[pa]; b = pts[pb]
        m = _pt_mid(a, b)
        # inward normal approximated by midpoint -> center
        inward = _norm(cx - m[0], cy - m[1])
        sides[s] = {'a': a, 'b': b, 'mid': m, 'inward': inward}
    return {'orient': orient, 'points': pts, 'sides': sides, 'center': (cx, cy), 'bbox': (x0, y0, float(w), float(h))}


def _neighbor_center_for_side(geom: dict, side: str) -> _PT:
    info = geom['sides'][side]
    mid = info['mid']
    c = geom['center']
    # Mirror the center across the side line via midpoint + inward normal.
    nx, ny = _norm(mid[0] - c[0], mid[1] - c[1])
    dist = _dist(c, mid)
    return (c[0] + 2.0 * dist * nx, c[1] + 2.0 * dist * ny)


def _find_neighbor_geom(scope_node, target_el, geom: dict, side: str, orient_hint: Optional[str] = None) -> Optional[dict]:
    want = _neighbor_center_for_side(geom, side)
    w = float(geom['bbox'][2]); h = float(geom['bbox'][3])
    tol = max(w, h) * 0.35
    best = None
    best_d = None
    for el in scope_node.iter():
        if el is target_el:
            continue
        if not str(getattr(el, 'tag', '') or '').endswith(('rect', 'path', 'polygon')) and not str(getattr(el, 'tag', '') or '').endswith('image'):
            continue
        g2 = hex_geometry_for_target(el, orient_hint)
        if not g2:
            continue
        c2 = g2['center']
        d = _dist(c2, want)
        if d > tol:
            continue
        if best is None or d < best_d:
            best = g2
            best_d = d
    return best


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


def token_to_path_d(token: str, geom: dict, *, scope_node=None, target_el=None, orient_hint: Optional[str] = None) -> str:
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
        ng = _find_neighbor_geom(scope_node, target_el, geom, side, orient_hint) if (scope_node is not None and target_el is not None) else None
        if ng is not None:
            pts_poly.append(ng['center'])
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
            return _path_quad(p1, center, p2)
        return ''
    return ''


def render_paths_for_target(scope_node, target_el, paths_spec_raw: str, *, orient_hint: Optional[str] = None) -> int:
    geom = hex_geometry_for_target(target_el, orient_hint)
    if not geom:
        _l.w(f"[paths] target id='{target_el.get('id') if target_el is not None else ''}' has no usable bbox")
        return 0
    parent = target_el.getparent()
    if parent is None:
        return 0
    blocks = parse_paths_block(paths_spec_raw)
    insert_at = parent.index(target_el) + 1
    created = 0
    for blk in blocks:
        layers = resolve_style_templates(scope_node, blk.get('style_id'))
        if not layers:
            _l.w(f"[paths] style '{blk.get('style_id')}' produced no path templates")
            continue
        for tok in (blk.get('tokens') or []):
            d_attr = token_to_path_d(tok, geom, scope_node=scope_node, target_el=target_el, orient_hint=orient_hint)
            if not d_attr:
                continue
            for lay in layers:
                p = instantiate_styled_path(lay, d_attr)
                parent.insert(insert_at, p)
                insert_at += 1
                created += 1
    return created
