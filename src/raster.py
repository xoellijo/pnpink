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


def _partition_jobs(items: list[tuple], parts: int) -> list[list[tuple]]:
    n = max(1, int(parts or 1))
    groups: list[list[tuple]] = [[] for _ in range(n)]
    for idx, item in enumerate(items):
        groups[idx % n].append(item)
    return [group for group in groups if group]


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
        mask_val = str(node.get("mask") or "").strip()
        if mask_val and mask_val.lower() != "none":
            return True
        style = str(node.get("style") or "")
        for part in style.split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            key = key.strip().lower()
            value = value.strip().lower()
            if key in {"filter", "mask"} and value not in ("", "none"):
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
    selected_ids = set()
    candidate_ids = {id(_promote_filter_raster_target(n, root)) for n in candidates}
    for node in candidates:
        if node is None or id(node) in selected_ids:
            continue
        promoted = _promote_filter_raster_target(node, root)
        cur = promoted.getparent() if hasattr(promoted, "getparent") else None
        skip = False
        while cur is not None and cur is not root:
            if id(cur) in candidate_ids:
                skip = True
                break
            cur = cur.getparent() if hasattr(cur, "getparent") else None
        if not skip:
            selected.append(node)
            selected_ids.add(id(node))
    return selected


def _promote_filter_raster_target(node, root):
    cur = node
    best = node
    while cur is not None and cur is not root:
        node_id = str(cur.get("id") or "").strip()
        if node_id.startswith("fa_clipwrap_"):
            best = cur
            break
        parent = cur.getparent() if hasattr(cur, "getparent") else None
        if parent is None or parent is root:
            break
        cur = parent
    return best


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
    x = y = 0.0
    w = h = 0.0
    fx_rect = str(node.get("data-fx-rect") or "").strip()
    if fx_rect:
        try:
            vals = [float(part) for part in fx_rect.replace(",", " ").split()[:4]]
            if len(vals) == 4:
                x, y, w, h = vals
        except Exception:
            x = y = 0.0
            w = h = 0.0
    if w <= 0.0 or h <= 0.0:
        x = float(bbox.get("x") or 0.0)
        y = float(bbox.get("y") or 0.0)
        w = max(0.001, float(bbox.get("width") or 0.0))
        h = max(0.001, float(bbox.get("height") or 0.0))
        try:
            parent_ctm = parent.composed_transform()
        except Exception:
            parent_ctm = None
        try:
            inv_parent = parent_ctm.inverse() if parent_ctm is not None else None
        except Exception:
            inv_parent = None
        if inv_parent is not None:
            p1 = inv_parent.apply_to_point((x, y))
            p2 = inv_parent.apply_to_point((x + w, y))
            p3 = inv_parent.apply_to_point((x, y + h))
            p4 = inv_parent.apply_to_point((x + w, y + h))
            xs = [p1[0], p2[0], p3[0], p4[0]]
            ys = [p1[1], p2[1], p3[1], p4[1]]
            x = float(min(xs))
            y = float(min(ys))
            w = max(0.001, float(max(xs) - min(xs)))
            h = max(0.001, float(max(ys) - min(ys)))
    w = max(0.001, float(w))
    h = max(0.001, float(h))

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
    export_nodes = [_promote_filter_raster_target(node, root) for node in nodes]
    export_node_ids = _ensure_node_ids(export_nodes)
    used_ids = set(node_ids) | set(export_node_ids)
    image_ids: set[str] = set()
    for node_idx, node in enumerate(export_nodes, start=1):
        for img_idx, image in enumerate(_iter_rendered_images(node), start=1):
            image_ids.add(_ensure_aux_node_id(image, f"pnpink_filter_image_{node_idx}_{img_idx}", img_idx, used_ids))

    try:
        doc.write(svg_path, encoding="utf-8", xml_declaration=True)
    except TypeError:
        doc.write(svg_path)

    all_bboxes = SVG.query_all(doc, set(export_node_ids) | image_ids, inkscape_bin=inkscape_exe, minimize_for_ids=False)
    bbox_by_id = {node_id: all_bboxes[node_id] for node_id in export_node_ids if node_id in all_bboxes}
    image_bboxes = {image_id: all_bboxes[image_id] for image_id in image_ids if image_id in all_bboxes}
    raster_dir = tempfile.mkdtemp(prefix="pnpink_filter_raster_")
    exe_dir = os.path.dirname(inkscape_exe) or None

    jobs = []
    node_by_id = {str(node.get("id") or ""): node for node in nodes}
    export_node_by_id = {str(node.get("id") or ""): node for node in export_nodes}
    replace_to_export = {str(node.get("id") or ""): str(export.get("id") or "") for node, export in zip(nodes, export_nodes)}
    for node_id in node_ids:
        export_node_id = replace_to_export.get(node_id) or node_id
        if export_node_id not in bbox_by_id:
            continue
        node = export_node_by_id.get(export_node_id)
        raster_dpi = int(max_raster_dpi)
        png_path = os.path.join(raster_dir, f"{_safe_raster_filename(node_id)}.png")
        argv = INKSCAPE.build_export_id_png_argv(inkscape_exe, svg_path, export_node_id, png_path, dpi=raster_dpi)
        jobs.append((node_id, export_node_id, png_path, raster_dpi, argv))
    _l.i("[raster] export-id png jobs=%d ids=%s", len(jobs), ",".join(node_id for node_id, *_ in jobs[:12]))
    if not jobs:
        return {"rasterized_filters": 0, "raster_dir": raster_dir}

    results = {}
    worker_groups = _partition_jobs(jobs, max(1, min(int(max_workers or 1), len(jobs) or 1)))
    with ThreadPoolExecutor(max_workers=len(worker_groups)) as pool:
        futs = {}
        for group in worker_groups:
            exports = [(export_node_id, png_path, raster_dpi) for _node_id, export_node_id, png_path, raster_dpi, _argv in group]
            commands = INKSCAPE.build_shell_export_id_png_commands(svg_path, exports)
            fut = pool.submit(
                INKSCAPE.run_shell_commands,
                inkscape_exe,
                commands,
                exe_dir=exe_dir,
                env=env,
            )
            futs[fut] = list(group)
        for fut, group in futs.items():
            rc, msg = fut.result()
            for node_id, export_node_id, png_path, raster_dpi, _argv in group:
                ok = rc == 0 and os.path.isfile(png_path) and os.path.getsize(png_path) > 0
                results[node_id] = {
                    "ok": ok,
                    "png_path": png_path,
                    "dpi": raster_dpi,
                    "returncode": rc,
                    "message": msg,
                    "export_node_id": export_node_id,
                }

    replaced = 0
    used_dpis = []
    rasterized_ids = []
    for node_id in node_ids:
        item = results.get(node_id) or {}
        if not item.get("ok"):
            continue
        node = node_by_id.get(node_id)
        bbox = bbox_by_id.get(str(item.get("export_node_id") or ""))
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
