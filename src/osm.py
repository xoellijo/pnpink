# -*- coding: utf-8 -*-
"""
osm.py - Build layered SVG maps from OpenStreetMap URLs using Overpass.
"""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import os
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional, Tuple
import hashlib
import itertools
import json
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET

import log as LOG
_l = LOG
import net as NET


def _import_pyshp():
    try:
        import shapefile  # type: ignore
        return shapefile
    except Exception:
        pass
    here = Path(__file__).resolve().parent
    candidates = [
        here / "third_party" / "pyshp" / "shapefile.py",
        here / "_vendor" / "pyshp" / "shapefile.py",
        here / "vendor" / "pyshp" / "shapefile.py",
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location("shapefile", str(path))
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules.setdefault("shapefile", mod)
            spec.loader.exec_module(mod)
            _l.i(f"[sources] osm using vendored pyshp '{path.name}'")
            return mod
        except Exception:
            continue
    raise ModuleNotFoundError("No module named 'shapefile'")


def _vendored_pyshp_path() -> Optional[Path]:
    here = Path(__file__).resolve().parent
    candidates = [
        here / "third_party" / "pyshp" / "shapefile.py",
        here / "_vendor" / "pyshp" / "shapefile.py",
        here / "vendor" / "pyshp" / "shapefile.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


@dataclass
class OSMMapSource:
    assets_dir: Path

    OVERPASS_URLS = (
        "https://overpass-api.de/api/interpreter",
    )
    MIN_PEAK_ELEVATION_M = 1000.0
    MAX_RENDERED_PEAKS = 20
    COAST_JOIN_TOLERANCE_PX = 5.0
    COAST_MIN_CHAIN_LENGTH_PX = 18.0
    COAST_SIMPLIFY_FINAL_PX = 0.75
    COAST_SECOND_PASS_TOLERANCE_PX = 10.0
    COAST_SECOND_PASS_MIN_LENGTH_PX = 120.0
    COAST_BORDER_CLOSE_THRESHOLD_PX = 10.0
    LAND_SIMPLIFY_PX = 1.2
    LAND_SIMPLIFY_STRONG_PX = 3.0
    LAND_MIN_AREA_PX2 = 24.0

    @classmethod
    def land_simplify_for_zoom(cls, zoom: int) -> float:
        z = int(max(0, min(19, int(zoom))))
        if z <= 1:
            return 18.0
        if z == 2:
            return 14.0
        if z == 3:
            return 11.0
        if z == 4:
            return 8.0
        if z == 5:
            return 6.0
        if z == 6:
            return 4.5
        if z == 7:
            return 3.5
        if z <= 9:
            return cls.LAND_SIMPLIFY_STRONG_PX
        if z <= 11:
            return 0.0
        return cls.LAND_SIMPLIFY_PX

    @classmethod
    def places_selector_for_zoom(cls, bbox: str, zoom: int) -> str:
        z = int(max(0, min(19, int(zoom))))
        if z <= 6:
            return (
                f'node["place"="city"]{bbox}(if:number(t["population"]) >= 100000);'
            )
        if z == 7:
            return (
                f'node["place"="city"]{bbox}(if:number(t["population"]) >= 50000);'
            )
        if z <= 9:
            return (
                f'node["place"="city"]{bbox};'
                f'node["place"="town"]{bbox}(if:number(t["population"]) >= 25000);'
            )
        return f'node["place"~"city|town"]{bbox};'

    @classmethod
    def places_query_for_zoom(cls, bbox: str, zoom: int) -> str:
        return "[out:json][timeout:25];(" + cls.places_selector_for_zoom(bbox, zoom) + ");out geom;"

    @classmethod
    def water_selector_for_zoom(cls, bbox: str, zoom: int) -> str:
        z = int(max(0, min(19, int(zoom))))
        polys = (
            f'way["natural"="water"]{bbox};'
            f'way["water"~"lake|reservoir|pond|basin|lagoon"]{bbox};'
            f'way["landuse"~"reservoir|basin"]{bbox};'
            f'way["waterway"="riverbank"]{bbox};'
        )
        if z <= 6:
            lines = f'way["waterway"="river"]{bbox};'
        elif z <= 9:
            lines = f'way["waterway"~"river|canal|drain"]{bbox};'
        else:
            lines = f'way["waterway"~"river|canal|stream|drain"]{bbox};'
        return f"{polys}{lines}"

    @classmethod
    def water_query_for_zoom(cls, bbox: str, zoom: int) -> str:
        return "[out:json][timeout:25];(" + cls.water_selector_for_zoom(bbox, zoom) + ");out geom;"

    @classmethod
    def combined_query_for_zoom(cls, bbox: str, zoom: int) -> str:
        return (
            "[out:json][timeout:25];("
            + cls.water_selector_for_zoom(bbox, zoom)
            + cls.places_selector_for_zoom(bbox, zoom)
            + ");out geom;"
        )

    @staticmethod
    def parse_size_spec(size: str) -> dict:
        s = str(size or "").strip().lower()
        if not s:
            s = "medium"
        aliases = {
            "s": "small",
            "m": "medium",
            "l": "large",
            "h": "xlarge",
            "high": "xlarge",
            "orig": "largest",
            "original": "largest",
            "full": "largest",
            "o": "largest",
        }
        s = aliases.get(s, s)
        m_box = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", s)
        if m_box:
            return {
                "kind": "min_box",
                "min_w": max(1, int(m_box.group(1))),
                "min_h": max(1, int(m_box.group(2))),
                "label": s,
            }
        if re.match(r"^\d+$", s):
            n = max(1, int(s))
            return {"kind": "min_side", "min_side": n, "label": s}
        if s in ("tiny", "small", "medium", "large", "xlarge", "largest"):
            return {"kind": "preset", "preset": s, "label": s}
        return {"kind": "preset", "preset": "medium", "label": "medium"}

    @classmethod
    def parse_expr(cls, expr: str) -> Optional[Tuple[int, float, float, str]]:
        s = (expr or "").strip()
        if not s.lower().startswith("osm://"):
            return None
        body = s[len("osm://"):].strip()
        if not body:
            return None
        size = "medium"
        if "/" in body:
            head, maybe_size = body.rsplit("/", 1)
            if cls.parse_size_spec(maybe_size).get("label") == maybe_size.strip().lower():
                body = head.strip()
                size = maybe_size.strip()
        m = re.match(r"^#map=(\d+)\s*/\s*([-+]?\d+(?:\.\d+)?)\s*/\s*([-+]?\d+(?:\.\d+)?)\s*$", body, re.I)
        if not m:
            return None
        zoom = max(0, min(19, int(m.group(1))))
        lat = max(-85.05112878, min(85.05112878, float(m.group(2))))
        lon = ((float(m.group(3)) + 180.0) % 360.0) - 180.0
        return zoom, lat, lon, size

    @classmethod
    def viewport_px(cls, size: str) -> Tuple[int, int]:
        spec = cls.parse_size_spec(size)
        if spec.get("kind") == "min_box":
            return int(spec.get("min_w") or 1024), int(spec.get("min_h") or 768)
        if spec.get("kind") == "min_side":
            n = int(spec.get("min_side") or 1024)
            return n, n
        preset = str(spec.get("preset") or "medium")
        mp = {
            "tiny": (320, 240),
            "small": (640, 480),
            "medium": (1024, 768),
            "large": (1400, 1050),
            "xlarge": (1920, 1440),
            "largest": (2560, 1920),
        }
        return mp.get(preset, (1024, 768))

    @staticmethod
    def latlon_to_world_px(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
        scale = 256.0 * (2.0 ** float(zoom))
        x = (float(lon) + 180.0) / 360.0 * scale
        lat_rad = math.radians(float(lat))
        y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) * 0.5 * scale
        return x, y

    @staticmethod
    def world_px_to_latlon(x: float, y: float, zoom: int) -> Tuple[float, float]:
        scale = 256.0 * (2.0 ** float(zoom))
        lon = float(x) / scale * 360.0 - 180.0
        n = math.pi - 2.0 * math.pi * float(y) / scale
        lat = math.degrees(math.atan(math.sinh(n)))
        return lat, lon

    @classmethod
    def bbox_from_center(cls, zoom: int, lat: float, lon: float, size: str) -> Tuple[float, float, float, float]:
        w_px, h_px = cls.viewport_px(size)
        cx, cy = cls.latlon_to_world_px(lat, lon, zoom)
        x0 = cx - float(w_px) * 0.5
        y0 = cy - float(h_px) * 0.5
        x1 = cx + float(w_px) * 0.5
        y1 = cy + float(h_px) * 0.5
        lat_n, lon_w = cls.world_px_to_latlon(x0, y0, zoom)
        lat_s, lon_e = cls.world_px_to_latlon(x1, y1, zoom)
        west = max(-180.0, min(180.0, lon_w))
        east = max(-180.0, min(180.0, lon_e))
        south = max(-85.05112878, min(85.05112878, lat_s))
        north = max(-85.05112878, min(85.05112878, lat_n))
        return west, south, east, north

    def cache_svg_file(self, zoom: int, lat: float, lon: float, size: str) -> Path:
        ds = self._discover_land_dataset(zoom=zoom)
        ds_sig = "none"
        if ds and ds.exists():
            try:
                st = ds.stat()
                ds_sig = f"{ds.name}|{int(st.st_mtime)}|{int(st.st_size)}"
            except Exception:
                ds_sig = ds.name
        pyshp_sig = "none"
        vp = _vendored_pyshp_path()
        if vp and vp.exists():
            try:
                st = vp.stat()
                pyshp_sig = f"{vp.name}|{int(st.st_mtime)}|{int(st.st_size)}"
            except Exception:
                pyshp_sig = vp.name
        key = f"osm-overpass-v35|{zoom}|{lat:.6f}|{lon:.6f}|{size}|land={ds_sig}|pyshp={pyshp_sig}"
        k = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.assets_dir / f"osm_{k}.svg"

    def _discover_land_dataset(self, *, zoom: Optional[int] = None) -> Optional[Path]:
        env = str(os.environ.get("PNPINK_OSM_LAND_SHP") or "").strip()
        if env:
            p = Path(env)
            if p.is_file():
                return p
        roots = [
            self.assets_dir,
            self.assets_dir / "osm",
            self.assets_dir.parent,
            self.assets_dir.parent / "osm",
            self.assets_dir.parent / "osmdata",
            self.assets_dir.parent.parent if self.assets_dir.parent.parent != self.assets_dir.parent else self.assets_dir.parent,
        ]
        candidates: List[Path] = []
        seen: set[str] = set()
        for root in roots:
            try:
                if not root or not root.exists():
                    continue
                for pat in ("*land*.shp", "*land*.zip"):
                    for p in root.glob(pat):
                        key = str(p.resolve()).lower()
                        if key in seen or not p.is_file():
                            continue
                        seen.add(key)
                        candidates.append(p)
            except Exception:
                continue
        if not candidates:
            return None

        z = None if zoom is None else int(max(0, min(19, int(zoom))))

        def _score(p: Path) -> Tuple[int, int, int, str]:
            name = str(p.name or "").lower()
            is_simplified = "simplified" in name
            is_complete = "complete" in name
            is_split = "split" in name
            if z is not None and z <= 11:
                # Low/medium zoom: strongly prefer simplified global datasets.
                return (
                    0 if is_simplified else 1,
                    0 if is_complete else 1,
                    1 if is_split else 0,
                    name,
                )
            if z is not None and z > 11:
                # Higher zoom: prefer split detail when available.
                return (
                    0 if is_split else 1,
                    1 if is_simplified else 0,
                    0 if is_complete else 1,
                    name,
                )
            return (
                0 if is_simplified else 1,
                0 if is_complete else 1,
                1 if is_split else 0,
                name,
            )

        candidates.sort(key=_score)
        chosen = candidates[0]
        if len(candidates) > 1:
            _l.i(f"[sources] osm land dataset candidates={len(candidates)} zoom={z if z is not None else '-'} chosen='{chosen.name}'")
        return chosen

    def overpass_queries(self, west: float, south: float, east: float, north: float) -> List[Tuple[str, str]]:
        bbox = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
        zoom = self._query_zoom if getattr(self, "_query_zoom", None) is not None else 8
        queries: List[Tuple[str, str]] = []
        if not self._discover_land_dataset():
            queries.append(
                (
                    "coast",
                    "[out:json][timeout:25];"
                    "("
                    f'way["natural"="coastline"]{bbox};'
                    ");"
                    "out geom;",
                )
            )
        queries.extend([
            (
                "water",
                self.water_query_for_zoom(bbox, zoom),
            ),
            (
                "places",
                self.places_query_for_zoom(bbox, zoom),
            ),
        ])
        return queries

    def fetch_elements(self, query: str, *, label: str = "query") -> List[dict]:
        last_ex: Exception | None = None
        for base in self.OVERPASS_URLS:
            try:
                url = base + "?" + urllib.parse.urlencode({"data": query})
                data = NET.fetch_json(
                    url,
                    headers={"User-Agent": "PnPInk OSM/1.0 (+https://github.com/pnpink)"},
                    timeout=45,
                    retries=4,
                    log_prefix="[sources] osm",
                )
                return list((data or {}).get("elements") or [])
            except Exception as ex:
                last_ex = ex
                _l.w(f"[sources] osm overpass failed '{label}' '{base}': {ex}")
        if last_ex is not None:
            raise last_ex
        return []

    @staticmethod
    def _subdivide_bbox(west: float, south: float, east: float, north: float, nx: int, ny: int) -> List[Tuple[float, float, float, float]]:
        xs = [west + (east - west) * i / float(nx) for i in range(nx + 1)]
        ys = [south + (north - south) * j / float(ny) for j in range(ny + 1)]
        tiles: List[Tuple[float, float, float, float]] = []
        for ix, iy in itertools.product(range(nx), range(ny)):
            tiles.append((xs[ix], ys[iy], xs[ix + 1], ys[iy + 1]))
        return tiles

    def fetch_elements_tiled_combined(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        *,
        zoom: int,
    ) -> List[dict]:
        if zoom >= 8:
            nx = ny = 4
        elif zoom >= 6:
            nx = ny = 3
        else:
            nx = ny = 2
        merged: Dict[str, dict] = {}
        tiles = self._subdivide_bbox(west, south, east, north, nx, ny)
        _l.i(f"[sources] osm tiled combined grid={nx}x{ny}")

        def _one(idx_tile):
            idx, (tw, ts, te, tn) = idx_tile
            bbox = f"({ts:.6f},{tw:.6f},{tn:.6f},{te:.6f})"
            q = self.combined_query_for_zoom(bbox, zoom)
            try:
                part = self.fetch_elements(q, label=f"tile:{idx}/{len(tiles)}")
                return idx, part, None
            except Exception as ex:
                return idx, [], ex

        max_workers = min(len(tiles), 4)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="osm_tile") as exr:
            futs = [exr.submit(_one, item) for item in enumerate(tiles, start=1)]
            for fut in as_completed(futs):
                idx, part, err = fut.result()
                if err is not None:
                    _l.w(f"[sources] osm tile {idx}/{len(tiles)} skipped: {type(err).__name__}: {err}")
                    continue
                _l.i(f"[sources] osm tile {idx}/{len(tiles)} elements={len(part or [])}")
                for el in (part or []):
                    merged[self._element_key(el)] = el
        return list(merged.values())

    @staticmethod
    def _element_key(el: dict) -> str:
        return f"{str(el.get('type') or '')}:{str(el.get('id') or '')}"

    def fetch_all_elements(self, west: float, south: float, east: float, north: float) -> List[dict]:
        merged: Dict[str, dict] = {}
        old_zoom = getattr(self, "_query_zoom", None)
        try:
            zoom = int(getattr(self, "_query_zoom", 8) or 8)
            for label, query in self.overpass_queries(west, south, east, north):
                try:
                    part = self.fetch_elements(query, label=label)
                    _l.i(f"[sources] osm part '{label}' elements={len(part or [])}")
                    for el in (part or []):
                        merged[self._element_key(el)] = el
                except Exception as ex:
                    _l.w(f"[sources] osm part '{label}' failed: {type(ex).__name__}: {ex}")
                    if label in ("water", "places"):
                        tiled = self.fetch_elements_tiled_combined(west, south, east, north, zoom=zoom)
                        _l.i(f"[sources] osm tiled combined elements={len(tiled or [])}")
                        for el in (tiled or []):
                            merged[self._element_key(el)] = el
                        break
        finally:
            self._query_zoom = old_zoom
        return list(merged.values())

    @staticmethod
    def _mercator_m_from_latlon(lat: float, lon: float) -> Tuple[float, float]:
        origin_shift = 20037508.342789244
        mx = float(lon) * origin_shift / 180.0
        lat = max(-85.05112878, min(85.05112878, float(lat)))
        my = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) * origin_shift / math.pi
        return mx, my

    @classmethod
    def _dataset_crs(cls, path: Path) -> str:
        name = str(path.name or "").lower()
        if "3857" in name or "900913" in name:
            return "3857"
        return "4326"

    @classmethod
    def _bbox_intersects(cls, a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> bool:
        ax0, ay0, ax1, ay1 = a
        bx0, by0, bx1, by1 = b
        return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)

    @classmethod
    def _dataset_projector(
        cls,
        west: float,
        south: float,
        east: float,
        north: float,
        width: int,
        height: int,
        *,
        crs: str,
    ):
        if crs == "3857":
            minx, miny, maxx, maxy = cls._dataset_bbox(west, south, east, north, crs=crs)
            dx = max(1e-9, maxx - minx)
            dy = max(1e-9, maxy - miny)

            def _p(x: float, y: float) -> Tuple[float, float]:
                px = (float(x) - minx) / dx * float(width)
                py = (maxy - float(y)) / dy * float(height)
                return px, py

            return _p

        return cls._projector(west, south, east, north, width, height)

    @classmethod
    def _dataset_bbox(cls, west: float, south: float, east: float, north: float, *, crs: str) -> Tuple[float, float, float, float]:
        if crs == "3857":
            x0, y1 = cls._mercator_m_from_latlon(north, west)
            x1, y0 = cls._mercator_m_from_latlon(south, east)
            return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        return (west, south, east, north)

    def load_land_polygons(
        self, west: float, south: float, east: float, north: float, width: int, height: int, *, zoom: int
    ) -> List[List[Tuple[float, float]]]:
        ds_path = self._discover_land_dataset(zoom=zoom)
        if not ds_path:
            return []
        try:
            shapefile = _import_pyshp()
        except Exception as ex:
            _l.w(f"[sources] osm land dataset found but pyshp is unavailable (system or vendored): {ex}")
            return []

        crs = self._dataset_crs(ds_path)
        projector = self._dataset_projector(west, south, east, north, width, height, crs=crs)
        ds_bbox = self._dataset_bbox(west, south, east, north, crs=crs)
        polys: List[List[Tuple[float, float]]] = []
        tol = float(self.land_simplify_for_zoom(zoom))
        try:
            reader = shapefile.Reader(str(ds_path))
        except Exception as ex:
            _l.w(f"[sources] osm land dataset open failed '{ds_path}': {ex}")
            return []

        try:
            for shp in reader.iterShapes():
                try:
                    sb = tuple(shp.bbox) if getattr(shp, "bbox", None) else None
                except Exception:
                    sb = None
                if sb and not self._bbox_intersects(ds_bbox, (float(sb[0]), float(sb[1]), float(sb[2]), float(sb[3]))):
                    continue
                pts = list(getattr(shp, "points", []) or [])
                if len(pts) < 3:
                    continue
                parts = list(getattr(shp, "parts", []) or [0])
                ends = parts[1:] + [len(pts)]
                for start, end in zip(parts, ends):
                    ring = pts[int(start):int(end)]
                    if len(ring) < 3:
                        continue
                    try:
                        if crs == "3857":
                            proj = [projector(float(p[0]), float(p[1])) for p in ring]
                        else:
                            proj = [projector(float(p[1]), float(p[0])) for p in ring]
                    except Exception:
                        continue
                    proj = self._clip_polygon_rect(proj, width, height)
                    proj = self._dedupe_consecutive(proj)
                    if len(proj) >= 3 and self._polygon_area(proj) >= self.LAND_MIN_AREA_PX2:
                        if tol > 0.0:
                            proj = self._dedupe_consecutive(self._simplify_points(proj, tolerance_px=tol))
                        if len(proj) >= 3 and self._polygon_area(proj) >= self.LAND_MIN_AREA_PX2:
                            polys.append(proj)
        finally:
            try:
                reader.close()
            except Exception:
                pass

        if polys:
            _l.i(
                f"[sources] osm land dataset '{ds_path.name}' polygons={len(polys)}"
                f" crs={crs} simplify_px={tol:.2f} zoom={int(zoom)}"
            )
        return polys

    @staticmethod
    def _projector(west: float, south: float, east: float, north: float, width: int, height: int):
        lat_mid = (south + north) * 0.5
        x0, y0 = OSMMapSource.latlon_to_world_px(north, west, 0)
        x1, y1 = OSMMapSource.latlon_to_world_px(south, east, 0)
        dx = max(1e-9, x1 - x0)
        dy = max(1e-9, y1 - y0)

        def _p(lat: float, lon: float) -> Tuple[float, float]:
            x, y = OSMMapSource.latlon_to_world_px(lat, lon, 0)
            px = (x - x0) / dx * float(width)
            py = (y - y0) / dy * float(height)
            return px, py

        return _p

    @staticmethod
    def _path_d_from_points(points: Iterable[Tuple[float, float]], closed: bool = False) -> str:
        pts = list(points or [])
        if len(pts) < 2:
            return ""
        out = [f"M {pts[0][0]:.2f},{pts[0][1]:.2f}"]
        for x, y in pts[1:]:
            out.append(f"L {x:.2f},{y:.2f}")
        if closed:
            out.append("Z")
        return " ".join(out)

    @staticmethod
    def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))

    @staticmethod
    def _polyline_length(points: Iterable[Tuple[float, float]]) -> float:
        pts = list(points or [])
        return sum(math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])) for a, b in zip(pts, pts[1:]))

    @staticmethod
    def _polygon_area(points: Iterable[Tuple[float, float]]) -> float:
        pts = list(points or [])
        if len(pts) < 3:
            return 0.0
        acc = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            acc += float(x1) * float(y2) - float(x2) * float(y1)
        return abs(acc) * 0.5

    @staticmethod
    def _dedupe_consecutive(points: Iterable[Tuple[float, float]]) -> List[Tuple[float, float]]:
        out: List[Tuple[float, float]] = []
        for p in (points or []):
            if not out or abs(float(out[-1][0]) - float(p[0])) > 1e-9 or abs(float(out[-1][1]) - float(p[1])) > 1e-9:
                out.append((float(p[0]), float(p[1])))
        if len(out) >= 2 and abs(float(out[0][0]) - float(out[-1][0])) < 1e-9 and abs(float(out[0][1]) - float(out[-1][1])) < 1e-9:
            out.pop()
        return out

    @staticmethod
    def _clip_polygon_rect(points: Iterable[Tuple[float, float]], width: float, height: float) -> List[Tuple[float, float]]:
        poly = [(float(x), float(y)) for x, y in (points or [])]
        if len(poly) < 3:
            return []

        def inside_left(p): return p[0] >= 0.0
        def inside_right(p): return p[0] <= float(width)
        def inside_top(p): return p[1] >= 0.0
        def inside_bottom(p): return p[1] <= float(height)

        def intersect(a, b, edge: str):
            ax, ay = a
            bx, by = b
            if edge in ("left", "right"):
                x = 0.0 if edge == "left" else float(width)
                if abs(bx - ax) <= 1e-12:
                    return (x, ay)
                t = (x - ax) / (bx - ax)
                return (x, ay + t * (by - ay))
            y = 0.0 if edge == "top" else float(height)
            if abs(by - ay) <= 1e-12:
                return (ax, y)
            t = (y - ay) / (by - ay)
            return (ax + t * (bx - ax), y)

        def clip_edge(subject, edge: str, inside_fn):
            if not subject:
                return []
            out = []
            prev = subject[-1]
            prev_in = inside_fn(prev)
            for cur in subject:
                cur_in = inside_fn(cur)
                if cur_in:
                    if not prev_in:
                        out.append(intersect(prev, cur, edge))
                    out.append(cur)
                elif prev_in:
                    out.append(intersect(prev, cur, edge))
                prev = cur
                prev_in = cur_in
            return out

        poly = clip_edge(poly, "left", inside_left)
        poly = clip_edge(poly, "right", inside_right)
        poly = clip_edge(poly, "top", inside_top)
        poly = clip_edge(poly, "bottom", inside_bottom)
        return OSMMapSource._dedupe_consecutive(poly)

    @staticmethod
    def _direction_at_end(points: List[Tuple[float, float]], *, at_head: bool) -> Optional[Tuple[float, float]]:
        pts = list(points or [])
        if len(pts) < 2:
            return None
        if at_head:
            p0, p1 = pts[0], pts[1]
            vx, vy = float(p0[0]) - float(p1[0]), float(p0[1]) - float(p1[1])
        else:
            p0, p1 = pts[-2], pts[-1]
            vx, vy = float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1])
        n = math.hypot(vx, vy)
        if n <= 1e-9:
            return None
        return (vx / n, vy / n)

    @staticmethod
    def _merge_mode(chain: List[Tuple[float, float]], seg: List[Tuple[float, float]], mode: str) -> List[Tuple[float, float]]:
        if mode == "tail-head":
            return chain + seg[1:]
        if mode == "tail-tail":
            return chain + list(reversed(seg[:-1]))
        if mode == "head-tail":
            return seg[:-1] + chain
        return list(reversed(seg[1:])) + chain

    @classmethod
    def _simplify_points(cls, points: Iterable[Tuple[float, float]], tolerance_px: float = 1.5) -> List[Tuple[float, float]]:
        pts = list(points or [])
        if len(pts) <= 2:
            return pts
        out = [pts[0]]
        last = pts[0]
        tol = max(0.25, float(tolerance_px))
        for p in pts[1:-1]:
            if cls._dist(last, p) >= tol:
                out.append(p)
                last = p
        out.append(pts[-1])
        return out

    @staticmethod
    def _snap_key(p: Tuple[float, float], tol: float = 2.0) -> Tuple[int, int]:
        t = max(0.25, float(tol))
        return int(round(float(p[0]) / t)), int(round(float(p[1]) / t))

    @classmethod
    def _join_polyline_segments(cls, segments: List[List[Tuple[float, float]]], tol: float = 2.0) -> List[List[Tuple[float, float]]]:
        remaining: List[List[Tuple[float, float]]] = []
        for seg in (segments or []):
            pts = list(seg or [])
            if len(pts) >= 2:
                remaining.append(pts)

        out: List[List[Tuple[float, float]]] = []
        while remaining:
            chain = remaining.pop(0)
            changed = True
            while changed:
                changed = False
                i = 0
                while i < len(remaining):
                    seg = remaining[i]
                    a0 = cls._snap_key(chain[0], tol)
                    a1 = cls._snap_key(chain[-1], tol)
                    b0 = cls._snap_key(seg[0], tol)
                    b1 = cls._snap_key(seg[-1], tol)
                    merged = None
                    if a1 == b0:
                        merged = chain + seg[1:]
                    elif a1 == b1:
                        merged = chain + list(reversed(seg[:-1]))
                    elif a0 == b1:
                        merged = seg[:-1] + chain
                    elif a0 == b0:
                        merged = list(reversed(seg[1:])) + chain
                    if merged is not None:
                        chain = merged
                        remaining.pop(i)
                        changed = True
                    else:
                        i += 1
                if not changed and remaining:
                    candidates: List[Tuple[int, str, float]] = []
                    for i, seg in enumerate(remaining):
                        pairs = [
                            ("tail-head", cls._dist(chain[-1], seg[0])),
                            ("tail-tail", cls._dist(chain[-1], seg[-1])),
                            ("head-tail", cls._dist(chain[0], seg[-1])),
                            ("head-head", cls._dist(chain[0], seg[0])),
                        ]
                        for mode, d in pairs:
                            if d <= float(tol):
                                candidates.append((i, mode, d))
                    if len(candidates) == 1:
                        best_i, best_mode, _best_d = candidates[0]
                        seg = remaining.pop(best_i)
                        chain = cls._merge_mode(chain, seg, best_mode)
                        changed = True
            out.append(chain)
        filtered: List[List[Tuple[float, float]]] = []
        for c in out:
            cc = cls._simplify_points(c, tolerance_px=cls.COAST_SIMPLIFY_FINAL_PX)
            if len(cc) < 2:
                continue
            plen = sum(cls._dist(cc[i], cc[i+1]) for i in range(len(cc)-1))
            if plen >= cls.COAST_MIN_CHAIN_LENGTH_PX:
                filtered.append(cc)
        return filtered

    @classmethod
    def _second_pass_join_large_chains(cls, chains: List[List[Tuple[float, float]]]) -> List[List[Tuple[float, float]]]:
        large: List[List[Tuple[float, float]]] = []
        small: List[List[Tuple[float, float]]] = []
        for ch in (chains or []):
            plen = cls._polyline_length(ch)
            if plen >= cls.COAST_SECOND_PASS_MIN_LENGTH_PX:
                large.append(list(ch))
            else:
                small.append(ch)

        remaining = list(large)
        out: List[List[Tuple[float, float]]] = []
        tol = float(cls.COAST_SECOND_PASS_TOLERANCE_PX)
        while remaining:
            chain = remaining.pop(0)
            changed = True
            while changed:
                changed = False
                best_i = -1
                best_mode = ""
                best_dist = float("inf")
                for i, seg in enumerate(remaining):
                    pairs = [
                        ("tail-head", cls._dist(chain[-1], seg[0])),
                        ("tail-tail", cls._dist(chain[-1], seg[-1])),
                        ("head-tail", cls._dist(chain[0], seg[-1])),
                        ("head-head", cls._dist(chain[0], seg[0])),
                    ]
                    mode, dist = min(pairs, key=lambda t: t[1])
                    if dist <= tol and dist < best_dist:
                        best_i = i
                        best_mode = mode
                        best_dist = dist
                if best_i >= 0:
                    seg = remaining.pop(best_i)
                    chain = cls._merge_mode(chain, seg, best_mode)
                    changed = True
            out.append(chain)
        return out + small

    @staticmethod
    def _nearest_border_point(width: float, height: float, p: Tuple[float, float]) -> Tuple[float, float]:
        x = min(max(0.0, float(p[0])), float(width))
        y = min(max(0.0, float(p[1])), float(height))
        cands = [
            (x, 0.0),
            (float(width), y),
            (x, float(height)),
            (0.0, y),
        ]
        return min(cands, key=lambda q: math.hypot(float(q[0]) - x, float(q[1]) - y))

    @staticmethod
    def _near_viewbox_border(width: float, height: float, p: Tuple[float, float], threshold: float) -> bool:
        x = float(p[0]); y = float(p[1]); t = max(1.0, float(threshold))
        return (x <= t) or (y <= t) or (abs(x - float(width)) <= t) or (abs(y - float(height)) <= t)

    @staticmethod
    def _border_param(width: float, height: float, p: Tuple[float, float]) -> float:
        x = min(max(0.0, float(p[0])), float(width))
        y = min(max(0.0, float(p[1])), float(height))
        eps = 1e-6
        if abs(y - 0.0) < eps:
            return x
        if abs(x - float(width)) < eps:
            return float(width) + y
        if abs(y - float(height)) < eps:
            return float(width) + float(height) + (float(width) - x)
        return float(width) * 2.0 + float(height) + (float(height) - y)

    @classmethod
    def _border_arc(cls, width: float, height: float, a: Tuple[float, float], b: Tuple[float, float], *, clockwise: bool) -> List[Tuple[float, float]]:
        per = 2.0 * (float(width) + float(height))
        ta = cls._border_param(width, height, a)
        tb = cls._border_param(width, height, b)
        corners = [
            (0.0, 0.0),
            (float(width), 0.0),
            (float(width), float(height)),
            (0.0, float(height)),
        ]
        pts = [a]
        if clockwise:
            if tb < ta:
                tb += per
            corner_params = [cls._border_param(width, height, c) for c in corners]
            for cp, c in sorted(zip(corner_params, corners), key=lambda t: t[0]):
                cp2 = cp
                while cp2 < ta:
                    cp2 += per
                if ta < cp2 < tb:
                    pts.append(c)
        else:
            if ta < tb:
                ta += per
            corner_params = [cls._border_param(width, height, c) for c in corners]
            for cp, c in sorted(zip(corner_params, corners), key=lambda t: t[0], reverse=True):
                cp2 = cp
                while cp2 < tb:
                    cp2 += per
                if tb < cp2 < ta:
                    pts.append(c)
        pts.append(b)
        return pts

    @classmethod
    def _coastline_land_polygon(cls, width: float, height: float, pts: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        seq = list(pts or [])
        if len(seq) < 2:
            return []
        if cls._dist(seq[0], seq[-1]) <= cls.COAST_JOIN_TOLERANCE_PX:
            poly = seq[:-1]
            if len(poly) >= 3 and cls._polygon_area(poly) >= 200.0:
                return poly
            return []
        thr = cls.COAST_BORDER_CLOSE_THRESHOLD_PX
        if not (cls._near_viewbox_border(width, height, seq[0], thr) and cls._near_viewbox_border(width, height, seq[-1], thr)):
            return []
        a = cls._nearest_border_point(width, height, seq[0])
        b = cls._nearest_border_point(width, height, seq[-1])
        cand_cw = [a] + seq + [b] + cls._border_arc(width, height, b, a, clockwise=True)[1:]
        cand_ccw = [a] + seq + [b] + cls._border_arc(width, height, b, a, clockwise=False)[1:]
        area_cw = cls._polygon_area(cand_cw)
        area_ccw = cls._polygon_area(cand_ccw)
        best = cand_cw if area_cw >= area_ccw else cand_ccw
        return best if cls._polygon_area(best) >= 200.0 else []

    @staticmethod
    def _is_closed_geom(geom: List[dict]) -> bool:
        if len(geom or []) < 3:
            return False
        a = geom[0]
        b = geom[-1]
        try:
            return abs(float(a.get("lat")) - float(b.get("lat"))) < 1e-9 and abs(float(a.get("lon")) - float(b.get("lon"))) < 1e-9
        except Exception:
            return False

    @staticmethod
    def _add_svg_path(parent, d: str, klass: str) -> None:
        if not d:
            return
        el = ET.SubElement(parent, "path")
        el.set("class", klass)
        el.set("d", d)

    @staticmethod
    def _add_point_label(parent, x: float, y: float, name: str, klass: str, radius: float = 3.0) -> None:
        g = ET.SubElement(parent, "g")
        g.set("class", klass)
        c = ET.SubElement(g, "circle")
        c.set("cx", f"{x:.2f}")
        c.set("cy", f"{y:.2f}")
        c.set("r", f"{radius:.2f}")
        if name:
            t = ET.SubElement(g, "text")
            t.set("x", f"{x + 3.0:.2f}")
            t.set("y", f"{y - 2.0:.2f}")
            t.text = name

    @staticmethod
    def _add_peak_triangle(parent, x: float, y: float, klass: str, size: float = 4.0) -> None:
        half = float(size) * 0.5
        pts = [
            (x, y - float(size)),
            (x - half, y),
            (x + half, y),
        ]
        el = ET.SubElement(parent, "path")
        el.set("class", klass)
        el.set("d", f"M {pts[0][0]:.2f},{pts[0][1]:.2f} L {pts[1][0]:.2f},{pts[1][1]:.2f} L {pts[2][0]:.2f},{pts[2][1]:.2f} Z")

    @classmethod
    def _parse_ele_m(cls, tags: Dict[str, str]) -> Optional[float]:
        raw = str((tags or {}).get("ele") or "").strip()
        if not raw:
            return None
        m = re.search(r"[-+]?\d+(?:[.,]\d+)?", raw)
        if not m:
            return None
        try:
            return float(m.group(0).replace(",", "."))
        except Exception:
            return None

    def render_svg(self, zoom: int, lat: float, lon: float, size: str, elements: List[dict], out_file: Path) -> None:
        width, height = self.viewport_px(size)
        west, south, east, north = self.bbox_from_center(zoom, lat, lon, size)
        project = self._projector(west, south, east, north, width, height)

        root = ET.Element("svg", {
            "xmlns": "http://www.w3.org/2000/svg",
            "version": "1.1",
            "width": str(int(width)),
            "height": str(int(height)),
            "viewBox": f"0 0 {int(width)} {int(height)}",
        })
        style = ET.SubElement(root, "style")
        style.text = (
            ".bg{fill:#eef5fb;}"
            ".coast-fill{fill:#f4efe3;stroke:#4f3118;stroke-width:0.76;stroke-linecap:round;stroke-linejoin:round;}"
            ".water-fill{fill:#b7ddf6;stroke:#7fb9dc;stroke-width:1.2;}"
            ".water-line{fill:none;stroke:#7fb9dc;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;}"
            ".coast-line{fill:none;stroke:#4f3118;stroke-width:0.76;stroke-linecap:round;stroke-linejoin:round;}"
            ".connection-road{fill:none;stroke:#9a7448;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;}"
            ".connection-rail{fill:none;stroke:#666;stroke-width:1.2;stroke-dasharray:5 3;stroke-linecap:round;}"
            ".place circle{fill:#000;stroke:#fff;stroke-width:0.8;paint-order:stroke fill;}"
            ".place text{fill:#000;stroke:#fff;stroke-width:1.2;paint-order:stroke fill;font-family:'Segoe UI',sans-serif;font-size:6.5px;font-weight:600;stroke-linejoin:round;}"
            ".peak{fill:#7a4a19;stroke:#ffffff;stroke-width:0.8;}"
        )
        bg = ET.SubElement(root, "rect")
        bg.set("class", "bg")
        bg.set("x", "0")
        bg.set("y", "0")
        bg.set("width", str(int(width)))
        bg.set("height", str(int(height)))

        g_coast = ET.SubElement(root, "g", {"id": "coast"})
        g_water = ET.SubElement(root, "g", {"id": "water"})
        g_coast_frag = ET.SubElement(root, "g", {"id": "coast_fragments"})
        g_peaks = ET.SubElement(root, "g", {"id": "peaks"})
        g_conn = ET.SubElement(root, "g", {"id": "conexiones"})
        g_places = ET.SubElement(root, "g", {"id": "localizaciones"})

        peak_candidates: List[Tuple[float, float, float]] = []
        coast_segments: List[List[Tuple[float, float]]] = []
        land_polys = self.load_land_polygons(west, south, east, north, width, height, zoom=zoom)
        using_land_dataset = bool(land_polys)

        for el in (elements or []):
            tags = dict(el.get("tags") or {})
            typ = str(el.get("type") or "")
            geom = list(el.get("geometry") or [])
            if typ == "way" and geom:
                pts = [project(float(p["lat"]), float(p["lon"])) for p in geom if ("lat" in p and "lon" in p)]
                if len(pts) < 2:
                    continue
                is_water = (
                    tags.get("natural") == "water"
                    or ("water" in tags)
                    or ("waterway" in tags)
                    or tags.get("landuse") in ("reservoir", "basin")
                )
                if (not using_land_dataset) and tags.get("natural") == "coastline":
                    coast_segments.append(list(pts))
                    self._add_svg_path(g_coast_frag, self._path_d_from_points(pts, closed=False), "coast-line")
                    continue
                pts = self._simplify_points(pts, tolerance_px=1.5)
                if is_water:
                    closed = self._is_closed_geom(geom)
                    self._add_svg_path(g_water, self._path_d_from_points(pts, closed=closed), "water-fill" if closed else "water-line")
                    continue
                if tags.get("highway") in ("motorway", "trunk"):
                    self._add_svg_path(g_conn, self._path_d_from_points(pts, closed=False), "connection-road")
                    continue
                if "railway" in tags:
                    self._add_svg_path(g_conn, self._path_d_from_points(pts, closed=False), "connection-rail")
                    continue
            elif typ == "node":
                try:
                    x, y = project(float(el.get("lat")), float(el.get("lon")))
                except Exception:
                    continue
                name = str(tags.get("name") or "").strip()
                if tags.get("natural") in ("peak", "volcano"):
                    ele_m = self._parse_ele_m(tags)
                    if (ele_m is not None) and (ele_m < self.MIN_PEAK_ELEVATION_M):
                        continue
                    peak_candidates.append((float(ele_m or 0.0), x, y))
                    continue
                if tags.get("place") in ("city", "town"):
                    self._add_point_label(g_places, x, y, name, "place", radius=2.6)
                    continue

        if not using_land_dataset:
            coast_chains = self._join_polyline_segments(coast_segments, tol=self.COAST_JOIN_TOLERANCE_PX)
            coast_chains = self._second_pass_join_large_chains(coast_chains)
            for chain in coast_chains:
                poly = self._coastline_land_polygon(width, height, chain)
                if len(poly) >= 3:
                    land_polys.append(poly)

        for poly in land_polys:
            self._add_svg_path(g_coast, self._path_d_from_points(poly, closed=True), "coast-fill")

        if not using_land_dataset:
            for chain in coast_chains:
                self._add_svg_path(g_coast, self._path_d_from_points(chain, closed=False), "coast-line")

        peak_candidates.sort(key=lambda t: t[0], reverse=True)
        for _ele_m, x, y in peak_candidates[:self.MAX_RENDERED_PEAKS]:
            self._add_peak_triangle(g_peaks, x, y, "peak", size=5.0)

        out_file.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")

    def resolve(self, expr: str) -> Optional[List[str]]:
        parsed = self.parse_expr(expr)
        if not parsed:
            return None
        zoom, lat, lon, size = parsed
        out_file = self.cache_svg_file(zoom, lat, lon, size)
        if out_file.is_file():
            _l.i(f"[sources] osm cache hit z={zoom} lat={lat:.4f} lon={lon:.4f} size='{size}'")
            return [str(out_file.resolve())]
        try:
            west, south, east, north = self.bbox_from_center(zoom, lat, lon, size)
            self._query_zoom = zoom
            elements = self.fetch_all_elements(west, south, east, north)
            self.render_svg(zoom, lat, lon, size, elements, out_file)
            meta = out_file.with_suffix(".json")
            try:
                meta.write_text(json.dumps({
                    "zoom": zoom, "lat": lat, "lon": lon, "size": size,
                    "bbox": [west, south, east, north],
                    "elements": len(elements or []),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            _l.i(f"[sources] osm resolved z={zoom} lat={lat:.4f} lon={lon:.4f} size='{size}' elements={len(elements or [])}")
            return [str(out_file.resolve())]
        except Exception as ex:
            _l.w(f"[sources] osm resolve failed '{expr}': {type(ex).__name__}: {ex!r}")
            return []
