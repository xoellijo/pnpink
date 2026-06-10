# -*- coding: utf-8 -*-
"""Tile-backed OSM/OpenFreeMap map source resolver."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
import hashlib
import json
import math
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

import log as LOG
import net as NET
import mvt_minimal as MVT
import map_style as MAP_STYLE
import prefs

_l = LOG


BBox = Tuple[float, float, float, float]
GeocodeResult = Tuple[float, float, float, float, float, float]
MapResolveResult = Tuple[str, BBox, Optional[int], Optional[int]]


@dataclass
class OSMMapSource:
    assets_dir: Path
    _tile_stats: dict = field(default_factory=dict, init=False, repr=False)
    _map_uses: dict = field(default_factory=dict, init=False, repr=False)

    OSM_TILE_TEMPLATE = "https://vector.openstreetmap.org/shortbread_v1/{z}/{x}/{y}.mvt"
    OFM_TILE_TEMPLATE = "https://tiles.openfreemap.org/planet/20260401_001001_pt/{z}/{x}/{y}.pbf"
    DEFAULT_PROVIDER_MAX_ZOOM = {"osm": 14, "ofm": 14}
    DEFAULT_PROVIDER_MAX_TILE_GRID = {"osm": 4, "ofm": 4}
    NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
    OUTPUT_WIDTH_PX = 1400
    MIN_OUTPUT_HEIGHT_PX = 200

    @staticmethod
    def _split_top(text: str) -> list[str]:
        out = []
        token = []
        depth = 0
        quote = ""
        for ch in str(text or ""):
            if quote:
                token.append(ch)
                if ch == quote:
                    quote = ""
                continue
            if ch in ("'", '"'):
                quote = ch
                token.append(ch)
                continue
            if ch == "[":
                depth += 1
                token.append(ch)
                continue
            if ch == "]":
                depth = max(0, depth - 1)
                token.append(ch)
                continue
            if ch.isspace() and depth == 0:
                if token:
                    out.append("".join(token))
                    token = []
                continue
            token.append(ch)
        if token:
            out.append("".join(token))
        return out

    @classmethod
    def _split_map_source_args(cls, expr: str) -> Tuple[str, dict]:
        tokens = cls._split_top(str(expr or "").strip())
        if not tokens:
            return "", {}
        url = tokens[0]
        args = {}
        for tok in tokens[1:]:
            if "=" not in tok:
                continue
            key, val = tok.split("=", 1)
            key = key.strip().lower()
            val = val.strip()
            if key in {"view", "v"}:
                args["view"] = val
            else:
                args[key] = val
        return url, args

    def _maptiles_dir(self) -> Path:
        return self.assets_dir / "maptiles"

    def _tile_ext_for_provider(self, provider: str) -> str:
        return ".pbf" if str(provider or "").strip().lower() == "ofm" else ".mvt"

    def _tile_cache_file(self, provider: str, z: int, x: int, y: int) -> Path:
        provider_l = str(provider or "").strip().lower()
        ext = self._tile_ext_for_provider(provider_l)
        return self._maptiles_dir() / f"{provider_l}_{int(z)}_{int(x)}_{int(y)}{ext}"

    def _provider_summary_key(self, provider: str) -> str:
        return "openfreemap" if str(provider or "").strip().lower() == "ofm" else "openstreetmap"

    @classmethod
    def _provider_max_zoom(cls, provider: str) -> int:
        provider_l = str(provider or "").strip().lower()
        return prefs.get_map_max_zoom(provider_l, cls.DEFAULT_PROVIDER_MAX_ZOOM.get(provider_l, 14))

    @classmethod
    def _provider_max_tile_grid(cls, provider: str) -> int:
        provider_l = str(provider or "").strip().lower()
        return prefs.get_map_max_tile_grid(provider_l, cls.DEFAULT_PROVIDER_MAX_TILE_GRID.get(provider_l, 4))

    def _inc_tile_stat(self, provider: str, name: str, step: int = 1) -> None:
        key = self._provider_summary_key(provider)
        stats = self._tile_stats.setdefault(key, {"downloaded": 0, "cached": 0, "failed": 0})
        stats[name] = int(stats.get(name) or 0) + int(step or 0)

    def _emit_source_summary(self, provider: str) -> None:
        key = self._provider_summary_key(provider)
        vals = self._tile_stats.get(key) or {}
        _l.i(
            "[sources.progress] source_summary provider=%s downloaded=%d cached=%d failed=%d uses=%d",
            key,
            int(vals.get("downloaded") or 0),
            int(vals.get("cached") or 0),
            int(vals.get("failed") or 0),
            int(self._map_uses.get(key) or 0),
        )

    def _fetch_tile_bytes(self, provider: str, z: int, x: int, y: int, template: str) -> bytes:
        cfile = self._tile_cache_file(provider, z, x, y)
        try:
            if cfile.is_file():
                raw = cfile.read_bytes()
                self._inc_tile_stat(provider, "cached")
                _l.i(f"[sources] {provider} tile cache hit z={z} x={x} y={y} file={cfile.name}")
                return raw
        except Exception:
            pass

        url = template.format(z=z, x=x, y=y)
        try:
            raw, _headers, _status = NET.fetch_bytes(url, timeout=30, retries=4, log_prefix=f"[sources] {provider} tile")
            self._inc_tile_stat(provider, "downloaded")
        except Exception:
            self._inc_tile_stat(provider, "failed")
            raise
        try:
            cfile.parent.mkdir(parents=True, exist_ok=True)
            cfile.write_bytes(raw)
            _l.i(f"[sources] {provider} tile cache miss z={z} x={x} y={y} -> {cfile.name}")
        except Exception as ex:
            _l.w(f"[sources] {provider} tile cache write failed z={z} x={x} y={y}: {ex}")
        return raw

    @classmethod
    def _strip_map_options(cls, body: str, provider: str) -> Tuple[str, Optional[int], Optional[int]]:
        text = str(body or "").strip()
        forced_zoom = None
        max_tile_grid = None
        while True:
            m = re.search(r"/([zt])(\d+)\s*$", text, re.I)
            if not m:
                break
            key = str(m.group(1) or "").lower()
            val = int(m.group(2))
            text = text[:m.start()].rstrip()
            if key == "z":
                forced_zoom = max(0, min(cls._provider_max_zoom(provider), val))
            elif key == "t":
                max_tile_grid = max(1, min(32, val))
        return text.strip(), forced_zoom, max_tile_grid

    @staticmethod
    def _parse_bbox_numbers(text: str) -> Optional[List[float]]:
        raw = str(text or "").strip()
        space_tokens = [p for p in re.split(r"\s+", raw) if p]
        if len(space_tokens) == 4:
            try:
                return [float(p.replace(",", ".")) for p in space_tokens]
            except ValueError:
                pass
        try:
            return [float(p) for p in re.split(r"[\s,;]+", raw) if p.strip()]
        except ValueError:
            return None

    @classmethod
    def parse_bbox_expr(cls, expr: str) -> Optional[Tuple[str, float, float, float, float, Optional[int], Optional[int]]]:
        s = str(expr or "").strip()
        m = re.match(r"^(osm|ofm)://(?P<body>\[\s*[^\]]+\s*\](?:/[zt]\d+)*)\s*$", s, re.I)
        if not m:
            return None
        provider = str(m.group(1) or "").strip().lower()
        body, forced_zoom, max_tile_grid = cls._strip_map_options(str(m.group("body") or ""), provider)
        mb = re.match(r"^\[\s*([^\]]+)\s*\]$", body)
        if not mb:
            return None
        nums = cls._parse_bbox_numbers(str(mb.group(1) or ""))
        if not nums or len(nums) != 4:
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
        return provider, west, south, east, north, forced_zoom, max_tile_grid

    @classmethod
    def parse_named_expr(cls, expr: str) -> Optional[Tuple[str, str, Optional[int], Optional[int]]]:
        s = str(expr or "").strip()
        m = re.match(r"^(osm|ofm)://\s*(.+?)\s*$", s, re.I)
        if not m:
            return None
        provider = str(m.group(1) or "").strip().lower()
        body = str(m.group(2) or "").strip()
        if not body or body.startswith("["):
            return None
        body, forced_zoom, max_tile_grid = cls._strip_map_options(body, provider)
        if not body or body.lower().startswith("#map="):
            return None
        return provider, body, forced_zoom, max_tile_grid

    def _geocode_cache_file(self, provider: str, query: str, feature_type: str = "") -> Path:
        seed = f"v2|{str(query or '').strip().lower()}|{str(feature_type or '').strip().lower()}"
        key = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return self._maptiles_dir() / f"nominatim_{key}.json"

    @staticmethod
    def _norm_place_text(value: object) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").lower())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()

    @classmethod
    def _choose_geocode_row(cls, query: str, rows: list) -> Optional[dict]:
        good = [r for r in rows if isinstance(r, dict) and r.get("boundingbox") and r.get("lat") and r.get("lon")]
        if not good:
            return None
        return good[0]

    @classmethod
    def _filter_geocode_rows(cls, query: str, rows: list) -> tuple[str, list]:
        parts = [p.strip() for p in str(query or "").split(",") if p.strip()]
        if len(parts) <= 1:
            return str(query or "").strip(), rows
        base = parts[0]
        filters = [cls._norm_place_text(p) for p in parts[1:] if cls._norm_place_text(p)]
        if not filters:
            return base, rows
        filtered = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            haystack = cls._norm_place_text(" ".join(str(row.get(k) or "") for k in ("display_name", "name", "category", "type", "addresstype")))
            if all(f in haystack for f in filters):
                filtered.append(row)
        return base, filtered

    def _load_geocode_rows(self, provider: str, query: str, feature_type: str = "") -> Optional[list]:
        cache = self._geocode_cache_file(provider, query, feature_type)
        data = None
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if data is None:
            params = {
                "q": query,
                "format": "jsonv2",
                "limit": "10",
            }
            if feature_type:
                params["featureType"] = feature_type
            url = f"{self.NOMINATIM_SEARCH_URL}?" + urllib.parse.urlencode(params)
            try:
                data = NET.fetch_json(
                    url,
                    timeout=30,
                    retries=4,
                    log_prefix="[sources] nominatim",
                )
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            except Exception as ex:
                _l.w(f"[sources] nominatim geocode failed '{query}': {ex}")
                return None
        return list(data or [])

    def geocode_place(self, provider: str, query: str) -> Optional[GeocodeResult]:
        search_query = str(query or "").split(",", 1)[0].strip() or str(query or "").strip()
        rows_raw = self._load_geocode_rows(provider, search_query) or []
        _base_query, rows = self._filter_geocode_rows(query, rows_raw)
        if not rows:
            _l.w(f"[sources] nominatim no candidates query='{query}' search='{search_query}'")
            return None
        try:
            preview = []
            for r in rows[:5]:
                if not isinstance(r, dict):
                    continue
                preview.append(
                    f"{r.get('display_name') or ''} "
                    f"type={r.get('category') or ''}/{r.get('type') or ''} "
                    f"rank={r.get('place_rank') or ''} imp={r.get('importance') or ''}"
                )
            if preview:
                _l.i(f"[sources] nominatim candidates query='{query}' search='{search_query}' filtered={len(rows)}/{len(rows_raw)} -> " + " | ".join(preview))
        except Exception:
            pass
        row = self._choose_geocode_row(query, rows) or {}
        if not row:
            _l.w(f"[sources] nominatim no valid bbox query='{query}' candidates={len(rows)}")
            return None
        try:
            south, north, west, east = [float(v) for v in (row.get("boundingbox") or [])]
            lat = float(row.get("lat"))
            lon = float(row.get("lon"))
            _l.i(
                f"[sources] nominatim selected query='{query}' search='{search_query}' "
                f"display='{row.get('display_name') or ''}' "
                f"bbox={west:.6f},{south:.6f},{east:.6f},{north:.6f}"
            )
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
    def choose_zoom_for_bbox(
        cls,
        provider: str,
        west: float,
        south: float,
        east: float,
        north: float,
        *,
        max_tile_grid: Optional[int] = None,
    ) -> int:
        if max_tile_grid is None:
            max_tile_grid = cls._provider_max_tile_grid(provider)
        zmax = cls._provider_max_zoom(provider)
        for z in range(zmax, -1, -1):
            xmin, ymin, xmax, ymax = cls.tile_span_for_bbox(west, south, east, north, z)
            nx = xmax - xmin + 1
            ny = ymax - ymin + 1
            if nx <= max_tile_grid and ny <= max_tile_grid:
                return z
        return 0

    @classmethod
    def tile_template_for_provider(cls, provider: str) -> str:
        if provider == "ofm":
            return cls.OFM_TILE_TEMPLATE
        return cls.OSM_TILE_TEMPLATE

    def fetch_vector_tiles_at_zoom(
        self,
        provider: str,
        bbox: BBox,
        z: int,
        *,
        max_tile_grid: Optional[int] = None,
    ) -> Tuple[int, List[Tuple[bytes, int, int, int]]]:
        west, south, east, north = bbox
        z = max(0, min(self._provider_max_zoom(provider), int(z)))
        xmin, ymin, xmax, ymax = self.tile_span_for_bbox(west, south, east, north, z)
        nx = xmax - xmin + 1
        ny = ymax - ymin + 1
        count = (xmax - xmin + 1) * (ymax - ymin + 1)
        _l.i(
            "[sources] map tiles provider=%s z=%d count=%d grid=%dx%d max_tile_grid=%s span=%d,%d..%d,%d bbox=%.6f,%.6f,%.6f,%.6f",
            provider, z, count, nx, ny, str(max_tile_grid or ""), xmin, ymin, xmax, ymax, west, south, east, north,
        )
        if max_tile_grid is not None and (nx > int(max_tile_grid) or ny > int(max_tile_grid)):
            _l.w(f"[sources] map forced zoom exceeds /t{int(max_tile_grid)}: grid={nx}x{ny} z={z}")
        template = self.tile_template_for_provider(provider)
        specs: List[Tuple[bytes, int, int, int]] = []
        for x in range(xmin, xmax + 1):
            for y in range(ymin, ymax + 1):
                raw = self._fetch_tile_bytes(provider, z, x, y, template)
                specs.append((raw, z, x, y))
        return z, specs

    def fetch_vector_tiles(
        self,
        provider: str,
        bbox: BBox,
        *,
        max_tile_grid: Optional[int] = None,
    ) -> Tuple[int, List[Tuple[bytes, int, int, int]]]:
        grid = int(max_tile_grid) if max_tile_grid is not None else self._provider_max_tile_grid(provider)
        z = self.choose_zoom_for_bbox(provider, *bbox, max_tile_grid=grid)
        return self.fetch_vector_tiles_at_zoom(provider, bbox, z, max_tile_grid=grid)

    @staticmethod
    def _bbox_from_expr_or_name(expr: str, geocoder: "OSMMapSource") -> Optional[MapResolveResult]:
        parsed_bbox = geocoder.parse_bbox_expr(expr)
        if parsed_bbox:
            provider, west, south, east, north, forced_zoom, max_tile_grid = parsed_bbox
            return provider, (west, south, east, north), forced_zoom, max_tile_grid
        parsed_named = geocoder.parse_named_expr(expr)
        if not parsed_named:
            return None
        provider, query, forced_zoom, max_tile_grid = parsed_named
        geo = geocoder.geocode_place(provider, query)
        if not geo:
            return None
        west, south, east, north, _lon, _lat = geo
        return provider, (west, south, east, north), forced_zoom, max_tile_grid

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
        expr_url, args = self._split_map_source_args(expr)
        resolved = self._bbox_from_expr_or_name(expr_url, self)
        if not resolved:
            return None
        provider, bbox, forced_zoom, max_tile_grid = resolved
        key = self._provider_summary_key(provider)
        self._map_uses[key] = int(self._map_uses.get(key) or 0) + 1
        try:
            if forced_zoom is None:
                z, tile_specs = self.fetch_vector_tiles(provider, bbox, max_tile_grid=max_tile_grid)
            else:
                z, tile_specs = self.fetch_vector_tiles_at_zoom(provider, bbox, forced_zoom, max_tile_grid=max_tile_grid)
        except Exception:
            self._emit_source_summary(provider)
            raise
        self._emit_source_summary(provider)
        width_px, height_px = self._output_size_for_bbox(bbox, self.OUTPUT_WIDTH_PX)
        view_expr = str(args.get("view") or "default").strip()
        view_filter = MAP_STYLE.resolve_view_filter(view_expr)
        _l.i(f"[sources] map view='{view_expr}'")
        root = MVT.build_debug_svg_multi(
            tile_specs,
            width=width_px,
            height=height_px,
            include_layers=None,
            view_filter=view_filter,
            bbox_override=bbox,
        )
        root.set("data-osm-provider", provider)
        root.set("data-osm-zoom", str(z))
        root.set("data-osm-max-tile-grid", str(max_tile_grid or self._provider_max_tile_grid(provider)))
        root.set("data-id-prefix", "")
        if forced_zoom is not None:
            root.set("data-osm-forced-zoom", str(forced_zoom))
        root.set("data-osm-view", view_expr)
        return ET.tostring(root, encoding="unicode")

    def resolve(self, expr: str) -> Optional[List[str]]:
        # Compatibility shim for older call-sites. Tile-backed maps are now rendered inline.
        if self.parse_bbox_expr(expr) or self.parse_named_expr(expr):
            return []
        return None
