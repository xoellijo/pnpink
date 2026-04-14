# -*- coding: utf-8 -*-
"""Tile-backed OSM/OpenFreeMap map source resolver."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import hashlib
import json
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET

import log as LOG
import net as NET
import mvt_minimal as MVT

_l = LOG


BBox = Tuple[float, float, float, float]
GeocodeResult = Tuple[float, float, float, float, float, float]


@dataclass
class OSMMapSource:
    assets_dir: Path

    OSM_TILE_TEMPLATE = "https://vector.openstreetmap.org/shortbread_v1/{z}/{x}/{y}.mvt"
    OFM_TILE_TEMPLATE = "https://tiles.openfreemap.org/planet/20260401_001001_pt/{z}/{x}/{y}.pbf"
    PROVIDER_MAX_ZOOM = {"osm": 14, "ofm": 14}
    NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
    OUTPUT_WIDTH_PX = 1400
    MIN_OUTPUT_HEIGHT_PX = 200

    def _maptiles_dir(self) -> Path:
        return self.assets_dir / "maptiles"

    def _tile_ext_for_provider(self, provider: str) -> str:
        return ".pbf" if str(provider or "").strip().lower() == "ofm" else ".mvt"

    def _tile_cache_file(self, provider: str, z: int, x: int, y: int) -> Path:
        provider_l = str(provider or "").strip().lower()
        ext = self._tile_ext_for_provider(provider_l)
        return self._maptiles_dir() / f"{provider_l}_{int(z)}_{int(x)}_{int(y)}{ext}"

    def _fetch_tile_bytes(self, provider: str, z: int, x: int, y: int, template: str) -> bytes:
        cfile = self._tile_cache_file(provider, z, x, y)
        try:
            if cfile.is_file():
                raw = cfile.read_bytes()
                _l.i(f"[sources] {provider} tile cache hit z={z} x={x} y={y} file={cfile.name}")
                return raw
        except Exception:
            pass

        url = template.format(z=z, x=x, y=y)
        raw, _headers, _status = NET.fetch_bytes(url, timeout=30, retries=4, log_prefix=f"[sources] {provider} tile")
        try:
            cfile.parent.mkdir(parents=True, exist_ok=True)
            cfile.write_bytes(raw)
            _l.i(f"[sources] {provider} tile cache miss z={z} x={x} y={y} -> {cfile.name}")
        except Exception as ex:
            _l.w(f"[sources] {provider} tile cache write failed z={z} x={x} y={y}: {ex}")
        return raw

    @classmethod
    def parse_bbox_expr(cls, expr: str) -> Optional[Tuple[str, float, float, float, float]]:
        s = str(expr or "").strip()
        m = re.match(r"^(osm|ofm)://\[\s*([^\]]+)\s*\]\s*$", s, re.I)
        if not m:
            return None
        provider = str(m.group(1) or "").strip().lower()
        nums = [float(x) for x in re.split(r"[\s,;]+", str(m.group(2) or "").strip()) if x.strip()]
        if len(nums) != 4:
            return None
        lat1, lon1, lat2, lon2 = nums
        if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
            return None
        if not (-85.05112878 <= lat1 <= 85.05112878 and -85.05112878 <= lat2 <= 85.05112878):
            return None
        west = min(lon1, lon2)
        east = max(lon1, lon2)
        south = min(lat1, lat2)
        north = max(lat1, lat2)
        return provider, west, south, east, north

    @classmethod
    def parse_named_expr(cls, expr: str) -> Optional[Tuple[str, str, Optional[int]]]:
        s = str(expr or "").strip()
        m = re.match(r"^(osm|ofm)://\s*(.+?)\s*$", s, re.I)
        if not m:
            return None
        provider = str(m.group(1) or "").strip().lower()
        body = str(m.group(2) or "").strip()
        if not body or body.startswith("["):
            return None
        forced_zoom = None
        mz = re.match(r"^(.*?)/z(\d+)\s*$", body, re.I)
        if mz:
            body = str(mz.group(1) or "").strip()
            forced_zoom = max(0, min(cls.PROVIDER_MAX_ZOOM.get(provider, 14), int(mz.group(2))))
        if not body or body.lower().startswith("#map="):
            return None
        return provider, body, forced_zoom

    def _geocode_cache_file(self, provider: str, query: str) -> Path:
        key = hashlib.sha256(f"{provider}|{query}".encode("utf-8")).hexdigest()
        return self._maptiles_dir() / f"nominatim_{provider}_{key}.json"

    def geocode_place(self, provider: str, query: str) -> Optional[GeocodeResult]:
        cache = self._geocode_cache_file(provider, query)
        data = None
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if data is None:
            url = f"{self.NOMINATIM_SEARCH_URL}?" + urllib.parse.urlencode({
                "q": query,
                "format": "jsonv2",
                "limit": "1",
            })
            try:
                data = NET.fetch_json(
                    url,
                    timeout=30,
                    retries=4,
                    log_prefix="[sources] nominatim",
                    headers={"User-Agent": "PnPInk OSM/1.0 (+https://github.com/pnpink)"},
                )
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            except Exception as ex:
                _l.w(f"[sources] nominatim geocode failed '{query}': {ex}")
                return None
        rows = list(data or [])
        if not rows:
            return None
        row = rows[0] or {}
        try:
            south, north, west, east = [float(v) for v in (row.get("boundingbox") or [])]
            lat = float(row.get("lat"))
            lon = float(row.get("lon"))
            return west, south, east, north, lon, lat
        except Exception:
            return None

    @staticmethod
    def lonlat_to_tile(lon: float, lat: float, z: int) -> Tuple[int, int]:
        n = 2 ** int(z)
        xf = (float(lon) + 180.0) / 360.0 * n
        lat_rad = math.radians(float(lat))
        yf = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) * 0.5 * n
        x = int(min(max(math.floor(xf), 0), n - 1))
        y = int(min(max(math.floor(yf), 0), n - 1))
        return x, y

    @classmethod
    def tile_span_for_bbox(cls, west: float, south: float, east: float, north: float, z: int) -> Tuple[int, int, int, int]:
        x0, y0 = cls.lonlat_to_tile(west, north, z)
        x1, y1 = cls.lonlat_to_tile(east, south, z)
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    @classmethod
    def choose_zoom_for_bbox(cls, provider: str, west: float, south: float, east: float, north: float, *, max_tiles: int = 4) -> int:
        zmax = int(cls.PROVIDER_MAX_ZOOM.get(provider, 14))
        for z in range(zmax, -1, -1):
            xmin, ymin, xmax, ymax = cls.tile_span_for_bbox(west, south, east, north, z)
            nx = xmax - xmin + 1
            ny = ymax - ymin + 1
            if nx <= 2 and ny <= 2 and (nx * ny) <= max_tiles:
                return z
        return 0

    @classmethod
    def tile_template_for_provider(cls, provider: str) -> str:
        if provider == "ofm":
            return cls.OFM_TILE_TEMPLATE
        return cls.OSM_TILE_TEMPLATE

    def fetch_vector_tiles_at_zoom(self, provider: str, bbox: BBox, z: int) -> Tuple[int, List[Tuple[bytes, int, int, int]]]:
        west, south, east, north = bbox
        z = max(0, min(self.PROVIDER_MAX_ZOOM.get(provider, 14), int(z)))
        xmin, ymin, xmax, ymax = self.tile_span_for_bbox(west, south, east, north, z)
        template = self.tile_template_for_provider(provider)
        specs: List[Tuple[bytes, int, int, int]] = []
        for x in range(xmin, xmax + 1):
            for y in range(ymin, ymax + 1):
                raw = self._fetch_tile_bytes(provider, z, x, y, template)
                specs.append((raw, z, x, y))
        _l.i(f"[sources] {provider} bbox tiles z={z} count={len(specs)} span={xmin},{ymin}..{xmax},{ymax}")
        return z, specs

    def fetch_vector_tiles(self, provider: str, bbox: BBox) -> Tuple[int, List[Tuple[bytes, int, int, int]]]:
        z = self.choose_zoom_for_bbox(provider, *bbox, max_tiles=4)
        return self.fetch_vector_tiles_at_zoom(provider, bbox, z)

    @staticmethod
    def _bbox_from_expr_or_name(expr: str, geocoder: "OSMMapSource") -> Optional[Tuple[str, BBox, Optional[int]]]:
        parsed_bbox = geocoder.parse_bbox_expr(expr)
        if parsed_bbox:
            provider, west, south, east, north = parsed_bbox
            return provider, (west, south, east, north), None
        parsed_named = geocoder.parse_named_expr(expr)
        if not parsed_named:
            return None
        provider, query, forced_zoom = parsed_named
        geo = geocoder.geocode_place(provider, query)
        if not geo:
            return None
        west, south, east, north, _lon, _lat = geo
        return provider, (west, south, east, north), forced_zoom

    @staticmethod
    def _output_size_for_bbox(bbox: BBox, width_px: int) -> Tuple[int, int]:
        west, south, east, north = bbox
        lat_c = (south + north) * 0.5
        width_geo = max(1e-9, (east - west) * max(0.01, math.cos(math.radians(lat_c))))
        height_geo = max(1e-9, north - south)
        aspect = max(0.05, min(20.0, width_geo / height_geo))
        height_px = max(OSMMapSource.MIN_OUTPUT_HEIGHT_PX, int(round(width_px / aspect)))
        return width_px, height_px

    def render_svg_text(self, expr: str) -> Optional[str]:
        resolved = self._bbox_from_expr_or_name(expr, self)
        if not resolved:
            return None
        provider, bbox, forced_zoom = resolved
        if forced_zoom is None:
            z, tile_specs = self.fetch_vector_tiles(provider, bbox)
        else:
            z, tile_specs = self.fetch_vector_tiles_at_zoom(provider, bbox, forced_zoom)
        width_px, height_px = self._output_size_for_bbox(bbox, self.OUTPUT_WIDTH_PX)
        root = MVT.build_debug_svg_multi(
            tile_specs,
            width=width_px,
            height=height_px,
            include_layers=None,
            bbox_override=bbox,
        )
        root.set("data-osm-provider", provider)
        root.set("data-osm-zoom", str(z))
        if forced_zoom is not None:
            root.set("data-osm-forced-zoom", str(forced_zoom))
        return ET.tostring(root, encoding="unicode")

    def resolve(self, expr: str) -> Optional[List[str]]:
        # Compatibility shim for older call-sites. Tile-backed maps are now rendered inline.
        if self.parse_bbox_expr(expr) or self.parse_named_expr(expr):
            return []
        return None
