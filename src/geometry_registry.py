"""geometry_registry.py - absolute geometry index for grid-aware path resolution."""

from __future__ import annotations

import math
from statistics import median
from typing import Dict, List, Optional, Tuple

import inkex

import log as LOG
_l = LOG
import svg as SVG

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


def family_base_for(el) -> str:
    try:
        return (el.get('data-origid') or SVG.strip_pnp_suffix(el.get('id') or '') or '').strip()
    except Exception:
        return ''


def _is_path(node) -> bool:
    try:
        t = node.tag
    except Exception:
        return False
    return isinstance(t, str) and t.endswith('path')


def _pt_mid(a: _PT, b: _PT) -> _PT:
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _dist(a: _PT, b: _PT) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _norm(vx: float, vy: float) -> _PT:
    ln = math.hypot(vx, vy)
    if ln <= 1e-9:
        return (0.0, 0.0)
    return (vx / ln, vy / ln)


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
            d = target_el.get('d') or ''
            pts = SVG.path_characteristic_points(d, SVG.composed_transform(target_el))
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
            d = target_el.get('d') or ''
            raw_pts = SVG.path_characteristic_points(d, SVG.composed_transform(target_el))
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


class GridGeometryRegistry:
    def __init__(self):
        self._placed_groups: List[Tuple[int, object]] = []
        self._clusters: Dict[Tuple[int, str, str], dict] = {}
        self._entry_by_elid: Dict[int, dict] = {}

    def add_group(self, page_index: int, group_node) -> None:
        if group_node is None:
            return
        self._placed_groups.append((int(page_index), group_node))

    def _build_cluster(self, page_index: int, family: str, orient_hint: Optional[str]) -> dict:
        key = (int(page_index), str(family or ''), str(orient_hint or '').strip().lower())
        if key in self._clusters:
            return self._clusters[key]
        entries = []
        if not family:
            cluster = {'entries': [], 'spacing': None, 'family': family, 'page_index': int(page_index)}
            self._clusters[key] = cluster
            return cluster
        for pg, group in self._placed_groups:
            if int(pg) != int(page_index) or group is None:
                continue
            try:
                hits = group.xpath(f".//*[@data-origid='{family}']")
            except Exception:
                hits = []
            for el in (hits or []):
                geom = hex_geometry_for_target(el, orient_hint)
                if not geom:
                    continue
                entry = {'el': el, 'geom': geom, 'page_index': int(page_index), 'family': family, 'neighbors': {}}
                entries.append(entry)
                self._entry_by_elid[id(el)] = entry
        spacing = None
        if len(entries) >= 2:
            dmins = []
            for e in entries:
                c = e['geom']['center']
                best = None
                for f in entries:
                    if f is e:
                        continue
                    d = _dist(c, f['geom']['center'])
                    if d <= 1e-6:
                        continue
                    best = d if best is None else min(best, d)
                if best is not None:
                    dmins.append(best)
            if dmins:
                spacing = float(median(sorted(dmins)))
        cluster = {'entries': entries, 'spacing': spacing, 'family': family, 'page_index': int(page_index)}
        self._clusters[key] = cluster
        self._precompute_neighbors(cluster)
        return cluster

    def ensure_cluster_for(self, page_index: int, target_el, orient_hint: Optional[str]) -> dict:
        family = family_base_for(target_el)
        return self._build_cluster(page_index, family, orient_hint)

    def entry_for(self, target_el) -> Optional[dict]:
        return self._entry_by_elid.get(id(target_el))

    def _precompute_neighbors(self, cluster: dict) -> None:
        entries = cluster.get('entries') or []
        spacing = float(cluster.get('spacing') or 0.0)
        if spacing <= 1e-9:
            return
        max_dist = spacing * 1.35
        max_perp = spacing * 0.30
        min_proj = spacing * 0.55
        for entry in entries:
            geom = entry['geom']
            c = geom['center']
            for side in _SIDE_ORDER:
                side_info = geom['sides'][side]
                nx, ny = _norm(side_info['mid'][0] - c[0], side_info['mid'][1] - c[1])
                tx, ty = (-ny, nx)
                best = None
                best_score = None
                for other in entries:
                    if other is entry:
                        continue
                    c2 = other['geom']['center']
                    vx = float(c2[0]) - float(c[0])
                    vy = float(c2[1]) - float(c[1])
                    dist = math.hypot(vx, vy)
                    if dist <= 1e-6 or dist > max_dist:
                        continue
                    proj = vx * nx + vy * ny
                    perp = abs(vx * tx + vy * ty)
                    if proj <= min_proj or perp > max_perp:
                        continue
                    ang = math.degrees(math.atan2(vy, vx))
                    want_ang = math.degrees(math.atan2(ny, nx))
                    ang_err = _angle_diff_deg(ang, want_ang)
                    if ang_err > 18.0:
                        continue
                    score = (ang_err, abs(dist - spacing), perp, dist)
                    if best is None or score < best_score:
                        best = other
                        best_score = score
                entry['neighbors'][side] = best

    def neighbor_for(self, target_el, side: str) -> Optional[dict]:
        entry = self.entry_for(target_el)
        if not entry:
            return None
        return (entry.get('neighbors') or {}).get(str(side or '').lower())
