# -*- coding: utf-8 -*-
"""
mvt_minimal.py - Minimal Mapbox Vector Tile decoder without protobuf runtime.

This module only implements the protobuf subset needed by MVT:
  - varint fields
  - length-delimited fields

It is intentionally decode-only and focused on a small feature set suitable for
debugging and SVG export prototypes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from collections import Counter, defaultdict
import math
import re
import struct
import xml.etree.ElementTree as ET

import log as LOG
import map_style as MAP_STYLE

_l = LOG

WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LEN = 2
WIRE_32BIT = 5

GEOM_UNKNOWN = 0
GEOM_POINT = 1
GEOM_LINESTRING = 2
GEOM_POLYGON = 3


@dataclass
class MVTValue:
    value: object = None


@dataclass
class MVTFeature:
    id: Optional[int] = None
    type: int = GEOM_UNKNOWN
    properties: Dict[str, object] = field(default_factory=dict)
    geometry_cmds: List[int] = field(default_factory=list)


@dataclass
class MVTLayer:
    name: str = ""
    version: int = 1
    extent: int = 4096
    features: List[MVTFeature] = field(default_factory=list)
    keys: List[str] = field(default_factory=list)
    values: List[MVTValue] = field(default_factory=list)


@dataclass
class MVTTile:
    layers: List[MVTLayer] = field(default_factory=list)


class MVTDecodeError(ValueError):
    pass


def _read_varint(buf: bytes, pos: int) -> Tuple[int, int]:
    shift = 0
    out = 0
    while True:
        if pos >= len(buf):
            raise MVTDecodeError("Unexpected EOF while reading varint")
        b = buf[pos]
        pos += 1
        out |= (b & 0x7F) << shift
        if not (b & 0x80):
            return out, pos
        shift += 7
        if shift > 70:
            raise MVTDecodeError("Varint too large")


def _read_key(buf: bytes, pos: int) -> Tuple[int, int, int]:
    key, pos = _read_varint(buf, pos)
    field_no = key >> 3
    wire_type = key & 0x07
    return field_no, wire_type, pos


def _read_len(buf: bytes, pos: int) -> Tuple[bytes, int]:
    ln, pos = _read_varint(buf, pos)
    end = pos + ln
    if end > len(buf):
        raise MVTDecodeError("Unexpected EOF while reading length-delimited field")
    return buf[pos:end], end


def _skip_field(buf: bytes, pos: int, wire_type: int) -> int:
    if wire_type == WIRE_VARINT:
        _, pos = _read_varint(buf, pos)
        return pos
    if wire_type == WIRE_64BIT:
        end = pos + 8
        if end > len(buf):
            raise MVTDecodeError("Unexpected EOF while skipping 64-bit field")
        return end
    if wire_type == WIRE_LEN:
        _, pos = _read_len(buf, pos)
        return pos
    if wire_type == WIRE_32BIT:
        end = pos + 4
        if end > len(buf):
            raise MVTDecodeError("Unexpected EOF while skipping 32-bit field")
        return end
    raise MVTDecodeError(f"Unsupported wire type: {wire_type}")


def _zigzag_decode(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _decode_value(buf: bytes) -> MVTValue:
    pos = 0
    out: object = None
    while pos < len(buf):
        field_no, wire_type, pos = _read_key(buf, pos)
        if field_no == 1 and wire_type == WIRE_LEN:
            raw, pos = _read_len(buf, pos)
            out = raw.decode("utf-8", "replace")
        elif field_no == 2 and wire_type == WIRE_VARINT:
            out, pos = _read_varint(buf, pos)
        elif field_no == 3 and wire_type == WIRE_VARINT:
            raw, pos = _read_varint(buf, pos)
            out = bool(raw)
        elif field_no == 4 and wire_type == WIRE_64BIT:
            end = pos + 8
            if end > len(buf):
                raise MVTDecodeError("Unexpected EOF while reading double value")
            out = struct.unpack("<d", buf[pos:end])[0]
            pos = end
        elif field_no == 5 and wire_type == WIRE_32BIT:
            end = pos + 4
            if end > len(buf):
                raise MVTDecodeError("Unexpected EOF while reading float value")
            out = struct.unpack("<f", buf[pos:end])[0]
            pos = end
        elif field_no == 6 and wire_type == WIRE_VARINT:
            raw, pos = _read_varint(buf, pos)
            out = _zigzag_decode(raw)
        elif field_no == 7 and wire_type == WIRE_VARINT:
            raw, pos = _read_varint(buf, pos)
            out = raw
        else:
            pos = _skip_field(buf, pos, wire_type)
    return MVTValue(out)


def _decode_feature(buf: bytes, keys: Sequence[str], values: Sequence[MVTValue]) -> MVTFeature:
    pos = 0
    feat = MVTFeature()
    tag_indexes: List[int] = []
    while pos < len(buf):
        field_no, wire_type, pos = _read_key(buf, pos)
        if field_no == 1 and wire_type == WIRE_VARINT:
            feat.id, pos = _read_varint(buf, pos)
        elif field_no == 2 and wire_type == WIRE_LEN:
            raw, pos = _read_len(buf, pos)
            p = 0
            while p < len(raw):
                iv, p = _read_varint(raw, p)
                tag_indexes.append(iv)
        elif field_no == 3 and wire_type == WIRE_VARINT:
            feat.type, pos = _read_varint(buf, pos)
        elif field_no == 4 and wire_type == WIRE_LEN:
            raw, pos = _read_len(buf, pos)
            p = 0
            while p < len(raw):
                iv, p = _read_varint(raw, p)
                feat.geometry_cmds.append(iv)
        else:
            pos = _skip_field(buf, pos, wire_type)
    props: Dict[str, object] = {}
    for i in range(0, len(tag_indexes) - 1, 2):
        kidx = tag_indexes[i]
        vidx = tag_indexes[i + 1]
        if 0 <= kidx < len(keys) and 0 <= vidx < len(values):
            props[keys[kidx]] = values[vidx].value
    feat.properties = props
    return feat


def _decode_layer(buf: bytes) -> MVTLayer:
    pos = 0
    layer = MVTLayer()
    feature_blobs: List[bytes] = []
    while pos < len(buf):
        field_no, wire_type, pos = _read_key(buf, pos)
        if field_no == 1 and wire_type == WIRE_LEN:
            raw, pos = _read_len(buf, pos)
            layer.name = raw.decode("utf-8", "replace")
        elif field_no == 2 and wire_type == WIRE_LEN:
            raw, pos = _read_len(buf, pos)
            feature_blobs.append(raw)
        elif field_no == 3 and wire_type == WIRE_LEN:
            raw, pos = _read_len(buf, pos)
            layer.keys.append(raw.decode("utf-8", "replace"))
        elif field_no == 4 and wire_type == WIRE_LEN:
            raw, pos = _read_len(buf, pos)
            layer.values.append(_decode_value(raw))
        elif field_no == 5 and wire_type == WIRE_VARINT:
            layer.extent, pos = _read_varint(buf, pos)
        elif field_no == 15 and wire_type == WIRE_VARINT:
            layer.version, pos = _read_varint(buf, pos)
        else:
            pos = _skip_field(buf, pos, wire_type)
    layer.features = [_decode_feature(raw, layer.keys, layer.values) for raw in feature_blobs]
    return layer


def decode_tile(raw: bytes) -> MVTTile:
    pos = 0
    tile = MVTTile()
    while pos < len(raw):
        field_no, wire_type, pos = _read_key(raw, pos)
        if field_no == 3 and wire_type == WIRE_LEN:
            layer_raw, pos = _read_len(raw, pos)
            tile.layers.append(_decode_layer(layer_raw))
        else:
            pos = _skip_field(raw, pos, wire_type)
    return tile


def _decode_geometry_stream(cmds: Sequence[int]) -> List[List[Tuple[int, int]]]:
    cursor_x = 0
    cursor_y = 0
    pos = 0
    rings: List[List[Tuple[int, int]]] = []
    current: List[Tuple[int, int]] = []
    while pos < len(cmds):
        cmd_int = cmds[pos]
        pos += 1
        cmd_id = cmd_int & 0x7
        cmd_count = cmd_int >> 3
        if cmd_id == 1:  # MoveTo
            if current:
                rings.append(current)
                current = []
            for _ in range(cmd_count):
                dx = _zigzag_decode(cmds[pos]); pos += 1
                dy = _zigzag_decode(cmds[pos]); pos += 1
                cursor_x += dx
                cursor_y += dy
                current.append((cursor_x, cursor_y))
        elif cmd_id == 2:  # LineTo
            for _ in range(cmd_count):
                dx = _zigzag_decode(cmds[pos]); pos += 1
                dy = _zigzag_decode(cmds[pos]); pos += 1
                cursor_x += dx
                cursor_y += dy
                current.append((cursor_x, cursor_y))
        elif cmd_id == 7:  # ClosePath
            if current and current[0] != current[-1]:
                current.append(current[0])
        else:
            raise MVTDecodeError(f"Unsupported geometry command id: {cmd_id}")
    if current:
        rings.append(current)
    return rings


def decode_feature_geometry(feature: MVTFeature) -> List[List[Tuple[int, int]]]:
    return _decode_geometry_stream(feature.geometry_cmds)


def feature_to_viewbox_points(
    feature: MVTFeature,
    layer: MVTLayer,
    z: int,
    x: int,
    y: int,
    bbox: Tuple[float, float, float, float],
    width: float,
    height: float,
) -> List[List[Tuple[float, float]]]:
    geoms = decode_feature_geometry(feature)
    out: List[List[Tuple[float, float]]] = []
    for ring in geoms:
        pts = [
            _lonlat_to_viewbox(*tile_local_to_lonlat(px, py, z, x, y, layer.extent), bbox, width, height)
            for (px, py) in ring
        ]
        if pts:
            out.append(pts)
    return out


def tile_bounds_mercator(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    n = 2.0 ** z
    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_w, lat_s, lon_e, lat_n


def tile_local_to_lonlat(px: float, py: float, z: int, x: int, y: int, extent: int) -> Tuple[float, float]:
    gx = (x + (px / max(1, extent))) / (2.0 ** z)
    gy = (y + (py / max(1, extent))) / (2.0 ** z)
    lon = gx * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * gy))))
    return lon, lat


def _lonlat_to_viewbox(lon: float, lat: float, bbox: Tuple[float, float, float, float], width: float, height: float) -> Tuple[float, float]:
    min_lon, min_lat, max_lon, max_lat = bbox
    dx = max(max_lon - min_lon, 1e-9)
    dy = max(max_lat - min_lat, 1e-9)
    x = (lon - min_lon) / dx * width
    y = (max_lat - lat) / dy * height
    return x, y


def feature_to_svg_path(
    feature: MVTFeature,
    layer: MVTLayer,
    z: int,
    x: int,
    y: int,
    bbox: Tuple[float, float, float, float],
    width: float,
    height: float,
) -> Optional[str]:
    geoms = feature_to_viewbox_points(feature, layer, z, x, y, bbox, width, height)
    return viewbox_points_to_svg_path(geoms, feature.type)


def viewbox_points_to_svg_path(geoms: List[List[Tuple[float, float]]], geom_type: int) -> Optional[str]:
    if not geoms:
        return None
    parts: List[str] = []
    for ring in geoms:
        if not ring:
            continue
        cmds = [f"M {ring[0][0]:.2f},{ring[0][1]:.2f}"]
        for px, py in ring[1:]:
            cmds.append(f"L {px:.2f},{py:.2f}")
        if geom_type == GEOM_POLYGON:
            cmds.append("Z")
        parts.append(" ".join(cmds))
    return " ".join(parts) if parts else None


def _smooth_step(value: object) -> float:
    text = str(value if value is not None else "1").strip().lower()
    if text in {"", "0", "off", "false", "no", "none"}:
        return 0.0
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            return max(1.0, float(left) / max(1e-9, float(right)))
        except Exception:
            return 1.0
    try:
        return max(0.0, float(text))
    except Exception:
        return 1.0


def _smooth_indices(count: int, step: float) -> List[int]:
    n = int(count or 0)
    if n <= 1:
        return list(range(n))
    out = [0]
    pos = 0.0
    step = max(1.0, float(step or 1.0))
    while out[-1] < n - 1:
        pos += step
        idx = int(round(pos))
        if idx <= out[-1]:
            idx = out[-1] + 1
        if idx >= n - 1:
            break
        out.append(idx)
    out.append(n - 1)
    return out


def _smooth_closed_indices(count: int, step: float) -> List[int]:
    n = int(count or 0)
    if n <= 3:
        return list(range(n))
    target = max(3, int(round(float(n) / max(1.0, float(step or 1.0)))))
    target = min(n, target)
    out: List[int] = []
    for i in range(target):
        idx = int(round((float(i) * float(n)) / float(target))) % n
        if idx not in out:
            out.append(idx)
    return out if len(out) >= 3 else list(range(min(n, 3)))


def _avg_point(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    if not points:
        return 0.0, 0.0
    n = float(len(points))
    return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n


def _smooth_open_grouped_path(ring: List[Tuple[float, float]], step: float) -> str:
    idxs = _smooth_indices(len(ring), step)
    cmds = [f"M {ring[0][0]:.2f},{ring[0][1]:.2f}"]
    for a, b in zip(idxs, idxs[1:]):
        end = ring[b]
        if b <= a + 1:
            cmds.append(f"L {end[0]:.2f},{end[1]:.2f}")
            continue
        cx, cy = _avg_point(ring[a + 1:b])
        cmds.append(f"Q {cx:.2f},{cy:.2f} {end[0]:.2f},{end[1]:.2f}")
    return " ".join(cmds)


def _smooth_closed_grouped_path(points: List[Tuple[float, float]], step: float) -> str:
    idxs = _smooth_closed_indices(len(points), step)
    cmds = [f"M {points[idxs[0]][0]:.2f},{points[idxs[0]][1]:.2f}"]
    for pos, a in enumerate(idxs):
        b = idxs[(pos + 1) % len(idxs)]
        end = points[b]
        if b > a:
            between = points[a + 1:b]
        else:
            between = points[a + 1:] + points[:b]
        if between:
            cx, cy = _avg_point(between)
            cmds.append(f"Q {cx:.2f},{cy:.2f} {end[0]:.2f},{end[1]:.2f}")
        else:
            cmds.append(f"L {end[0]:.2f},{end[1]:.2f}")
    cmds.append("Z")
    return " ".join(cmds)


def viewbox_points_to_smooth_line_path(geoms: List[List[Tuple[float, float]]], min_points: int = 4, smooth: object = 1) -> Optional[str]:
    if not geoms:
        return None
    parts: List[str] = []
    step = _smooth_step(smooth)
    for ring in geoms:
        if step <= 0.0 or len(ring) < max(3, int(min_points or 4)) or ring[0] == ring[-1]:
            d = viewbox_points_to_svg_path([ring], GEOM_LINESTRING)
            if d:
                parts.append(d)
            continue
        if step > 1.0:
            parts.append(_smooth_open_grouped_path(ring, step))
            continue
        cmds = [f"M {ring[0][0]:.2f},{ring[0][1]:.2f}"]
        for i in range(1, len(ring) - 1):
            cx, cy = ring[i]
            nx, ny = ring[i + 1]
            mx = (cx + nx) * 0.5
            my = (cy + ny) * 0.5
            cmds.append(f"Q {cx:.2f},{cy:.2f} {mx:.2f},{my:.2f}")
        cmds.append(f"L {ring[-1][0]:.2f},{ring[-1][1]:.2f}")
        parts.append(" ".join(cmds))
    return " ".join(parts) if parts else None


def viewbox_points_to_smooth_polygon_path(geoms: List[List[Tuple[float, float]]], min_points: int = 4, smooth: object = 1) -> Optional[str]:
    if not geoms:
        return None
    parts: List[str] = []
    step = _smooth_step(smooth)
    for ring in geoms:
        pts = list(ring or [])
        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts.pop()
        if step <= 0.0 or len(pts) < max(3, int(min_points or 4)):
            d = viewbox_points_to_svg_path([ring], GEOM_POLYGON)
            if d:
                parts.append(d)
            continue
        if step > 1.0:
            parts.append(_smooth_closed_grouped_path(pts, step))
            continue
        start_x = (pts[-1][0] + pts[0][0]) * 0.5
        start_y = (pts[-1][1] + pts[0][1]) * 0.5
        cmds = [f"M {start_x:.2f},{start_y:.2f}"]
        for i, (cx, cy) in enumerate(pts):
            nx, ny = pts[(i + 1) % len(pts)]
            mx = (cx + nx) * 0.5
            my = (cy + ny) * 0.5
            cmds.append(f"Q {cx:.2f},{cy:.2f} {mx:.2f},{my:.2f}")
        cmds.append("Z")
        parts.append(" ".join(cmds))
    return " ".join(parts) if parts else None


def _points_overlap_viewbox(points: List[List[Tuple[float, float]]], width: float, height: float) -> bool:
    flat = [p for ring in (points or []) for p in (ring or [])]
    if not flat:
        return False
    minx = min(p[0] for p in flat)
    maxx = max(p[0] for p in flat)
    miny = min(p[1] for p in flat)
    maxy = max(p[1] for p in flat)
    return not (maxx < 0.0 or maxy < 0.0 or minx > float(width) or miny > float(height))


def _prune_empty_groups(node: ET.Element) -> bool:
    for child in list(node):
        if _prune_empty_groups(child):
            node.remove(child)
    return node.tag == "g" and len(list(node)) == 0


def _feature_anchor_point(points: List[List[Tuple[float, float]]]) -> Optional[Tuple[float, float]]:
    if not points or not points[0]:
        return None
    ring = points[0]
    sx = sum(p[0] for p in ring)
    sy = sum(p[1] for p in ring)
    n = max(1, len(ring))
    return sx / n, sy / n


def _geom_name(geom_type: int) -> str:
    if geom_type == GEOM_POINT:
        return "point"
    if geom_type == GEOM_LINESTRING:
        return "line"
    if geom_type == GEOM_POLYGON:
        return "polygon"
    return "unknown"


def _label_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _dedupe_labels(
    items: List[Tuple[ET.Element, str, float, float, Dict[str, str], str]],
    *,
    min_distance: float = 42.0,
) -> List[Tuple[ET.Element, str, float, float, Dict[str, str], str]]:
    kept: List[Tuple[ET.Element, str, float, float, Dict[str, str], str]] = []
    seen: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    limit2 = float(min_distance) * float(min_distance)
    for item in items:
        _parent, _label_id, x, y, _style, text = item
        key = _label_key(text)
        if key:
            too_close = any(((x - px) * (x - px) + (y - py) * (y - py)) <= limit2 for px, py in seen[key])
            if too_close:
                continue
            seen[key].append((x, y))
        kept.append(item)
    return kept


def export_debug_svg(
    raw_tile: bytes,
    z: int,
    x: int,
    y: int,
    out_path: Path,
    width: int = 1024,
    height: int = 1024,
    include_layers: Optional[Iterable[str]] = None,
) -> Path:
    return export_debug_svg_multi([(raw_tile, z, x, y)], out_path, width=width, height=height, include_layers=include_layers)


def export_debug_svg_multi(
    tile_specs: Sequence[Tuple[bytes, int, int, int]],
    out_path: Path,
    width: int = 1024,
    height: int = 1024,
    include_layers: Optional[Iterable[str]] = None,
    view_filter: Optional[Dict[str, object]] = None,
    bbox_override: Optional[Tuple[float, float, float, float]] = None,
) -> Path:
    root = build_debug_svg_multi(
        tile_specs,
        width=width,
        height=height,
        include_layers=include_layers,
        view_filter=view_filter,
        bbox_override=bbox_override,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def build_debug_svg_multi(
    tile_specs: Sequence[Tuple[bytes, int, int, int]],
    *,
    width: int = 1024,
    height: int = 1024,
    include_layers: Optional[Iterable[str]] = None,
    view_filter: Optional[Dict[str, object]] = None,
    bbox_override: Optional[Tuple[float, float, float, float]] = None,
    smooth: object = None,
) -> ET.Element:
    if not tile_specs:
        raise ValueError("tile_specs cannot be empty")
    zset = {int(spec[1]) for spec in tile_specs}
    if len(zset) != 1:
        raise ValueError("All tiles must have the same zoom")
    z = int(tile_specs[0][1])
    bounds = [tile_bounds_mercator(tz, tx, ty) for (_raw, tz, tx, ty) in tile_specs]
    bbox = bbox_override or (
        min(b[0] for b in bounds),
        min(b[1] for b in bounds),
        max(b[2] for b in bounds),
        max(b[3] for b in bounds),
    )
    root = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {width} {height}",
            "width": str(width),
            "height": str(height),
        },
    )
    defs = ET.SubElement(root, "defs")
    clip = ET.SubElement(defs, "clipPath", {"id": "map-clip"})
    ET.SubElement(clip, "rect", {"x": "0", "y": "0", "width": str(width), "height": str(height)})
    scene = ET.SubElement(root, "g", {"id": "map_group", "clip-path": "url(#map-clip)"})
    ET.SubElement(scene, "rect", {"x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#eee6d1"})
    allowed = {str(v) for v in include_layers} if include_layers else None
    label_groups: Dict[Tuple[str, str], ET.Element] = {}
    feature_id_counters: Dict[str, int] = {}
    layer_seen: Dict[str, int] = {}
    layer_included: Dict[str, int] = {}
    layer_labeled: Dict[str, int] = {}
    layer_kind_seen: Dict[str, Counter] = defaultdict(Counter)
    layer_kind_included: Dict[str, Counter] = defaultdict(Counter)
    layer_kind_geom_seen: Dict[str, Counter] = defaultdict(Counter)
    layer_kind_geom_included: Dict[str, Counter] = defaultdict(Counter)
    label_items: List[Tuple[ET.Element, str, float, float, Dict[str, str], str]] = []
    smooth_value = MAP_STYLE.smooth_value(smooth)
    smooth_min_points = MAP_STYLE.smooth_line_min_points(override=smooth)

    def _debug_layer(name: str) -> bool:
        lname = str(name or "").lower()
        return any(part in lname for part in ("place", "boundar", "admin"))

    labels_group = ET.SubElement(scene, "g", {"id": "labels_group"})

    def _tile_group(tz: int, tx: int, ty: int) -> ET.Element:
        lon_w, lat_s, lon_e, lat_n = tile_bounds_mercator(tz, tx, ty)
        x0, y0 = _lonlat_to_viewbox(lon_w, lat_n, bbox, width, height)
        x1, y1 = _lonlat_to_viewbox(lon_e, lat_s, bbox, width, height)
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        clip_id = f"clip_z{tz}x{tx}y{ty}"
        clip_node = ET.SubElement(defs, "clipPath", {"id": clip_id})
        ET.SubElement(clip_node, "rect", {"x": f"{rx:.2f}", "y": f"{ry:.2f}", "width": f"{rw:.2f}", "height": f"{rh:.2f}"})
        return ET.SubElement(scene, "g", {"id": f"tile_z{tz}x{tx}y{ty}_group", "clip-path": f"url(#{clip_id})"})

    def _label_group_for(layer_name: str, kind_name: str) -> ET.Element:
        key = (str(layer_name or ""), str(kind_name or "label"))
        hit = label_groups.get(key)
        if hit is not None:
            return hit
        label_gid = f"label_{str(kind_name or 'label')}_group"
        hit = ET.SubElement(labels_group, "g", {"id": label_gid})
        label_groups[key] = hit
        return hit

    for raw_tile, tz, tx, ty in tile_specs:
        tile = decode_tile(raw_tile)
        tile_parent = _tile_group(tz, tx, ty)
        for layer in sorted(tile.layers, key=lambda lyr: (MAP_STYLE.layer_order(lyr.name), lyr.name)):
            if allowed is not None and layer.name not in allowed:
                continue
            if not MAP_STYLE.view_allows_layer(view_filter, layer.name):
                continue
            layer_seen[layer.name] = layer_seen.get(layer.name, 0) + len(layer.features)
            if _debug_layer(layer.name):
                for feat in layer.features:
                    kind = MAP_STYLE.feature_kind(feat) or "(empty)"
                    layer_kind_seen[layer.name][kind] += 1
                    layer_kind_geom_seen[layer.name][f"{kind}/{_geom_name(feat.type)}"] += 1
            g = ET.SubElement(tile_parent, "g", {"id": f"{layer.name}_group"})
            kind_groups: Dict[str, ET.Element] = {}
            for feat in layer.features:
                if not MAP_STYLE.view_allows_feature(view_filter, layer.name, feat):
                    continue
                if allowed is None and not MAP_STYLE.include_feature(layer.name, feat, z):
                    continue
                layer_included[layer.name] = layer_included.get(layer.name, 0) + 1
                if _debug_layer(layer.name):
                    kind = MAP_STYLE.feature_kind(feat) or "(empty)"
                    layer_kind_included[layer.name][kind] += 1
                    layer_kind_geom_included[layer.name][f"{kind}/{_geom_name(feat.type)}"] += 1
                elem_id = MAP_STYLE.feature_id(layer.name, feat, feature_id_counters)
                draw_group = g
                kind_group = MAP_STYLE.feature_kind(feat)
                if kind_group and kind_group != str(layer.name or "").lower():
                    draw_group = kind_groups.get(kind_group)
                    if draw_group is None:
                        draw_group = ET.SubElement(g, "g", {"id": f"{kind_group}_group"})
                        kind_groups[kind_group] = draw_group
                pts = feature_to_viewbox_points(feat, layer, tz, tx, ty, bbox, width, height)
                if not _points_overlap_viewbox(pts, width, height):
                    continue
                if feat.type in (GEOM_POLYGON, GEOM_LINESTRING):
                    if feat.type == GEOM_LINESTRING and MAP_STYLE.smooth_line_enabled(layer.name, smooth):
                        path_d = viewbox_points_to_smooth_line_path(pts, smooth_min_points, smooth_value)
                    elif feat.type == GEOM_POLYGON and MAP_STYLE.smooth_polygon_enabled(layer.name, smooth):
                        path_d = viewbox_points_to_smooth_polygon_path(pts, smooth_min_points, smooth_value)
                    else:
                        path_d = viewbox_points_to_svg_path(pts, feat.type)
                    if not path_d:
                        continue
                    style = {"d": path_d, "id": elem_id}
                    style.update(MAP_STYLE.feature_style(layer.name, feat, feat.type, z))
                    ET.SubElement(draw_group, "path", style)
                elif feat.type == GEOM_POINT:
                    anchor = _feature_anchor_point(pts)
                    if not anchor:
                        continue
                    cx, cy = anchor
                    shape = MAP_STYLE.point_shape(layer.name, feat, z)
                    size = MAP_STYLE.point_size(layer.name, feat, z)
                    if shape == "triangle":
                        tri = f"M {cx:.2f},{cy - size * 1.55:.2f} L {cx - size * 1.35:.2f},{cy + size * 1.10:.2f} L {cx + size * 1.35:.2f},{cy + size * 1.10:.2f} Z"
                        ET.SubElement(draw_group, "path", {"id": elem_id, "d": tri, **MAP_STYLE.feature_style(layer.name, feat, feat.type, z)})
                    elif shape not in {"none", "hidden", "off", "label"}:
                        ET.SubElement(draw_group, "circle", {"id": elem_id, "cx": f"{cx:.2f}", "cy": f"{cy:.2f}", "r": f"{size:.2f}", **MAP_STYLE.feature_style(layer.name, feat, feat.type, z)})
                    label = MAP_STYLE.feature_label_text(feat) if (MAP_STYLE.labels_enabled(layer.name) and MAP_STYLE.view_allows_label(view_filter, layer.name)) else None
                    if label:
                        layer_labeled[layer.name] = layer_labeled.get(layer.name, 0) + 1
                        dx, dy = MAP_STYLE.label_offset(layer.name, feat)
                        text_style = MAP_STYLE.label_style(layer.name, feat, z)
                        label_parent = _label_group_for(layer.name, kind_group or layer.name)
                        label_items.append((label_parent, f"label_{elem_id}", cx + dx, cy + dy, text_style, label))
    if label_items:
        for label_parent, label_id, lx, ly, text_style, label in _dedupe_labels(label_items):
            t = ET.SubElement(
                label_parent,
                "text",
                {"id": label_id, "x": f"{lx:.2f}", "y": f"{ly:.2f}", **text_style},
            )
            t.text = label
    if labels_group in list(scene):
        scene.remove(labels_group)
        scene.append(labels_group)
    _prune_empty_groups(scene)
    for lname in sorted(name for name in layer_seen if _debug_layer(name)):
        _l.i(
            "[map.debug] layer=%s z=%d features=%d included=%d labels=%d",
            lname,
            z,
            layer_seen.get(lname, 0),
            layer_included.get(lname, 0),
            layer_labeled.get(lname, 0),
        )
        _l.i(
            "[map.debug] layer=%s kinds=%s included_kinds=%s",
            lname,
            ",".join(f"{k}:{v}" for k, v in layer_kind_seen[lname].most_common(12)),
            ",".join(f"{k}:{v}" for k, v in layer_kind_included[lname].most_common(12)),
        )
        _l.i(
            "[map.debug] layer=%s kind_geom=%s included_kind_geom=%s",
            lname,
            ",".join(f"{k}:{v}" for k, v in layer_kind_geom_seen[lname].most_common(16)),
            ",".join(f"{k}:{v}" for k, v in layer_kind_geom_included[lname].most_common(16)),
        )
    return root


def describe_tile(raw_tile: bytes) -> List[str]:
    tile = decode_tile(raw_tile)
    lines: List[str] = []
    for layer in tile.layers:
        feat_count = len(layer.features)
        geom_counts = {GEOM_POINT: 0, GEOM_LINESTRING: 0, GEOM_POLYGON: 0, GEOM_UNKNOWN: 0}
        for feat in layer.features:
            geom_counts[feat.type] = geom_counts.get(feat.type, 0) + 1
        sample_keys = sorted({k for feat in layer.features[:50] for k in feat.properties.keys()})
        lines.append(
            f"{layer.name}: features={feat_count} extent={layer.extent} "
            f"points={geom_counts.get(GEOM_POINT,0)} "
            f"lines={geom_counts.get(GEOM_LINESTRING,0)} "
            f"polygons={geom_counts.get(GEOM_POLYGON,0)} "
            f"sample_keys={','.join(sample_keys[:12])}"
        )
    return lines


def sample_tile_properties(raw_tile: bytes, max_features_per_layer: int = 5) -> List[str]:
    tile = decode_tile(raw_tile)
    lines: List[str] = []
    for layer in tile.layers:
        lines.append(f"[{layer.name}]")
        shown = 0
        for feat in layer.features:
            if shown >= max_features_per_layer:
                break
            if not feat.properties:
                continue
            lines.append(f"  feature id={feat.id} type={feat.type} props={feat.properties}")
            shown += 1
        if shown == 0:
            lines.append("  (no properties in sampled features)")
    return lines


def summarize_tile_properties(raw_tile: bytes, max_values_per_key: int = 12) -> List[str]:
    tile = decode_tile(raw_tile)
    lines: List[str] = []
    for layer in tile.layers:
        lines.append(f"[{layer.name}]")
        counters: Dict[str, Counter] = defaultdict(Counter)
        missing: Counter = Counter()
        for feat in layer.features:
            seen = set()
            for key, value in feat.properties.items():
                sval = repr(value)
                counters[key][sval] += 1
                seen.add(key)
            all_keys = set(counters.keys()) | set(feat.properties.keys())
            for key in all_keys:
                if key not in seen:
                    missing[key] += 1
        if not counters:
            lines.append("  (no properties)")
            continue
        for key in sorted(counters.keys()):
            top = counters[key].most_common(max(1, max_values_per_key))
            values = ", ".join(f"{val} x{count}" for val, count in top)
            miss = missing.get(key, 0)
            if miss:
                values += f", <missing> x{miss}"
            lines.append(f"  {key}: {values}")
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Decode a .mvt tile and emit a debug SVG")
    ap.add_argument("tile", help="Path to .mvt tile")
    ap.add_argument("z", type=int)
    ap.add_argument("x", type=int)
    ap.add_argument("y", type=int)
    ap.add_argument("--out", default="examples/assets/mvt_debug.svg")
    ap.add_argument("--layers", nargs="*", default=None)
    ap.add_argument("--list-layers", action="store_true")
    ap.add_argument("--dump-props", action="store_true")
    ap.add_argument("--max-props", type=int, default=5)
    ap.add_argument("--summarize-props", action="store_true")
    ap.add_argument("--max-values", type=int, default=12)
    ap.add_argument("--extra-tile", nargs=3, action="append", metavar=("X", "Y", "PATH"),
                    help="Add adjacent tile with same zoom as: --extra-tile X Y PATH")
    ns = ap.parse_args(argv)

    raw = Path(ns.tile).read_bytes()
    if ns.list_layers:
        for line in describe_tile(raw):
            print(line)
        return 0
    if ns.dump_props:
        for line in sample_tile_properties(raw, max_features_per_layer=max(1, ns.max_props)):
            print(line)
        return 0
    if ns.summarize_props:
        for line in summarize_tile_properties(raw, max_values_per_key=max(1, ns.max_values)):
            print(line)
        return 0
    tile_specs: List[Tuple[bytes, int, int, int]] = [(raw, ns.z, ns.x, ns.y)]
    for item in (ns.extra_tile or []):
        tx = int(item[0])
        ty = int(item[1])
        raw_extra = Path(item[2]).read_bytes()
        tile_specs.append((raw_extra, ns.z, tx, ty))
    export_debug_svg_multi(tile_specs, Path(ns.out), include_layers=ns.layers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
