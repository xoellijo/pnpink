from __future__ import annotations

from dataclasses import dataclass
import math
import re

import inkex

import const as CONST
import gradients as GRD
import log as LOG
import style_templates as STPL
import svg as SVG


_l = LOG

DECORATION_ATTR = f"{{{CONST.NS_PNP}}}decoration"
LAYER_ATTR = f"{{{CONST.NS_PNP}}}decoration-layer"
PADDING_ATTR = f"{{{CONST.NS_PNP}}}decoration-padding"
_PREPARED_ATTR = "data-dm-decoration-prepared"
_URL_ID_RE = re.compile(r"^url\(\s*#([^)]+)\s*\)$", re.IGNORECASE)
_DEFAULT_PADDING = "1pt"
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


def _measure(doc_root, token: str, base: float, *, em_base: float | None = None) -> float:
    value = str(token or "").strip().lower()
    try:
        if value.endswith("em"):
            return float(value[:-2]) * float(base if em_base is None else em_base)
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
        _measure(doc_root, top, height, em_base=height),
        _measure(doc_root, right, width, em_base=height),
        _measure(doc_root, bottom, height, em_base=height),
        _measure(doc_root, left, width, em_base=height),
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


def _parent_inverse_transform(parent):
    transform = SVG.composed_transform(parent)
    try:
        return transform.inverse()
    except AttributeError:
        return -transform


def _path_decoration(doc_root, source, parent, axis_x, center_x, center_y, width, stroke_width):
    plan = STPL.path_layer_plan(doc_root, source, stroke_width)
    if not plan:
        return None
    extension = STPL.cap_extension(plan)
    line_width = max(1e-9, float(width) - extension * 2.0)
    start_x = center_x - axis_x[0] * line_width * 0.5
    start_y = center_y - axis_x[1] * line_width * 0.5
    end_x = center_x + axis_x[0] * line_width * 0.5
    end_y = center_y + axis_x[1] * line_width * 0.5
    d_attr = f"M {start_x:.9g},{start_y:.9g} L {end_x:.9g},{end_y:.9g}"
    node = STPL.instantiate_path_stack(source, d_attr, plan)
    if node is None:
        return None
    node.set("transform", str(_parent_inverse_transform(parent)))
    return node


def _rect_decoration(doc_root, source, parent, axis_x, axis_y, center_x, center_y, width, height):
    stroke = STPL.style_value(source, "stroke", "none").lower()
    source_stroke = 0.0 if stroke in ("", "none") else STPL.length_uu(
        doc_root,
        STPL.style_value(source, "stroke-width", "1"),
        1.0,
    )
    rect_width = max(1e-9, float(width) - source_stroke)
    rect_height = max(1e-9, float(height) - source_stroke)
    rect = SVG.etree.Element(f"{{{CONST.NS_SVG}}}rect")
    rect.set("x", f"{-rect_width * 0.5:.9g}")
    rect.set("y", f"{-rect_height * 0.5:.9g}")
    rect.set("width", f"{rect_width:.9g}")
    rect.set("height", f"{rect_height:.9g}")
    rx, ry = STPL.rect_corner_radii(source, rect_width, rect_height)
    if rx > 0.0:
        rect.set("rx", f"{rx:.9g}")
    if ry > 0.0:
        rect.set("ry", f"{ry:.9g}")
    STPL.copy_style(source, rect)
    world = inkex.Transform(
        f"matrix({axis_x[0]:.12g},{axis_x[1]:.12g},"
        f"{axis_y[0]:.12g},{axis_y[1]:.12g},{center_x:.12g},{center_y:.12g})"
    )
    rect.set("transform", str(_parent_inverse_transform(parent) @ world))
    GRD.normalize_user_space_gradients(
        doc_root,
        rect,
        (
            STPL.length_uu(doc_root, source.get("x"), 0.0),
            STPL.length_uu(doc_root, source.get("y"), 0.0),
            STPL.length_uu(doc_root, source.get("width"), 0.0),
            STPL.length_uu(doc_root, source.get("height"), 0.0),
        ),
    )
    return rect


def apply(doc_root, items, bboxes) -> int:
    inserted = 0
    positions = {}
    for item in items or []:
        bbox = (bboxes or {}).get(item.tspan_id)
        tspan = item.tspan
        text = SVG.find_id(doc_root, item.text_id, include_defs=False)
        source = STPL.resolve(doc_root, _source_id(item.source_ref))
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
        top, right, bottom, left = _padding(
            doc_root, item.padding or _DEFAULT_PADDING, width, height
        )
        width = max(1e-9, width + left + right)
        stroke_width = max(1e-9, height + top + bottom)
        center_x = float(bbox["x"]) + float(bbox["width"]) * 0.5
        center_y = float(bbox["y"]) + float(bbox["height"]) * 0.5
        center_x += axis_y[0] * (bottom - top) * 0.5
        center_y += axis_y[1] * (bottom - top) * 0.5
        source_kind = STPL.local_name(source)
        if source_kind in ("path", "g"):
            decoration = _path_decoration(
                doc_root, source, parent, axis_x,
                center_x, center_y, width, stroke_width,
            )
        elif source_kind == "rect":
            decoration = _rect_decoration(
                doc_root, source, parent, axis_x, axis_y,
                center_x, center_y, width, stroke_width,
            )
        else:
            _l.w(
                "[text_decoration] unsupported source type=%r source=%r",
                source_kind, item.source_ref,
            )
            continue
        if decoration is None:
            _l.w("[text_decoration] source=%r produced no geometry", item.source_ref)
            continue
        decoration.set("id", SVG.ensure_id(doc_root, decoration, "dm_text_decoration"))
        decoration.set("data-dm-text-decoration", item.tspan_id)

        key = id(text)
        if key not in positions:
            positions[key] = [parent.index(text), parent.index(text) + 1]
        if item.layer == "front":
            index = positions[key][1]
            parent.insert(index, decoration)
            positions[key][1] += 1
        else:
            index = positions[key][0]
            parent.insert(index, decoration)
            positions[key][0] += 1
            positions[key][1] += 1
        tspan.attrib.pop(_PREPARED_ATTR, None)
        inserted += 1
    return inserted
