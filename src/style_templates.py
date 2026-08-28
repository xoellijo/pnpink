from __future__ import annotations

from dataclasses import dataclass
import re

import inkex

import svg as SVG


STYLE_ATTRS = {
    "class", "style", "fill", "fill-opacity", "fill-rule", "color",
    "stroke", "stroke-width", "stroke-opacity", "stroke-linecap", "stroke-linejoin",
    "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
    "marker-start", "marker-mid", "marker-end", "filter", "opacity",
    "paint-order", "vector-effect", "shape-rendering", "mix-blend-mode",
}


@dataclass(frozen=True)
class PathLayer:
    source: object
    stroke_width: float
    linecap: str


def local_name(node) -> str:
    tag = str(getattr(node, "tag", "") or "")
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def resolve(root, source_id: str):
    sid = str(source_id or "").strip()
    if not sid or root is None:
        return None
    return SVG.find_id(root, sid, include_defs=True)


def copy_style(source, target) -> None:
    for key, value in (getattr(source, "attrib", None) or {}).items():
        if str(key).rsplit("}", 1)[-1] in STYLE_ATTRS:
            target.set(key, value)
    style = SVG.style_map(target)
    style.pop("display", None)
    style.pop("visibility", None)
    SVG.style_set(target, style)


def style_value(node, key: str, default: str = "") -> str:
    current = node
    while current is not None:
        style = SVG.style_map(current)
        value = style.get(key)
        if value is None:
            value = current.get(key)
        if value is not None and str(value).strip() not in ("", "inherit"):
            return str(value).strip()
        current = current.getparent() if hasattr(current, "getparent") else None
    return default


def length_uu(root, value, default: float = 0.0) -> float:
    raw = str(value or "").strip()
    if not raw:
        return float(default)
    try:
        if re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", raw):
            return float(raw)
        return float(root.unittouu(raw))
    except Exception:
        return float(default)


def path_templates(source) -> list:
    if source is None:
        return []
    kind = local_name(source)
    if kind == "path":
        return [source]
    if kind != "g":
        return []
    return [node for node in source.iter() if node is not source and local_name(node) == "path"]


def resolve_path_templates(root, source_id: str) -> list:
    return path_templates(resolve(root, source_id))


def instantiate_path(source, d_attr: str):
    path = SVG.etree.Element(inkex.addNS("path", "svg"))
    if source is None:
        path.set("style", "fill:none;stroke:#000000;stroke-width:1;")
    else:
        copy_style(source, path)
    path.set("d", d_attr)
    return path


def path_layer_plan(root, source, target_stroke_width: float) -> list[PathLayer]:
    templates = path_templates(source)
    if not templates:
        return []
    target = max(float(target_stroke_width), 1e-9)
    measured = []
    for path in templates:
        stroke = style_value(path, "stroke", "none").lower()
        width = 0.0 if stroke in ("", "none") else length_uu(root, style_value(path, "stroke-width", "1"), 1.0)
        measured.append(max(width, 0.0))
    if local_name(source) == "path":
        widths = [target]
    else:
        maximum = max(measured or [0.0])
        widths = [target if maximum <= 1e-9 else target * width / maximum for width in measured]
    return [
        PathLayer(path, width, style_value(path, "stroke-linecap", "butt").lower())
        for path, width in zip(templates, widths)
    ]


def cap_extension(plan: list[PathLayer]) -> float:
    return max(
        [layer.stroke_width * 0.5 for layer in plan if layer.linecap in ("round", "square")]
        or [0.0]
    )


def _clone_group_shell(source):
    group = SVG.etree.Element(inkex.addNS("g", "svg"))
    copy_style(source, group)
    return group


def instantiate_path_stack(source, d_attr: str, plan: list[PathLayer]):
    by_source = {id(layer.source): layer for layer in plan}
    if local_name(source) == "path":
        layer = plan[0]
        path = instantiate_path(source, d_attr)
        style = SVG.style_map(path)
        style["stroke-width"] = f"{layer.stroke_width:.9g}"
        style.setdefault("fill", "none")
        SVG.style_set(path, style)
        return path

    def clone_branch(node):
        kind = local_name(node)
        if kind == "path":
            layer = by_source.get(id(node))
            if layer is None:
                return None
            path = instantiate_path(node, d_attr)
            style = SVG.style_map(path)
            if layer.stroke_width > 0.0:
                style["stroke-width"] = f"{layer.stroke_width:.9g}"
            style.setdefault("fill", "none")
            SVG.style_set(path, style)
            return path
        if kind != "g":
            return None
        group = _clone_group_shell(node)
        for child in node:
            cloned = clone_branch(child)
            if cloned is not None:
                group.append(cloned)
        return group if len(group) else None

    return clone_branch(source)


def rect_corner_radii(source, width: float, height: float) -> tuple[float, float]:
    source_width = max(length_uu(source.getroottree().getroot(), source.get("width"), 0.0), 1e-9)
    source_height = max(length_uu(source.getroottree().getroot(), source.get("height"), 0.0), 1e-9)
    raw_rx = source.get("rx")
    raw_ry = source.get("ry")
    rx = length_uu(source.getroottree().getroot(), raw_rx or raw_ry, 0.0)
    ry = length_uu(source.getroottree().getroot(), raw_ry or raw_rx, 0.0)
    return max(0.0, width * rx / source_width), max(0.0, height * ry / source_height)


__all__ = [
    "STYLE_ATTRS", "PathLayer", "local_name", "resolve", "copy_style", "style_value",
    "length_uu", "path_templates", "resolve_path_templates", "instantiate_path",
    "path_layer_plan", "cap_extension", "instantiate_path_stack", "rect_corner_radii",
]
