from __future__ import annotations

from dataclasses import dataclass
import math
import re

import inkex

import const as CONST
import log as LOG
import svg as SVG


_l = LOG

DECORATION_ATTR = f"{{{CONST.NS_PNP}}}decoration"
LAYER_ATTR = f"{{{CONST.NS_PNP}}}decoration-layer"
PADDING_ATTR = f"{{{CONST.NS_PNP}}}decoration-padding"
_PREPARED_ATTR = "data-dm-decoration-prepared"
_URL_ID_RE = re.compile(r"^url\(\s*#([^)]+)\s*\)$", re.IGNORECASE)
_STYLE_ATTRS = {
    "class", "style", "fill", "fill-opacity", "fill-rule", "color",
    "stroke", "stroke-opacity", "stroke-linecap", "stroke-linejoin",
    "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
    "marker-start", "marker-mid", "marker-end", "filter", "opacity",
    "paint-order", "vector-effect", "shape-rendering",
}


@dataclass
class DecorationItem:
    tspan: SVG.etree._Element
    tspan_id: str
    text_id: str
    source_ref: str
    layer: str
    padding: str


def _attr(node, qualified: str, local: str) -> str:
    return str(node.get(qualified) or node.get(f"pnp:{local}") or "").strip()


def scope_has_decorations(scope) -> bool:
    for node in scope.iter():
        if _attr(node, DECORATION_ATTR, "decoration"):
            return True
        tag = str(getattr(node, "tag", "") or "")
        if tag.endswith("text") and "pnp:decoration" in "".join(node.itertext()).lower():
            return True
    return False


def collect(scope, doc_root, *, mark_prepared: bool = False) -> list[DecorationItem]:
    items = []
    for node in scope.iter():
        tag = str(getattr(node, "tag", "") or "")
        if not tag.endswith("tspan") or node.get(_PREPARED_ATTR) == "1":
            continue
        source_ref = _attr(node, DECORATION_ATTR, "decoration")
        if not source_ref or not "".join(node.itertext()).strip():
            continue
        text = node.getparent()
        while text is not None and not str(getattr(text, "tag", "") or "").endswith("text"):
            text = text.getparent()
        if text is None:
            continue
        tspan_id = SVG.ensure_id(doc_root, node, "dm_decoration_text")
        text_id = SVG.ensure_id(doc_root, text, "dm_decoration_owner")
        layer = _attr(node, LAYER_ATTR, "decoration-layer").lower() or "behind"
        if layer not in ("behind", "front"):
            _l.w("[text_decoration] invalid layer=%r for tspan=%s; using behind", layer, tspan_id)
            layer = "behind"
        items.append(DecorationItem(
            tspan=node,
            tspan_id=tspan_id,
            text_id=text_id,
            source_ref=source_ref,
            layer=layer,
            padding=_attr(node, PADDING_ATTR, "decoration-padding"),
        ))
        if mark_prepared:
            node.set(_PREPARED_ATTR, "1")
    return items


def _source_id(value: str) -> str:
    raw = str(value or "").strip()
    match = _URL_ID_RE.match(raw)
    if match:
        return match.group(1).strip()
    return raw[1:].strip() if raw.startswith("#") else raw


def _padding_tokens(value: str) -> list[str]:
    raw = str(value or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1].strip()
    tokens = [token for token in re.split(r"[\s,]+", raw) if token]
    if not tokens:
        return ["0", "0", "0", "0"]
    if len(tokens) == 1:
        return tokens * 4
    if len(tokens) == 2:
        return [tokens[0], tokens[1], tokens[0], tokens[1]]
    if len(tokens) == 3:
        return [tokens[0], tokens[1], tokens[2], tokens[1]]
    return tokens[:4]


def _measure(doc_root, token: str, base: float) -> float:
    value = str(token or "").strip().lower()
    try:
        if value.endswith("em"):
            return float(value[:-2]) * float(base)
        if value.endswith("%"):
            return float(value[:-1]) * float(base) / 100.0
        if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", value):
            return float(doc_root.unittouu(f"{value}mm"))
        return float(doc_root.unittouu(value))
    except Exception:
        _l.w("[text_decoration] invalid padding token=%r; using 0", token)
        return 0.0


def _padding(doc_root, value: str, width: float, height: float):
    top, right, bottom, left = _padding_tokens(value)
    return (
        _measure(doc_root, top, height),
        _measure(doc_root, right, width),
        _measure(doc_root, bottom, height),
        _measure(doc_root, left, width),
    )


def _axes(node):
    transform = SVG.composed_transform(node)
    axis_x = (float(transform.a), float(transform.b))
    axis_y = (float(transform.c), float(transform.d))
    length_x = math.hypot(*axis_x) or 1.0
    length_y = math.hypot(*axis_y) or 1.0
    return (
        (axis_x[0] / length_x, axis_x[1] / length_x),
        (axis_y[0] / length_y, axis_y[1] / length_y),
    )


def _oriented_size(bbox, axis_x, axis_y):
    box_width = max(0.0, float(bbox.get("width") or 0.0))
    box_height = max(0.0, float(bbox.get("height") or 0.0))
    ax, ay = abs(axis_x[0]), abs(axis_x[1])
    bx, by = abs(axis_y[0]), abs(axis_y[1])
    determinant = ax * by - bx * ay
    if abs(determinant) > 1e-6:
        width = (box_width * by - bx * box_height) / determinant
        height = (ax * box_height - box_width * ay) / determinant
        if width > 0.0 and height > 0.0:
            return width, height
    projected_x = box_width * ax + box_height * ay
    projected_y = box_width * bx + box_height * by
    return max(projected_x, 1e-9), max(projected_y, 1e-9)


def _copy_style(source, path) -> None:
    for key, value in (source.attrib or {}).items():
        local = str(key).rsplit("}", 1)[-1]
        if local in _STYLE_ATTRS:
            path.set(key, value)
    style = SVG.style_map(path)
    style.pop("display", None)
    style.pop("visibility", None)
    SVG.style_set(path, style)


def _parent_inverse_transform(parent):
    transform = SVG.composed_transform(parent)
    try:
        return transform.inverse()
    except AttributeError:
        return -transform


def apply(doc_root, items, bboxes) -> int:
    inserted = 0
    positions = {}
    for item in items or []:
        bbox = (bboxes or {}).get(item.tspan_id)
        tspan = item.tspan
        text = SVG.find_id(doc_root, item.text_id, include_defs=False)
        source = SVG.find_id(doc_root, _source_id(item.source_ref), include_defs=True)
        if bbox is None or text is None or source is None:
            _l.w(
                "[text_decoration] unresolved tspan=%s source=%r bbox=%s",
                item.tspan_id, item.source_ref, bbox is not None,
            )
            continue
        parent = text.getparent()
        if parent is None:
            continue
        axis_x, axis_y = _axes(tspan)
        width, height = _oriented_size(bbox, axis_x, axis_y)
        top, right, bottom, left = _padding(doc_root, item.padding, width, height)
        width = max(1e-9, width + left + right)
        stroke_width = max(1e-9, height + top + bottom)
        center_x = float(bbox["x"]) + float(bbox["width"]) * 0.5
        center_y = float(bbox["y"]) + float(bbox["height"]) * 0.5
        center_x += axis_y[0] * (bottom - top) * 0.5
        center_y += axis_y[1] * (bottom - top) * 0.5
        start_x = center_x - axis_x[0] * width * 0.5
        start_y = center_y - axis_x[1] * width * 0.5
        end_x = center_x + axis_x[0] * width * 0.5
        end_y = center_y + axis_x[1] * width * 0.5

        path = SVG.etree.Element(f"{{{CONST.NS_SVG}}}path")
        path.set("id", SVG.ensure_id(doc_root, path, "dm_text_decoration"))
        path.set("d", f"M {start_x:.9g},{start_y:.9g} L {end_x:.9g},{end_y:.9g}")
        path.set("transform", str(_parent_inverse_transform(parent)))
        path.set("data-dm-text-decoration", item.tspan_id)
        _copy_style(source, path)
        style = SVG.style_map(path)
        style["stroke-width"] = f"{stroke_width:.9g}"
        style.setdefault("fill", "none")
        SVG.style_set(path, style)

        key = id(text)
        if key not in positions:
            positions[key] = [parent.index(text), parent.index(text) + 1]
        if item.layer == "front":
            index = positions[key][1]
            parent.insert(index, path)
            positions[key][1] += 1
        else:
            index = positions[key][0]
            parent.insert(index, path)
            positions[key][0] += 1
            positions[key][1] += 1
        tspan.attrib.pop(_PREPARED_ATTR, None)
        inserted += 1
    return inserted
