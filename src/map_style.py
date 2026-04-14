# -*- coding: utf-8 -*-
"""Map style rules for vector-tile-backed OSM/OpenFreeMap rendering."""
from __future__ import annotations

from typing import Any, Dict, Optional
import re


GEOM_UNKNOWN = 0
GEOM_POINT = 1
GEOM_LINESTRING = 2
GEOM_POLYGON = 3


_BASE_LAYER_STYLE: Dict[int, Dict[str, str]] = {
    GEOM_POLYGON: {"fill": "#ddd", "stroke": "#666", "stroke-width": "0.4"},
    GEOM_LINESTRING: {"fill": "none", "stroke": "#444", "stroke-width": "0.7"},
    GEOM_POINT: {"fill": "#000", "stroke": "#fff", "stroke-width": "0.8"},
}

_LAYER_ORDER: Dict[str, int] = {
    "ocean": 0,
    "landcover": 2,
    "park": 3,
    "landuse": 4,
    "land": 5,
    "water": 6,
    "water_polygons": 6,
    "waterway": 7,
    "water_lines": 7,
    "aeroway": 12,
    "transportation": 20,
    "streets": 20,
    "building": 30,
    "buildings": 30,
    "water_name": 90,
    "water_polygons_labels": 90,
    "transportation_name": 92,
    "street_labels": 92,
    "mountain_peak": 94,
    "place": 96,
    "place_labels": 96,
}


def _props(feature: Any) -> Dict[str, object]:
    return dict(getattr(feature, "properties", {}) or {})


def feature_kind(feature: Any) -> str:
    props = _props(feature)
    value = props.get("kind")
    if value in (None, ""):
        value = props.get("class")
    return str(value or "").strip().lower()


def feature_label_text(feature: Any) -> Optional[str]:
    props = _props(feature)
    for key in ("name", "name_en", "name:latin", "name_int"):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _slug(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "item"


def feature_id(layer_name: str, feature: Any, counters: Dict[str, int]) -> str:
    layer_slug = _slug(layer_name)
    kind_slug = _slug(feature_kind(feature))
    key = f"{layer_slug}:{kind_slug}"
    counters[key] = counters.get(key, 0) + 1
    numeric_id = getattr(feature, "id", None)
    if numeric_id is not None:
        return f"{kind_slug}_{numeric_id}"
    return f"{kind_slug}_{counters[key]}"


def _zoom_band(z: int) -> int:
    if z <= 9:
        return 0
    if z <= 11:
        return 1
    return 2


def include_feature(layer_name: str, feature: Any, z: int) -> bool:
    lname = str(layer_name or "").lower()
    kind = feature_kind(feature)
    band = _zoom_band(z)
    props = _props(feature)
    rank = int(props.get("rank") or 999)
    ele = int(props.get("ele") or -9999)

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
    if lname in ("boundaries", "boundary"):
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
        rail = int(props.get("rail") or 0)
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


def feature_style(layer_name: str, feature: Any, geom_type: int) -> Dict[str, str]:
    lname = str(layer_name or "").lower()
    kind = feature_kind(feature)
    props = _props(feature)

    if geom_type == GEOM_POLYGON:
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
            if kind == "beach":
                return {"fill": "#ecd8aa", "stroke": "#d7bf8a", "stroke-width": "0.2"}
            if kind in ("industrial", "commercial", "brownfield", "garages", "greenfield"):
                return {"fill": "#d8d4cf", "stroke": "#bbb2aa", "stroke-width": "0.2"}
            if kind in ("bare_rock", "heath"):
                return {"fill": "#d2c6b6", "stroke": "#b9ab99", "stroke-width": "0.2"}
            return {"fill": "#d8d2bd", "stroke": "#c4bba8", "stroke-width": "0.18"}

    if geom_type == GEOM_LINESTRING:
        if lname in ("waterway", "water_lines"):
            return {"fill": "none", "stroke": "#6fb2e4", "stroke-width": "0.8"}
        if lname == "transportation":
            subclass = str(props.get("subclass") or "").strip().lower()
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
        if lname == "streets":
            rail = int(props.get("rail") or 0)
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

    if lname == "mountain_peak":
        if kind == "saddle":
            return {"fill": "#7c6855", "stroke": "#fff", "stroke-width": "0.8"}
        return {"fill": "#5f5244", "stroke": "#fff", "stroke-width": "0.8"}

    return dict(_BASE_LAYER_STYLE.get(geom_type, _BASE_LAYER_STYLE[GEOM_POINT]))


def label_style(layer_name: str, feature: Any) -> Dict[str, str]:
    lname = str(layer_name or "").lower()
    kind = feature_kind(feature)
    style = {
        "font-size": "10",
        "font-family": "Segoe UI, sans-serif",
        "fill": "#111",
        "stroke": "#fff",
        "stroke-width": "1.2",
        "paint-order": "stroke fill",
        "stroke-linejoin": "round",
    }
    if lname in ("place", "place_labels"):
        if kind == "city":
            style["font-size"] = "12"
        elif kind == "town":
            style["font-size"] = "11"
    elif lname == "mountain_peak":
        style["font-size"] = "8"
    elif lname in ("water_name", "water_polygons_labels"):
        style["font-size"] = "9"
    return style


def label_offset(layer_name: str, feature: Any) -> tuple[float, float]:
    lname = str(layer_name or "").lower()
    if lname == "mountain_peak":
        return 4.0, -4.0
    return 4.0, -2.0


def layer_order(layer_name: str) -> int:
    return _LAYER_ORDER.get(str(layer_name or "").lower(), 50)
