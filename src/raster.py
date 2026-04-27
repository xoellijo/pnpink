# -*- coding: utf-8 -*-
"""Rasterization helpers for SVG export pre-processing."""

from __future__ import annotations

import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import inkscape_cli as INKSCAPE
import log as LOG

_l = LOG


def _bitmap_size_px(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(str(path)) as im:
            return int(im.width), int(im.height)
    except Exception:
        pass

    try:
        with open(path, "rb") as fh:
            header = fh.read(24)
        if header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
            return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
    except Exception:
        pass
    return None


def _has_svg_filter_reference(node) -> bool:
    try:
        val = str(node.get("filter") or "").strip()
        if val and val.lower() != "none":
            return True
        style = str(node.get("style") or "")
        for part in style.split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            if key.strip().lower() == "filter" and value.strip().lower() not in ("", "none"):
                return True
    except Exception:
        pass
    return False


def _is_inside_non_rendered_svg_area(node) -> bool:
    blocked = {"defs", "filter", "mask", "clipPath", "pattern", "marker", "symbol", "metadata", "namedview"}
    cur = node
    while cur is not None:
        tag = getattr(cur, "tag", "")
        local = str(tag).rsplit("}", 1)[-1] if isinstance(tag, str) else ""
        if local in blocked:
            return True
        cur = cur.getparent() if hasattr(cur, "getparent") else None
    return False


def _filtered_export_nodes(root) -> list:
    candidates = []
    try:
        for node in root.iter():
            if node is root or _is_inside_non_rendered_svg_area(node):
                continue
            if _has_svg_filter_reference(node):
                # For PDF export we only pre-rasterize filters applied to bitmap content.
                # Basic opacity/vector content is left to the normal PDF exporter.
                if not _has_rasterizable_content(node):
                    continue
                candidates.append(node)
    except Exception:
        return []

    selected = []
    candidate_ids = {id(n) for n in candidates}
    for node in candidates:
        cur = node.getparent() if hasattr(node, "getparent") else None
        skip = False
        while cur is not None and cur is not root:
            if id(cur) in candidate_ids:
                skip = True
                break
            cur = cur.getparent() if hasattr(cur, "getparent") else None
        if not skip:
            selected.append(node)
    return selected


def _has_rasterizable_content(node) -> bool:
    try:
        for child in node.iter():
            local = str(getattr(child, "tag", "")).rsplit("}", 1)[-1]
            if local in {"image", "use"} and not _is_inside_non_rendered_svg_area(child):
                return True
    except Exception:
        pass
    return False


def _ensure_node_ids(nodes: list) -> list[str]:
    out = []
    used = set()
    for idx, node in enumerate(nodes, start=1):
        node_id = str(node.get("id") or "").strip()
        if not node_id or node_id in used:
            node_id = f"pnpink_filter_raster_{idx}"
            node.set("id", node_id)
        used.add(node_id)
        out.append(node_id)
    return out


def _safe_raster_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return safe[:120] or "node"


def _iter_rendered_images(node) -> list:
    out = []
    try:
        local = str(getattr(node, "tag", "")).rsplit("}", 1)[-1]
        if local == "image":
            out.append(node)
        for child in node.iter():
            if child is node:
                continue
            child_local = str(getattr(child, "tag", "")).rsplit("}", 1)[-1]
            if child_local == "image" and not _is_inside_non_rendered_svg_area(child):
                out.append(child)
    except Exception:
        return []
    return out


def _ensure_aux_node_id(node, prefix: str, idx: int, used: set[str]) -> str:
    node_id = str(node.get("id") or "").strip()
    if not node_id or node_id in used:
        node_id = f"{prefix}_{idx}"
        node.set("id", node_id)
    used.add(node_id)
    return node_id


def _adaptive_filter_raster_dpi(
    node,
    image_bboxes: dict[str, dict],
    source_svg_path: str,
    *,
    target_dpi: float = 300.0,
    min_dpi: float = 50.0,
    max_dpi: float = 600.0,
) -> int:
    import svg as SVG

    best_effective = 0.0
    for image in _iter_rendered_images(node):
        image_id = str(image.get("id") or "").strip()
        bbox = image_bboxes.get(image_id) or {}
        bw = float(bbox.get("width") or 0.0)
        bh = float(bbox.get("height") or 0.0)
        if bw <= 0 or bh <= 0:
            continue
        href = SVG.get_href(image)
        absref = image.get(SVG.SODI_ABSREF) or ""
        img_path = SVG._resolve_image_path(href, absref, source_svg_path)
        if not img_path:
            continue
        px = _bitmap_size_px(Path(img_path))
        if not px:
            continue
        dpi_x = float(px[0]) / (bw / 96.0)
        dpi_y = float(px[1]) / (bh / 96.0)
        best_effective = max(best_effective, min(dpi_x, dpi_y))

    if best_effective <= 0:
        return int(max_dpi)
    wanted = 2.0 * min(float(target_dpi), best_effective)
    clamped = max(float(min_dpi), min(float(max_dpi), wanted))
    return int(max(1, round(clamped)))


def _replace_node_with_raster_image(node, png_path: str, bbox: dict, *, dpi: float) -> None:
    import svg as SVG

    parent = node.getparent() if hasattr(node, "getparent") else None
    if parent is None:
        return
    old_id = str(node.get("id") or "").strip()
    x = float(bbox.get("x") or 0.0)
    y = float(bbox.get("y") or 0.0)
    w = max(0.001, float(bbox.get("width") or 0.0))
    h = max(0.001, float(bbox.get("height") or 0.0))

    png_size = _bitmap_size_px(Path(png_path))
    if png_size:
        rw = max(0.001, float(png_size[0]) * 96.0 / max(1.0, float(dpi or 600.0)))
        rh = max(0.001, float(png_size[1]) * 96.0 / max(1.0, float(dpi or 600.0)))
        x = x - max(0.0, rw - w) / 2.0
        y = y - max(0.0, rh - h) / 2.0
        w, h = rw, rh

    image = SVG.etree.Element(f"{{{SVG.NSS.get('svg', 'http://www.w3.org/2000/svg')}}}image")
    if old_id:
        image.set("id", old_id)
    image.set("x", f"{x:.6f}")
    image.set("y", f"{y:.6f}")
    image.set("width", f"{w:.6f}")
    image.set("height", f"{h:.6f}")
    image.set("preserveAspectRatio", "none")
    image.set("data-pnpink-rasterized-filter", "1")
    SVG.set_href(image, Path(png_path).resolve().as_uri(), touch_plain=True)
    try:
        image.set(SVG.SODI_ABSREF, str(Path(png_path).resolve()))
    except Exception:
        pass

    try:
        parent_ctm = parent.composed_transform()
        inv_parent = parent_ctm.inverse()
        if str(inv_parent).strip():
            image.set("transform", str(inv_parent))
    except Exception:
        pass

    idx = parent.index(node)
    parent.remove(node)
    parent.insert(idx, image)


def rasterize_filtered_nodes_for_export(
    doc,
    svg_path: str,
    inkscape_exe: str,
    env: dict[str, str],
    *,
    target_dpi: int = 300,
    max_raster_dpi: int = 600,
    max_workers: int = 3,
) -> dict:
    import svg as SVG

    root = doc.getroot()
    nodes = _filtered_export_nodes(root)
    _l.i("[raster] filtered node candidates=%d", len(nodes))
    if not nodes:
        return {"rasterized_filters": 0, "raster_dir": ""}

    node_ids = _ensure_node_ids(nodes)
    used_ids = set(node_ids)
    image_ids: set[str] = set()
    for node_idx, node in enumerate(nodes, start=1):
        for img_idx, image in enumerate(_iter_rendered_images(node), start=1):
            image_ids.add(_ensure_aux_node_id(image, f"pnpink_filter_image_{node_idx}_{img_idx}", img_idx, used_ids))

    try:
        doc.write(svg_path, encoding="utf-8", xml_declaration=True)
    except TypeError:
        doc.write(svg_path)

    all_bboxes = SVG.query_all(doc, set(node_ids) | image_ids, inkscape_bin=inkscape_exe, minimize_for_ids=False)
    bbox_by_id = {node_id: all_bboxes[node_id] for node_id in node_ids if node_id in all_bboxes}
    image_bboxes = {image_id: all_bboxes[image_id] for image_id in image_ids if image_id in all_bboxes}
    raster_dir = tempfile.mkdtemp(prefix="pnpink_filter_raster_")
    exe_dir = os.path.dirname(inkscape_exe) or None

    jobs = []
    node_by_id = {str(node.get("id") or ""): node for node in nodes}
    for node_id in node_ids:
        if node_id not in bbox_by_id:
            continue
        node = node_by_id.get(node_id)
        raster_dpi = _adaptive_filter_raster_dpi(
            node,
            image_bboxes,
            svg_path,
            target_dpi=float(target_dpi),
            min_dpi=50.0,
            max_dpi=float(max_raster_dpi),
        ) if node is not None else int(max_raster_dpi)
        png_path = os.path.join(raster_dir, f"{_safe_raster_filename(node_id)}.png")
        argv = INKSCAPE.build_export_id_png_argv(inkscape_exe, svg_path, node_id, png_path, dpi=raster_dpi)
        jobs.append((node_id, png_path, raster_dpi, argv))
    _l.i("[raster] export-id png jobs=%d ids=%s", len(jobs), ",".join(node_id for node_id, *_ in jobs[:12]))

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers or 1), len(jobs) or 1))) as pool:
        futs = {
            pool.submit(INKSCAPE.run, argv, exe_dir=exe_dir, env=env): (node_id, png_path, raster_dpi)
            for node_id, png_path, raster_dpi, argv in jobs
        }
        for fut, (node_id, png_path, raster_dpi) in futs.items():
            rc, msg = fut.result()
            ok = rc == 0 and os.path.isfile(png_path) and os.path.getsize(png_path) > 0
            results[node_id] = {"ok": ok, "png_path": png_path, "dpi": raster_dpi, "returncode": rc, "message": msg}

    replaced = 0
    used_dpis = []
    rasterized_ids = []
    for node_id in node_ids:
        item = results.get(node_id) or {}
        if not item.get("ok"):
            continue
        node = node_by_id.get(node_id)
        bbox = bbox_by_id.get(node_id)
        if node is None or not bbox:
            continue
        dpi = float(item.get("dpi") or max_raster_dpi)
        _replace_node_with_raster_image(node, str(item["png_path"]), bbox, dpi=dpi)
        used_dpis.append(int(dpi))
        rasterized_ids.append(node_id)
        replaced += 1

    failures = [
        f"{node_id}: rc={item.get('returncode')} {str(item.get('message') or '').strip()[:300]}"
        for node_id, item in results.items()
        if not item.get("ok")
    ]
    if replaced:
        _l.i("[raster] replaced=%d ids=%s", replaced, ",".join(rasterized_ids[:12]))
    if failures:
        _l.w("[raster] failures=%d first=%s", len(failures), failures[0])
    return {
        "rasterized_filters": replaced,
        "raster_filter_candidates": len(nodes),
        "raster_dir": raster_dir,
        "raster_failures": failures,
        "raster_dpis": used_dpis,
        "raster_ids": rasterized_ids,
    }
