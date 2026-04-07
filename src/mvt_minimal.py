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
    if not geoms:
        return None
    parts: List[str] = []
    for ring in geoms:
        cmds = [f"M {ring[0][0]:.2f},{ring[0][1]:.2f}"]
        for px, py in ring[1:]:
            cmds.append(f"L {px:.2f},{py:.2f}")
        if feature.type == GEOM_POLYGON:
            cmds.append("Z")
        parts.append(" ".join(cmds))
    return " ".join(parts) if parts else None


def _feature_label_text(feature: MVTFeature) -> Optional[str]:
    for key in ("name", "name_en", "name:latin", "name_int"):
        val = feature.properties.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _kind(feature: MVTFeature) -> str:
    v = feature.properties.get("kind")
    if v in (None, ""):
        v = feature.properties.get("class")
    return str(v or "").strip().lower()


def _slug(s: object) -> str:
    t = str(s or "").strip().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return t or "item"


def _feature_id(layer_name: str, feature: MVTFeature, counters: Dict[str, int]) -> str:
    layer_slug = _slug(layer_name)
    kind_slug = _slug(_kind(feature))
    key = f"{layer_slug}:{kind_slug}"
    counters[key] = counters.get(key, 0) + 1
    suffix = counters[key]
    if feature.id is not None:
        return f"{kind_slug}_{feature.id}"
    return f"{kind_slug}_{suffix}"


def _zoom_band(z: int) -> int:
    if z <= 9:
        return 0
    if z <= 11:
        return 1
    return 2


def _feature_anchor_point(points: List[List[Tuple[float, float]]]) -> Optional[Tuple[float, float]]:
    if not points or not points[0]:
        return None
    ring = points[0]
    sx = sum(p[0] for p in ring)
    sy = sum(p[1] for p in ring)
    n = max(1, len(ring))
    return sx / n, sy / n


def _layer_style(layer_name: str, geom_type: int) -> Dict[str, str]:
    lname = (layer_name or "").lower()
    if geom_type == GEOM_POLYGON:
        if "ocean" in lname or "water" in lname:
            return {"fill": "#95c8eb", "stroke": "#4d98cf", "stroke-width": "0.5"}
        if "land" in lname:
            return {"fill": "#d8d2bd", "stroke": "#5a4328", "stroke-width": "0.4"}
        if "building" in lname:
            return {"fill": "#d7c7c0", "stroke": "#8e776f", "stroke-width": "0.3"}
        return {"fill": "#ddd", "stroke": "#666", "stroke-width": "0.4"}
    if geom_type == GEOM_LINESTRING:
        if "water" in lname:
            return {"fill": "none", "stroke": "#4d98cf", "stroke-width": "0.9"}
        if "street" in lname or "road" in lname or "transport" in lname:
            return {"fill": "none", "stroke": "#7d6b5d", "stroke-width": "0.8"}
        if "boundary" in lname:
            return {"fill": "none", "stroke": "#996", "stroke-width": "0.5", "stroke-dasharray": "3 2"}
        return {"fill": "none", "stroke": "#444", "stroke-width": "0.7"}
    return {"fill": "#000", "stroke": "#fff", "stroke-width": "0.8"}


def _default_include_feature(layer_name: str, feature: MVTFeature, z: int) -> bool:
    lname = (layer_name or "").lower()
    kind = _kind(feature)
    band = _zoom_band(z)
    rank = int(feature.properties.get("rank") or 999)
    ele = int(feature.properties.get("ele") or -9999)
    if lname == "ocean":
        return True
    if lname == "water":
        return kind in ("ocean", "lake")
    if lname == "water_name":
        return band >= 1 and kind in ("bay", "lake", "ocean")
    if lname == "waterway":
        return kind in ("river",)
    if lname == "water_polygons":
        return kind in ("water", "river", "reservoir", "basin", "lagoon")
    if lname == "water_lines":
        return band >= 1 and kind in ("river",)
    if lname == "water_polygons_labels":
        return False
    if lname == "boundaries":
        return False
    if lname == "mountain_peak":
        if kind == "saddle":
            return band >= 2 and rank <= 3
        if band == 0:
            return kind == "peak" and rank <= 2 and ele >= 300
        if band == 1:
            return kind == "peak" and rank <= 3 and ele >= 120
        return kind == "peak" and rank <= 4 and ele >= 50
    if lname == "park":
        return band >= 1
    if lname == "landcover":
        return kind in ("wood", "grass", "wetland")
    if lname == "landuse":
        return band >= 1 and kind in ("industrial", "commercial", "university", "school", "hospital", "quarry", "military", "railway", "residential")
    if lname == "place_labels":
        if band == 0:
            return kind in ("city", "town")
        if band == 1:
            return kind in ("city", "suburb", "village", "island")
        return kind in ("city", "town", "suburb", "village", "island")
    if lname == "place":
        if band == 0:
            return kind in ("city", "town", "island") and rank <= 8
        if band == 1:
            return kind in ("city", "town", "village", "island") and rank <= 10
        return kind in ("city", "town", "village", "hamlet", "island") and rank <= 12
    if lname == "land":
        if band == 0:
            return kind == "forest"
        return kind in (
            "forest", "park", "grass", "grassland", "meadow", "garden", "golf_course",
            "orchard", "farmland", "farmyard", "beach", "industrial", "commercial",
            "brownfield", "bare_rock", "heath", "allotments", "greenfield",
            "greenhouse_horticulture", "garages",
        )
    if lname == "streets":
        rail = int(feature.properties.get("rail") or 0)
        if band == 0:
            return rail == 1 or kind in ("motorway", "trunk", "primary", "narrow_gauge", "rail")
        return rail == 1 or kind in ("motorway", "trunk", "primary", "secondary", "tertiary", "narrow_gauge", "rail")
    if lname == "transportation":
        return kind in ("motorway", "trunk", "primary", "secondary", "tertiary", "rail")
    if lname == "transportation_name":
        return kind in ("motorway", "trunk", "primary", "rail")
    if lname == "street_labels":
        return band >= 1 and kind in ("motorway", "rail", "funicular")
    return True


def _feature_style(layer_name: str, feature: MVTFeature) -> Dict[str, str]:
    lname = (layer_name or "").lower()
    kind = _kind(feature)
    if feature.type == GEOM_POLYGON:
        if lname == "ocean":
            return {"fill": "#b9d8f2", "stroke": "none"}
        if lname == "water":
            if kind == "ocean":
                return {"fill": "#b9d8f2", "stroke": "none"}
            return {"fill": "#8ec4e8", "stroke": "#6baed6", "stroke-width": "0.35"}
        if lname == "water_polygons":
            if kind == "river":
                return {"fill": "#9fcfee", "stroke": "#79b4db", "stroke-width": "0.35"}
            return {"fill": "#8ec4e8", "stroke": "#6baed6", "stroke-width": "0.35"}
        if lname == "landcover":
            if kind == "wood":
                return {"fill": "#c9ddb2", "stroke": "#a7c08a", "stroke-width": "0.2"}
            if kind == "grass":
                return {"fill": "#d9e7bf", "stroke": "#bccf95", "stroke-width": "0.18"}
            if kind == "wetland":
                return {"fill": "#cddfb3", "stroke": "#9ab88a", "stroke-width": "0.18"}
        if lname == "landuse":
            if kind in ("industrial", "commercial", "railway", "quarry", "military"):
                return {"fill": "#d8d4cf", "stroke": "#bbb2aa", "stroke-width": "0.18"}
            if kind in ("university", "school", "hospital"):
                return {"fill": "#e8e5c8", "stroke": "#cfcaa1", "stroke-width": "0.18"}
            if kind == "residential":
                return {"fill": "#efe7dd", "stroke": "none"}
        if lname == "park":
            return {"fill": "#c6ddb0", "stroke": "#9fbe83", "stroke-width": "0.22"}
        if lname == "land":
            if kind in ("forest", "park", "grass", "grassland", "meadow", "garden", "golf_course", "orchard"):
                return {"fill": "#c9ddb2", "stroke": "#a7c08a", "stroke-width": "0.25"}
            if kind in ("farmland", "farmyard", "allotments", "greenhouse_horticulture"):
                return {"fill": "#d8dfb0", "stroke": "#b7c184", "stroke-width": "0.22"}
            if kind in ("beach",):
                return {"fill": "#ecd8aa", "stroke": "#d7bf8a", "stroke-width": "0.2"}
            if kind in ("industrial", "commercial", "brownfield", "garages", "greenfield"):
                return {"fill": "#d8d4cf", "stroke": "#bbb2aa", "stroke-width": "0.2"}
            if kind in ("bare_rock", "heath"):
                return {"fill": "#d2c6b6", "stroke": "#b9ab99", "stroke-width": "0.2"}
            return {"fill": "#d8d2bd", "stroke": "#c4bba8", "stroke-width": "0.18"}
        return _layer_style(layer_name, feature.type)
    if feature.type == GEOM_LINESTRING:
        if lname == "waterway":
            return {"fill": "none", "stroke": "#6fb2e4", "stroke-width": "0.8"}
        if lname == "boundary":
            return {"fill": "none", "stroke": "#988f84", "stroke-width": "0.4", "stroke-dasharray": "3 2"}
        if lname == "transportation":
            subclass = str(feature.properties.get("subclass") or "").strip().lower()
            if kind == "rail" or subclass == "rail":
                return {"fill": "none", "stroke": "#555", "stroke-width": "0.8"}
            if kind == "motorway":
                return {"fill": "none", "stroke": "#b76e3a", "stroke-width": "1.15"}
            if kind == "trunk":
                return {"fill": "none", "stroke": "#c58a57", "stroke-width": "0.95"}
            if kind == "primary":
                return {"fill": "none", "stroke": "#d6ab76", "stroke-width": "0.75"}
            if kind == "secondary":
                return {"fill": "none", "stroke": "#e0bf92", "stroke-width": "0.55"}
            if kind == "tertiary":
                return {"fill": "none", "stroke": "#e9d4b2", "stroke-width": "0.4"}
        if lname == "transportation_name":
            if kind == "motorway":
                return {"fill": "none", "stroke": "#a55f31", "stroke-width": "0.9"}
            if kind in ("trunk", "primary"):
                return {"fill": "none", "stroke": "#b58457", "stroke-width": "0.7"}
            return {"fill": "none", "stroke": "#555", "stroke-width": "0.7"}
        if lname == "water_lines":
            return {"fill": "none", "stroke": "#6fb2e4", "stroke-width": "0.8"}
        if lname == "streets":
            rail = int(feature.properties.get("rail") or 0)
            if rail == 1 or kind in ("rail", "narrow_gauge"):
                return {"fill": "none", "stroke": "#555", "stroke-width": "0.8"}
            if kind == "motorway":
                return {"fill": "none", "stroke": "#b76e3a", "stroke-width": "1.15"}
            if kind == "trunk":
                return {"fill": "none", "stroke": "#c58a57", "stroke-width": "0.95"}
            if kind == "primary":
                return {"fill": "none", "stroke": "#d6ab76", "stroke-width": "0.75"}
            if kind == "secondary":
                return {"fill": "none", "stroke": "#e0bf92", "stroke-width": "0.55"}
            if kind == "tertiary":
                return {"fill": "none", "stroke": "#e9d4b2", "stroke-width": "0.4"}
        if lname == "street_labels":
            if kind == "motorway":
                return {"fill": "none", "stroke": "#a55f31", "stroke-width": "1.0"}
            return {"fill": "none", "stroke": "#555", "stroke-width": "0.7"}
        return _layer_style(layer_name, feature.type)
    if lname == "mountain_peak":
        if kind == "saddle":
            return {"fill": "#7c6855", "stroke": "#fff", "stroke-width": "0.8"}
        return {"fill": "#5f5244", "stroke": "#fff", "stroke-width": "0.8"}
    return _layer_style(layer_name, feature.type)


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
) -> Path:
    if not tile_specs:
        raise ValueError("tile_specs cannot be empty")
    zset = {int(spec[1]) for spec in tile_specs}
    if len(zset) != 1:
        raise ValueError("All tiles must have the same zoom")
    z = int(tile_specs[0][1])
    bounds = [tile_bounds_mercator(tz, tx, ty) for (_raw, tz, tx, ty) in tile_specs]
    bbox = (
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
    ET.SubElement(root, "rect", {"x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#eee6d1"})
    allowed = {str(v) for v in include_layers} if include_layers else None
    order = {
        "ocean": 0,
        "water": 1,
        "water_polygons": 1,
        "waterway": 2,
        "water_lines": 2,
        "landcover": 3,
        "park": 4,
        "landuse": 5,
        "land": 6,
        "transportation": 7,
        "streets": 7,
        "transportation_name": 8,
        "street_labels": 8,
        "mountain_peak": 9,
        "place": 10,
        "place_labels": 10,
        "water_name": 11,
        "water_polygons_labels": 11,
        "boundaries": 12,
        "boundary": 12,
    }
    layer_groups: Dict[str, ET.Element] = {}
    feature_id_counters: Dict[str, int] = {}
    for raw_tile, tz, tx, ty in tile_specs:
        tile = decode_tile(raw_tile)
        for layer in sorted(tile.layers, key=lambda lyr: (order.get(lyr.name, 50), lyr.name)):
            if allowed is not None and layer.name not in allowed:
                continue
            g = layer_groups.get(layer.name)
            if g is None:
                g = ET.SubElement(root, "g", {"id": layer.name})
                layer_groups[layer.name] = g
            for feat in layer.features:
                if allowed is None and not _default_include_feature(layer.name, feat, z):
                    continue
                elem_id = _feature_id(layer.name, feat, feature_id_counters)
                pts = feature_to_viewbox_points(feat, layer, tz, tx, ty, bbox, width, height)
                path_d = feature_to_svg_path(feat, layer, tz, tx, ty, bbox, width, height)
                if feat.type in (GEOM_POLYGON, GEOM_LINESTRING):
                    if not path_d:
                        continue
                    style = {"d": path_d, "id": elem_id}
                    style.update(_feature_style(layer.name, feat))
                    ET.SubElement(g, "path", style)
                elif feat.type == GEOM_POINT:
                    anchor = _feature_anchor_point(pts)
                    if not anchor:
                        continue
                    cx, cy = anchor
                    if layer.name == "mountain_peak":
                        tri = f"M {cx:.2f},{cy - 3.4:.2f} L {cx - 3.0:.2f},{cy + 2.4:.2f} L {cx + 3.0:.2f},{cy + 2.4:.2f} Z"
                        ET.SubElement(g, "path", {"id": elem_id, "d": tri, **_feature_style(layer.name, feat)})
                    else:
                        ET.SubElement(g, "circle", {"id": elem_id, "cx": f"{cx:.2f}", "cy": f"{cy:.2f}", "r": "2.2", **_feature_style(layer.name, feat)})
                    label = _feature_label_text(feat)
                    if label:
                        kind = _kind(feat)
                        fsize = "10"
                        dx = 4.0
                        dy = -2.0
                        if layer.name in ("place", "place_labels"):
                            if kind == "city":
                                fsize = "12"
                            elif kind == "town":
                                fsize = "11"
                        elif layer.name == "mountain_peak":
                            fsize = "8"
                            dx = 4.0
                            dy = -4.0
                        elif layer.name in ("water_name", "water_polygons_labels"):
                            fsize = "9"
                        t = ET.SubElement(
                            g,
                            "text",
                            {
                                "id": f"{elem_id}_label",
                                "x": f"{cx + dx:.2f}",
                                "y": f"{cy + dy:.2f}",
                                "font-size": fsize,
                                "font-family": "Segoe UI, sans-serif",
                                "fill": "#111",
                                "stroke": "#fff",
                                "stroke-width": "1.2",
                                "paint-order": "stroke fill",
                                "stroke-linejoin": "round",
                            },
                        )
                        t.text = label
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


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
