# -*- coding: utf-8 -*-
"""Plotter cut-template export from generated page/instance geometry."""

from __future__ import annotations

import os
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import export as EXPORT
import inkscape_cli as INKSCAPE
import log as LOG
import temp_paths as TEMPPATHS

SVG_NS = "http://www.w3.org/2000/svg"
INK_NS = "http://www.inkscape.org/namespaces/inkscape"
CUT_STROKE = "#ff0000"
PAGE_STROKE = "#808080"
STROKE_WIDTH = "0.15"
ET.register_namespace("", SVG_NS)


def _f(value, default=0.0) -> float:
    try:
        return float(str(value or "").strip())
    except Exception:
        return float(default)


def _read_pages(root) -> dict[str, dict]:
    pages: dict[str, dict] = {}
    for el in root.findall(f".//{{{INK_NS}}}page"):
        page_id = str(el.get("id") or "").strip()
        if not page_id:
            continue
        pages[page_id] = {
            "id": page_id,
            "x": _f(el.get("x")),
            "y": _f(el.get("y")),
            "w": _f(el.get("width")),
            "h": _f(el.get("height")),
        }
    return pages


def _parse_bbox(text: str) -> tuple[float, float, float, float] | None:
    parts = str(text or "").replace(",", " ").split()
    if len(parts) != 4:
        return None
    x, y, w, h = [_f(p) for p in parts]
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _local_name(node) -> str:
    tag = str(getattr(node, "tag", "") or "")
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _strip_pnp_suffix(value: str) -> str:
    return value.split("_pnp", 1)[0]


def _clone_node(node):
    return ET.fromstring(ET.tostring(node, encoding="utf-8"))


def _clean_cut_style(node, *, stroke: str = CUT_STROKE) -> None:
    node.set("fill", "none")
    node.set("stroke", stroke)
    node.set("stroke-width", STROKE_WIDTH)
    style = str(node.get("style") or "")
    if style:
        remove = {"display", "visibility", "opacity", "fill", "stroke", "stroke-width"}
        parts = [part.strip() for part in style.split(";") if part.strip().split(":", 1)[0].strip().lower() not in remove]
        if parts:
            node.set("style", ";".join(parts))
        elif "style" in node.attrib:
            del node.attrib["style"]
    for child in list(node):
        _clean_cut_style(child, stroke=stroke)


def _find_cut_shape(carrier, shape_id: str):
    target = str(shape_id or "").strip()
    graphics = {"rect", "path", "circle", "ellipse", "polygon", "polyline"}
    first_graphic = None
    for node in carrier.iter():
        if node is carrier:
            continue
        if _local_name(node) not in graphics:
            continue
        orig = str(node.get("data-origid") or "").strip()
        node_id = str(node.get("id") or "").strip()
        if target and (orig == target or _strip_pnp_suffix(node_id) == target):
            return node
        if first_graphic is None and (orig or node_id):
            first_graphic = node
    return first_graphic


def _shape_kind(shape_node) -> tuple:
    if shape_node is None:
        return ("rect",)
    ln = _local_name(shape_node)
    if ln == "rect":
        rx = _f(shape_node.get("rx"))
        ry = _f(shape_node.get("ry"), rx)
        return ("rect", round(rx, 3), round(ry, 3))
    if ln == "path":
        return ("path", str(shape_node.get("d") or "").strip())
    if ln in {"circle", "ellipse", "polygon", "polyline"}:
        return (ln, str(shape_node.get("points") or "").strip())
    return (ln,)


def _ancestor_transform(parent_map: dict, node, stop) -> str:
    transforms = []
    cur = parent_map.get(node)
    while cur is not None and cur is not stop:
        tr = str(cur.get("transform") or "").strip()
        if tr:
            transforms.append(tr)
        cur = parent_map.get(cur)
    if stop is not None:
        tr = str(stop.get("transform") or "").strip()
        if tr:
            transforms.append(tr)
    transforms.reverse()
    return " ".join(transforms)


def _make_rect_shape(x: float, y: float, w: float, h: float, idx: int):
    node = ET.Element(f"{{{SVG_NS}}}rect", {
        "id": f"cut_{idx}",
        "x": f"{x:.3f}",
        "y": f"{y:.3f}",
        "width": f"{w:.3f}",
        "height": f"{h:.3f}",
    })
    _clean_cut_style(node)
    return node


def _add_page_patterns(patterns: dict[tuple, dict], root, pages: dict[str, dict]) -> None:
    groups = [g for g in root.findall(f".//{{{SVG_NS}}}g") if str(g.get("data-pnpink-page-id") or "").strip()]
    for group in groups:
        page = pages.get(str(group.get("data-pnpink-page-id") or "").strip())
        if not page:
            continue
        parent_map = {child: parent for parent in group.iter() for child in list(parent)}
        shapes = []
        for idx, node in enumerate(group.iter(), start=1):
            bbox = _parse_bbox(node.get("data-pnpink-cut-bbox"))
            if bbox is None:
                continue
            x, y, w, h = bbox
            rel_bbox = (round(x - page["x"], 3), round(y - page["y"], 3), round(w, 3), round(h, 3))
            shape = _find_cut_shape(node, str(node.get("data-pnpink-cut-shape-id") or ""))
            shape_kind = _shape_kind(shape)
            if shape is None:
                out_shape = _make_rect_shape(*rel_bbox, idx)
            else:
                out_shape = _clone_node(shape)
                _clean_cut_style(out_shape)
                out_shape.set("id", f"cut_{idx}")
                transform = _ancestor_transform(parent_map, shape, group)
                wrapper_tr = f"translate({-float(page['x']):.6f},{-float(page['y']):.6f})"
                if transform:
                    wrapper_tr += " " + transform
                wrapper = ET.Element(f"{{{SVG_NS}}}g", {"transform": wrapper_tr})
                wrapper.append(out_shape)
                out_shape = wrapper
            shapes.append((rel_bbox, shape_kind, out_shape))
        if not shapes:
            continue
        shapes.sort(key=lambda item: item[0])
        shape_key = tuple((bbox, kind) for bbox, kind, _shape in shapes)
        key = (round(page["w"], 3), round(page["h"], 3), shape_key)
        if key not in patterns:
            patterns[key] = {
                "page": page,
                "shapes": [shape for _bbox, _kind, shape in shapes],
                "shape_key": shape_key,
                "pages": [],
            }
        patterns[key]["pages"].append(page["id"])


def _merge_layout_patterns(patterns: list[dict]) -> list[dict]:
    out = []
    for idx, pattern in enumerate(patterns):
        page = pattern.get("page") or {}
        key = set(pattern.get("shape_key") or ())
        if not key:
            continue
        is_subset = False
        for j, other in enumerate(patterns):
            if idx == j:
                continue
            opage = other.get("page") or {}
            if round(_f(page.get("w")), 3) != round(_f(opage.get("w")), 3):
                continue
            if round(_f(page.get("h")), 3) != round(_f(opage.get("h")), 3):
                continue
            okey = set(other.get("shape_key") or ())
            if len(okey) > len(key) and key.issubset(okey):
                is_subset = True
                other.setdefault("pages", []).extend(pattern.get("pages") or [])
                break
        if not is_subset:
            out.append(pattern)
    return out


def _write_cut_svg(path: str, page: dict, shapes: list) -> None:
    w = float(page["w"])
    h = float(page["h"])
    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "version": "1.1",
        "width": f"{w:.3f}mm",
        "height": f"{h:.3f}mm",
        "viewBox": f"0 0 {w:.3f} {h:.3f}",
    })
    ET.SubElement(root, f"{{{SVG_NS}}}rect", {
        "id": "page_border",
        "x": "0",
        "y": "0",
        "width": f"{w:.3f}",
        "height": f"{h:.3f}",
        "fill": "none",
        "stroke": PAGE_STROKE,
        "stroke-width": STROKE_WIDTH,
    })
    for shape in shapes:
        root.append(_clone_node(shape))
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _convert_svg(svg_path: str, out_path: str, export_format: str, export_dpi: int) -> tuple[bool, str]:
    exe = INKSCAPE.find_executable()
    if not exe:
        return False, "Inkscape executable not found"
    argv = INKSCAPE.build_page_export_argv(
        exe,
        svg_path,
        out_path,
        export_type=str(export_format or "png").strip().lower(),
        dpi=int(export_dpi or 300),
    )
    rc, msg = INKSCAPE.run(argv, exe_dir=os.path.dirname(exe) or None, env=INKSCAPE.clean_launch_env())
    if int(rc) != 0 or not EXPORT.paths_exist_with_size([out_path]):
        return False, str(msg or f"{export_format.upper()} conversion failed")
    return True, str(msg or "")


def export_cut_templates(svg_path: str, output_base: str, *, export_format: str = "svg", export_dpi: int = 300) -> tuple[bool, dict]:
    started = time.perf_counter()
    fmt = str(export_format or "svg").strip().lower()
    if fmt not in {"svg", "png", "dxf"}:
        fmt = "svg"
    if not svg_path or not os.path.isfile(svg_path):
        return False, {"error": f"SVG output not found: {svg_path}"}

    source_info = EXPORT.resolve_chunked_output_source(svg_path)
    source_paths = list(source_info.get("chunk_paths") or []) or [svg_path]
    patterns_by_key: dict[tuple, dict] = {}
    for source_path in source_paths:
        tree = ET.parse(source_path)
        root = tree.getroot()
        _add_page_patterns(patterns_by_key, root, _read_pages(root))
    patterns = _merge_layout_patterns(list(patterns_by_key.values()))
    if not patterns:
        return False, {"error": "No generated cut bboxes found in SVG output"}

    base = os.path.splitext(os.path.abspath(output_base))[0]
    work_dir = "" if fmt == "svg" else TEMPPATHS.make_work_dir("cut_export", stem=Path(base).name)
    outputs = []
    for index, pattern in enumerate(patterns, start=1):
        final_path = f"{base}_cut{index}.{fmt}"
        svg_target = final_path if fmt == "svg" else os.path.join(work_dir, f"{Path(base).name}_cut{index}.svg")
        _write_cut_svg(svg_target, pattern["page"], pattern["shapes"])
        if fmt != "svg":
            ok, msg = _convert_svg(svg_target, final_path, fmt, export_dpi)
            if not ok:
                return False, {"error": msg, "outputs": outputs}
        outputs.append(final_path)
        LOG.i(
            "[export.cut] pattern=%d pages=%s rects=%d out='%s'",
            index,
            ",".join(pattern.get("pages") or []),
            len(pattern.get("shapes") or []),
            final_path,
        )
    return True, {"outputs": outputs, "pattern_count": len(outputs), "elapsed_s": time.perf_counter() - started}
