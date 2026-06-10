# -*- coding: utf-8 -*-
"""Map style rules for vector-tile-backed OSM/OpenFreeMap rendering."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional
import json
import re
import time


GEOM_UNKNOWN = 0
GEOM_POINT = 1
GEOM_LINESTRING = 2
GEOM_POLYGON = 3

_GEOM_NAMES = {
    GEOM_POLYGON: "polygon",
    GEOM_LINESTRING: "line",
    GEOM_POINT: "point",
}

_FALLBACK_CONFIG: Dict[str, Any] = {
    "label_languages": ["name_en", "name:en", "name:latin", "name_int", "name"],
    "zoom_bands": {
        "low": {"max": 9},
        "medium": {"min": 10, "max": 11},
        "high": {"min": 12},
    },
    "layer_order": {},
    "base_style": {
        "polygon": {"fill": "#ddd", "stroke": "#666", "stroke-width": "0.4"},
        "line": {"fill": "none", "stroke": "#444", "stroke-width": "0.7", "stroke-linejoin": "round", "stroke-linecap": "round"},
        "point": {"fill": "#000", "stroke": "#fff", "stroke-width": "0.8"},
    },
    "features": {},
    "label_style": {
        "default": {
            "font-size": "10",
            "font-family": "Segoe UI, sans-serif",
            "fill": "#111",
            "stroke": "#fff",
            "stroke-width": "1.2",
            "paint-order": "stroke fill",
            "stroke-linejoin": "round",
        }
    },
    "label_offset": {"default": [4.0, -2.0]},
}


def _strip_jsonc(text: str) -> str:
    out = []
    i = 0
    in_str = False
    esc = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for k, v in dict(override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def _config_path() -> Path:
    return Path(__file__).with_name("map_style.jsonc")


def _load_config() -> Dict[str, Any]:
    path = _config_path()
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(_strip_jsonc(raw))
        if isinstance(data, dict):
            return _merge_dict(_FALLBACK_CONFIG, data)
    except Exception:
        pass
    return deepcopy(_FALLBACK_CONFIG)


_CONFIG = _load_config()
try:
    _CONFIG_MTIME = _config_path().stat().st_mtime
except Exception:
    _CONFIG_MTIME = None
_CONFIG_NEXT_CHECK = 0.0


def _config() -> Dict[str, Any]:
    global _CONFIG, _CONFIG_MTIME, _CONFIG_NEXT_CHECK
    now = time.monotonic()
    if now < _CONFIG_NEXT_CHECK:
        return _CONFIG
    _CONFIG_NEXT_CHECK = now + 0.5
    try:
        mtime = _config_path().stat().st_mtime
    except Exception:
        mtime = None
    if mtime != _CONFIG_MTIME:
        _CONFIG = _load_config()
        _CONFIG_MTIME = mtime
    return _CONFIG


def _props(feature: Any) -> Dict[str, object]:
    return dict(getattr(feature, "properties", {}) or {})


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _split_view_expr(expr: str) -> list[tuple[str, str]]:
    text = str(expr or "").strip()
    out: list[tuple[str, str]] = []
    if not text:
        return out
    sign = "+"
    token = []
    depth = 0
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
            token.append(ch)
            continue
        if ch == "]":
            depth = max(0, depth - 1)
            token.append(ch)
            continue
        if depth == 0 and ch in "+-" and i > 0:
            value = "".join(token).strip()
            if value:
                out.append((sign, value))
            sign = ch
            token = []
            continue
        if depth == 0 and ch in "+-" and i == 0:
            sign = ch
            continue
        token.append(ch)
    value = "".join(token).strip()
    if value:
        out.append((sign, value))
    return out


def _parse_view_item(raw: str) -> tuple[str, Optional[set[str]]]:
    item = str(raw or "").strip()
    m = re.fullmatch(r"([A-Za-z_][\w\-]*)\s*\[\s*([^\]]*)\s*\]", item)
    if m:
        values = [v for v in re.split(r"\s+", str(m.group(2) or "").strip()) if v]
        return m.group(1).lower(), {str(v).strip().lower() for v in values if str(v).strip()}
    return item.lower(), None


def _view_layers(name: str) -> Optional[list[str]]:
    value = (_config().get("views") or {}).get(str(name or "").lower())
    if value is None:
        return None
    if value == "*":
        return ["*"]
    return [str(v).strip().lower() for v in _as_list(value) if str(v).strip()]


def resolve_view_filter(expr: str) -> Optional[Dict[str, Any]]:
    tokens = _split_view_expr(expr)
    if not tokens:
        return None
    include_all = False
    include_layers: set[str] = set()
    include_kinds: Dict[str, set[str]] = {}
    exclude_layers: set[str] = set()
    exclude_kinds: Dict[str, set[str]] = {}
    labels_disabled = False
    labels_enabled_layers: set[str] = set()

    def apply_item(sign: str, raw_item: str) -> None:
        nonlocal include_all, labels_disabled
        name, kinds = _parse_view_item(raw_item)
        preset_layers = None if kinds is not None else _view_layers(name)
        if preset_layers is not None:
            if name in {"label", "labels"}:
                labels_disabled = sign == "-"
            layers = [str(v).lower() for v in preset_layers]
        else:
            layers = [name]
        for layer in layers:
            if layer == "*":
                include_all = sign == "+"
                continue
            if kinds is None:
                if sign == "+":
                    include_layers.add(layer)
                    exclude_layers.discard(layer)
                    if layer.endswith("_name") or layer.endswith("_labels"):
                        labels_enabled_layers.add(layer)
                else:
                    exclude_layers.add(layer)
                    include_layers.discard(layer)
                continue
            target = include_kinds if sign == "+" else exclude_kinds
            target.setdefault(layer, set()).update(kinds)
            if sign == "+":
                exclude_layers.discard(layer)
                if layer.endswith("_name") or layer.endswith("_labels"):
                    labels_enabled_layers.add(layer)

    for sign, item in tokens:
        apply_item(sign, item)

    return {
        "include_all": include_all,
        "include_layers": include_layers,
        "include_kinds": include_kinds,
        "exclude_layers": exclude_layers,
        "exclude_kinds": exclude_kinds,
        "labels_disabled": labels_disabled,
        "labels_enabled_layers": labels_enabled_layers,
        "raw": str(expr or "").strip(),
    }


def view_allows_layer(view_filter: Optional[Dict[str, Any]], layer_name: str) -> bool:
    if not view_filter:
        return True
    layer = str(layer_name or "").lower()
    if layer in (view_filter.get("exclude_layers") or set()):
        return False
    return (
        bool(view_filter.get("include_all"))
        or layer in (view_filter.get("include_layers") or set())
        or layer in (view_filter.get("include_kinds") or {})
    )


def view_allows_feature(view_filter: Optional[Dict[str, Any]], layer_name: str, feature: Any) -> bool:
    if not view_allows_layer(view_filter, layer_name):
        return False
    if not view_filter:
        return True
    layer = str(layer_name or "").lower()
    kind = feature_kind(feature)
    if kind and kind in (view_filter.get("exclude_kinds") or {}).get(layer, set()):
        return False
    include_kinds = view_filter.get("include_kinds") or {}
    if layer in include_kinds and layer not in (view_filter.get("include_layers") or set()):
        return kind in include_kinds.get(layer, set())
    return True


def view_allows_label(view_filter: Optional[Dict[str, Any]], layer_name: str) -> bool:
    if not view_filter:
        return True
    layer = str(layer_name or "").lower()
    if layer in (view_filter.get("labels_enabled_layers") or set()):
        return True
    return not bool(view_filter.get("labels_disabled"))


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _feature_value(feature: Any, key: str) -> Any:
    props = _props(feature)
    if key == "kind":
        return feature_kind(feature)
    if key == "class":
        return str(props.get("class") or "").strip().lower()
    return props.get(key)


def feature_kind(feature: Any) -> str:
    props = _props(feature)
    value = props.get("kind")
    if value in (None, ""):
        value = props.get("class")
    return str(value or "").strip().lower()


def feature_label_text(feature: Any) -> Optional[str]:
    props = _props(feature)
    for key in _as_list(_config().get("label_languages")):
        value = props.get(str(key))
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
    kind_slug = _slug(feature_kind(feature) or layer_slug)
    key = f"{layer_slug}:{kind_slug}"
    counters[key] = counters.get(key, 0) + 1
    counters["__uid__"] = counters.get("__uid__", 0) + 1
    numeric_id = getattr(feature, "id", None)
    suffix = f"_u{counters['__uid__']}"
    if numeric_id is not None:
        return f"{kind_slug}{numeric_id}{suffix}"
    return f"{kind_slug}{counters[key]}{suffix}"


def _band_matches(rule_zoom: Any, z: int) -> bool:
    if rule_zoom in (None, "", "all", "*"):
        return True
    bands = _config().get("zoom_bands") or {}
    for name in _as_list(rule_zoom):
        if isinstance(name, (int, float)):
            if int(name) == int(z):
                return True
            continue
        spec = bands.get(str(name))
        if not isinstance(spec, dict):
            continue
        zmin = spec.get("min", -999)
        zmax = spec.get("max", 999)
        if int(z) >= _to_int(zmin, -999) and int(z) <= _to_int(zmax, 999):
            return True
    return False


def _value_matches(actual: Any, expected: Any) -> bool:
    expected_values = [str(v).strip().lower() for v in _as_list(expected)]
    actual_s = str(actual or "").strip().lower()
    return actual_s in expected_values


_RULE_CONTROL_KEYS = {
    "always", "zoom", "attrs",
    "rank_min", "rank_max", "ele_min", "ele_max",
}


def _rule_matches(rule: Dict[str, Any], feature: Any, z: int) -> bool:
    if not isinstance(rule, dict):
        return False
    if rule.get("always") is True:
        return True
    if not _band_matches(rule.get("zoom"), z):
        return False
    for key in ("kind", "class", "subclass"):
        if key in rule and not _value_matches(_feature_value(feature, key), rule.get(key)):
            return False
    for key, expected in rule.items():
        if key in _RULE_CONTROL_KEYS or key in ("kind", "class", "subclass", "rail"):
            continue
        if not _value_matches(_feature_value(feature, key), expected):
            return False
    if "rail" in rule and _to_int(_feature_value(feature, "rail"), 0) != _to_int(rule.get("rail"), 0):
        return False
    rank = _to_int(_feature_value(feature, "rank"), 999)
    ele = _to_int(_feature_value(feature, "ele"), -9999)
    if "rank_min" in rule and rank < _to_int(rule.get("rank_min"), -999):
        return False
    if "rank_max" in rule and rank > _to_int(rule.get("rank_max"), 999):
        return False
    if "ele_min" in rule and ele < _to_int(rule.get("ele_min"), -9999):
        return False
    if "ele_max" in rule and ele > _to_int(rule.get("ele_max"), 999999):
        return False
    return True


def _feature_cfg(layer_name: str) -> Dict[str, Any]:
    return dict((_config().get("features") or {}).get(str(layer_name or "").lower()) or {})


def include_feature(layer_name: str, feature: Any, z: int) -> bool:
    cfg = _feature_cfg(layer_name)
    if "include" not in cfg:
        return True
    rules = cfg.get("include") or []
    return any(_rule_matches(rule, feature, z) for rule in rules)


def _style_for_rules(rules: Any, feature: Any, z: int) -> Dict[str, str]:
    for rule in _as_list(rules):
        if not isinstance(rule, dict):
            continue
        if _rule_matches(rule, feature, z):
            attrs = rule.get("attrs") or {}
            if isinstance(attrs, dict):
                return {str(k): _style_value(v, feature, z) for k, v in attrs.items()}
    return {}


def _fmt_num(value: float) -> str:
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _eval_expr(expr: str, feature: Any, z: int) -> Optional[str]:
    text = str(expr or "").strip()
    if not text.startswith("="):
        return None
    body = text[1:].strip()
    if not re.match(r"^[0-9A-Za-z_+\-*/()., \t]+$", body):
        return None
    props = _props(feature)
    names = {
        "rank": _to_int(props.get("rank"), 0),
        "ele": _to_int(props.get("ele"), 0),
        "zoom": int(z),
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "clamp": lambda v, lo, hi: max(float(lo), min(float(hi), float(v))),
    }
    try:
        return _fmt_num(eval(body, {"__builtins__": {}}, names))
    except Exception:
        return None


def _style_value(value: Any, feature: Any, z: int) -> str:
    if isinstance(value, str):
        evaluated = _eval_expr(value, feature, z)
        if evaluated is not None:
            return evaluated
    return str(value)


def feature_style(layer_name: str, feature: Any, geom_type: int, z: int = 0) -> Dict[str, str]:
    geom_name = _GEOM_NAMES.get(geom_type, "point")
    base = dict(((_config().get("base_style") or {}).get(geom_name)) or {})
    cfg = _feature_cfg(layer_name)
    layer_style = cfg.get("style") or {}
    styled = _style_for_rules(layer_style.get(geom_name), feature, z)
    if styled:
        base.update(styled)
    return base


def label_style(layer_name: str, feature: Any, z: int = 0) -> Dict[str, str]:
    kind = feature_kind(feature)
    root = _config().get("label_style") or {}
    style = dict(root.get("default") or {})
    cfg = _feature_cfg(layer_name)
    layer_labels = cfg.get("label_style") or {}
    style.update(dict(layer_labels.get("default") or {}))
    style.update(dict(layer_labels.get(kind) or {}))
    return {str(k): _style_value(v, feature, z) for k, v in style.items()}


def labels_enabled(layer_name: str) -> bool:
    return _feature_cfg(layer_name).get("labels", True) is not False


def point_shape(layer_name: str, feature: Any, z: int = 0) -> str:
    cfg = _feature_cfg(layer_name)
    shapes = cfg.get("point_shape") or {}
    if isinstance(shapes, dict):
        kind = feature_kind(feature)
        value = shapes.get(kind, shapes.get("default", "circle"))
    else:
        value = shapes or "circle"
    return str(value or "circle").strip().lower()


def point_size(layer_name: str, feature: Any, z: int = 0) -> float:
    cfg = _feature_cfg(layer_name)
    sizes = cfg.get("point_size") or {}
    value = 2.2
    if isinstance(sizes, dict):
        kind = feature_kind(feature)
        value = sizes.get(kind, sizes.get("default", value))
    elif sizes:
        value = sizes
    try:
        return max(0.1, float(_style_value(value, feature, z)))
    except Exception:
        return 2.2


def label_offset(layer_name: str, feature: Any) -> tuple[float, float]:
    cfg = _feature_cfg(layer_name)
    val = cfg.get("label_offset")
    if val is None:
        val = ((_config().get("label_offset") or {}).get("default") or [4.0, -2.0])
    vals = _as_list(val)
    try:
        return float(vals[0]), float(vals[1])
    except Exception:
        return 4.0, -2.0


def layer_order(layer_name: str) -> int:
    return _to_int((_config().get("layer_order") or {}).get(str(layer_name or "").lower()), 50)
