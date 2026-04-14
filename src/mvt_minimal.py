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
import struct
import xml.etree.ElementTree as ET

import map_style as MAP_STYLE


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


def _feature_anchor_point(points: List[List[Tuple[float, float]]]) -> Optional[Tuple[float, float]]:
    if not points or not points[0]:
        return None
    ring = points[0]
    sx = sum(p[0] for p in ring)
    sy = sum(p[1] for p in ring)
    n = max(1, len(ring))
    return sx / n, sy / n


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
    bbox_override: Optional[Tuple[float, float, float, float]] = None,
) -> Path:
    root = build_debug_svg_multi(tile_specs, width=width, height=height, include_layers=include_layers, bbox_override=bbox_override)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
    return out_path


def build_debug_svg_multi(
    tile_specs: Sequence[Tuple[bytes, int, int, int]],
    *,
    width: int = 1024,
    height: int = 1024,
    include_layers: Optional[Iterable[str]] = None,
    bbox_override: Optional[Tuple[float, float, float, float]] = None,
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
    scene = ET.SubElement(root, "g", {"clip-path": "url(#map-clip)"})
    ET.SubElement(scene, "rect", {"x": "0", "y": "0", "width": str(width), "height": str(height), "fill": "#eee6d1"})
    allowed = {str(v) for v in include_layers} if include_layers else None
    layer_groups: Dict[str, ET.Element] = {}
    feature_id_counters: Dict[str, int] = {}
    for raw_tile, tz, tx, ty in tile_specs:
        tile = decode_tile(raw_tile)
        for layer in sorted(tile.layers, key=lambda lyr: (MAP_STYLE.layer_order(lyr.name), lyr.name)):
            if allowed is not None and layer.name not in allowed:
                continue
            g = layer_groups.get(layer.name)
            if g is None:
                g = ET.SubElement(scene, "g", {"id": layer.name})
                layer_groups[layer.name] = g
            for feat in layer.features:
                if allowed is None and not MAP_STYLE.include_feature(layer.name, feat, z):
                    continue
                elem_id = MAP_STYLE.feature_id(layer.name, feat, feature_id_counters)
                pts = feature_to_viewbox_points(feat, layer, tz, tx, ty, bbox, width, height)
                path_d = feature_to_svg_path(feat, layer, tz, tx, ty, bbox, width, height)
                if feat.type in (GEOM_POLYGON, GEOM_LINESTRING):
                    if not path_d:
                        continue
                    style = {"d": path_d, "id": elem_id}
                    style.update(MAP_STYLE.feature_style(layer.name, feat, feat.type))
                    ET.SubElement(g, "path", style)
                elif feat.type == GEOM_POINT:
                    anchor = _feature_anchor_point(pts)
                    if not anchor:
                        continue
                    cx, cy = anchor
                    if layer.name == "mountain_peak":
                        tri = f"M {cx:.2f},{cy - 3.4:.2f} L {cx - 3.0:.2f},{cy + 2.4:.2f} L {cx + 3.0:.2f},{cy + 2.4:.2f} Z"
                        ET.SubElement(g, "path", {"id": elem_id, "d": tri, **MAP_STYLE.feature_style(layer.name, feat, feat.type)})
                    else:
                        ET.SubElement(g, "circle", {"id": elem_id, "cx": f"{cx:.2f}", "cy": f"{cy:.2f}", "r": "2.2", **MAP_STYLE.feature_style(layer.name, feat, feat.type)})
                    label = MAP_STYLE.feature_label_text(feat)
                    if label:
                        dx, dy = MAP_STYLE.label_offset(layer.name, feat)
                        text_style = MAP_STYLE.label_style(layer.name, feat)
                        t = ET.SubElement(
                            g,
                            "text",
                            {
                                "id": f"{elem_id}_label",
                                "x": f"{cx + dx:.2f}",
                                "y": f"{cy + dy:.2f}",
                                **text_style,
                            },
                        )
                        t.text = label
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
