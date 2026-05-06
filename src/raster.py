# -*- coding: utf-8 -*-
"""Rasterization helpers for SVG export pre-processing."""

from __future__ import annotations

import os
import re
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import inkscape_cli as INKSCAPE
import prefs
import log as LOG
import temp_paths as TEMPPATHS

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


def _is_effectively_hidden(node) -> bool:
    cur = node
    while cur is not None:
        try:
            if str(cur.get("display") or "").strip().lower() == "none":
                return True
            if str(cur.get("visibility") or "").strip().lower() == "hidden":
                return True
            style = str(cur.get("style") or "").lower()
            if "display:none" in style or "visibility:hidden" in style:
                return True
        except Exception:
            pass
        cur = cur.getparent() if hasattr(cur, "getparent") else None
    return False


def _local_name(node) -> str:
    try:
        return str(getattr(node, "tag", "")).rsplit("}", 1)[-1]
    except Exception:
        return ""


def _find_node_by_id(root, node_id: str):
    try:
        for node in root.iter():
            if str(node.get("id") or "").strip() == str(node_id or "").strip():
                return node
    except Exception:
        return None
    return None


def _set_display_none(node) -> None:
    import svg as SVG

    try:
        styles = SVG.style_map(node)
        styles["display"] = "none"
        SVG.style_set(node, styles)
    except Exception:
        st = str(node.get("style") or "").strip()
        if st and not st.endswith(";"):
            st += ";"
        node.set("style", st + "display:none")


def _href_fragment(node) -> str:
    try:
        href = (
            node.get("href")
            or node.get("{http://www.w3.org/1999/xlink}href")
            or ""
        )
        href = str(href).strip()
        return href[1:] if href.startswith("#") else ""
    except Exception:
        return ""


def _collect_hidden_template_image_ids(root) -> set[str]:
    hidden_ids: set[str] = set()
    try:
        for node in root.iter():
            if _local_name(node) != "image":
                continue
            node_id = str(node.get("id") or "").strip()
            if not node_id or "_pnp" in node_id:
                continue
            if _is_effectively_hidden(node):
                hidden_ids.add(node_id)
    except Exception:
        pass
    return hidden_ids


def _node_matches_hidden_template_image(node, hidden_template_image_ids: set[str]) -> bool:
    if not hidden_template_image_ids:
        return False
    try:
        for child in node.iter():
            local = _local_name(child)
            if local not in {"image", "use"}:
                continue
            node_id = str(child.get("id") or "").strip()
            if node_id in hidden_template_image_ids:
                return True
            href_id = _href_fragment(child)
            if href_id in hidden_template_image_ids:
                return True
    except Exception:
        return False
    return False


def _find_instance_root(node, root):
    cur = node
    best = None
    while cur is not None and cur is not root:
        try:
            node_id = str(cur.get("id") or "")
            if (
                str(cur.get("data-pnpink-row-index") or "").strip()
                or str(cur.get("data-pnpink-dataset-index") or "").strip()
                or node_id.startswith("dm_card_")
            ):
                best = cur
                break
        except Exception:
            pass
        cur = cur.getparent() if hasattr(cur, "getparent") else None
    return best


def _z_path_from(root_node, node) -> tuple[int, ...]:
    path = []
    cur = node
    while cur is not None and cur is not root_node:
        parent = cur.getparent() if hasattr(cur, "getparent") else None
        if parent is None:
            return tuple(path)
        try:
            path.append(list(parent).index(cur))
        except Exception:
            path.append(-1)
        cur = parent
    path.reverse()
    return tuple(path)


def _hide_nodes_above_export_target(export_node, stop_node) -> None:
    cur = export_node
    while cur is not None and cur is not stop_node:
        parent = cur.getparent() if hasattr(cur, "getparent") else None
        if parent is None:
            break
        try:
            children = list(parent)
            idx = children.index(cur)
        except Exception:
            children = []
            idx = -1
        for sibling in children[idx + 1:]:
            _set_display_none(sibling)
        if parent is stop_node:
            break
        cur = parent


def _hide_hidden_template_images(root, hidden_template_image_ids: set[str]) -> None:
    if not hidden_template_image_ids:
        return
    try:
        for node in root.iter():
            local = _local_name(node)
            if local not in {"image", "use"}:
                continue
            node_id = str(node.get("id") or "").strip()
            href_id = _href_fragment(node)
            if node_id in hidden_template_image_ids or href_id in hidden_template_image_ids:
                _set_display_none(node)
    except Exception:
        pass


def _build_area_export_pass_svg(doc, export_node_ids: list[str], hidden_template_image_ids: set[str]) -> str | None:
    import svg as SVG

    try:
        root_copy = deepcopy(doc.getroot())
        tree = SVG.etree.ElementTree(root_copy)
    except Exception:
        return None

    _hide_hidden_template_images(root_copy, hidden_template_image_ids)
    found = False
    for export_node_id in export_node_ids:
        export_node = _find_node_by_id(root_copy, export_node_id)
        if export_node is None:
            continue
        instance_root = _find_instance_root(export_node, root_copy) or root_copy
        _hide_nodes_above_export_target(export_node, instance_root)
        found = True
    if not found:
        return None

    try:
        return SVG._write_temp_svg(tree)
    except Exception:
        return None


def _filtered_export_nodes(root, hidden_template_image_ids: set[str] | None = None) -> list:
    hidden_template_image_ids = hidden_template_image_ids or set()
    candidates = []
    try:
        for node in root.iter():
            if node is root or _is_inside_non_rendered_svg_area(node):
                continue
            if _is_effectively_hidden(node):
                continue
            if _has_svg_filter_reference(node):
                # For PDF export we only pre-rasterize filters applied to bitmap content.
                # Basic opacity/vector content is left to the normal PDF exporter.
                if not _has_rasterizable_content(node):
                    continue
                if _node_matches_hidden_template_image(node, hidden_template_image_ids):
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
            if local in {"image", "use"} and not _is_inside_non_rendered_svg_area(child) and not _is_effectively_hidden(child):
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
        if local == "image" and not _is_effectively_hidden(node):
            out.append(node)
        for child in node.iter():
            if child is node:
                continue
            child_local = str(getattr(child, "tag", "")).rsplit("}", 1)[-1]
            if child_local == "image" and not _is_inside_non_rendered_svg_area(child) and not _is_effectively_hidden(child):
                out.append(child)
    except Exception:
        return []
    return out


def _count_ready_outputs(paths: list[str]) -> int:
    ready = 0
    for path in paths:
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                ready += 1
        except Exception:
            pass
    return ready


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


def _replace_node_with_raster_image(node, raster_path: str, bbox: dict, *, dpi: float) -> None:
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
    try:
        SVG.set_file_href(image, raster_path, touch_plain=True, touch_absref=True)
    except Exception:
        pass

    idx = parent.index(node)
    parent.remove(node)
    parent.insert(idx, image)


def _bbox_to_export_area(bbox: dict) -> tuple[float, float, float, float] | None:
    try:
        x = float(bbox.get("x") or 0.0)
        y = float(bbox.get("y") or 0.0)
        w = float(bbox.get("width") or 0.0)
        h = float(bbox.get("height") or 0.0)
    except Exception:
        return None
    if w <= 0.0 or h <= 0.0:
        return None
    return (x, y, x + w, y + h)


def _convert_png_to_jpeg(src_png: str, dst_jpg: str, *, matte_color: str = "#ffffff", quality: int = 95) -> bool:
    try:
        from PIL import Image
    except Exception:
        return False

    color = str(matte_color or "#ffffff").strip() or "#ffffff"
    try:
        with Image.open(src_png) as im:
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, color)
            composited = Image.alpha_composite(bg, rgba).convert("RGB")
            composited.save(dst_jpg, format="JPEG", quality=int(quality), optimize=True)
        return os.path.isfile(dst_jpg) and os.path.getsize(dst_jpg) > 0
    except Exception:
        return False


def _flatten_png_to_opaque(src_png: str, dst_png: str, *, matte_color: str = "#ffffff") -> bool:
    try:
        from PIL import Image
    except Exception:
        return False

    color = str(matte_color or "#ffffff").strip() or "#ffffff"
    try:
        with Image.open(src_png) as im:
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, color)
            composited = Image.alpha_composite(bg, rgba)
            out_path = dst_png
            tmp_path = ""
            if os.path.normcase(os.path.normpath(src_png)) == os.path.normcase(os.path.normpath(dst_png)):
                tmp_path = str(Path(dst_png).with_suffix(".opaque.png"))
                out_path = tmp_path
            composited.save(out_path, format="PNG")
        if tmp_path:
            try:
                if os.path.isfile(dst_png):
                    os.remove(dst_png)
            except Exception:
                pass
            os.replace(tmp_path, dst_png)
        return os.path.isfile(dst_png) and os.path.getsize(dst_png) > 0
    except Exception:
        return False


def _build_area_export_passes(root, export_nodes: list, export_node_ids: list[str]) -> list[list[str]]:
    by_instance: dict[int, list[tuple[tuple[int, ...], str]]] = {}
    for node, node_id in zip(export_nodes, export_node_ids):
        instance_root = _find_instance_root(node, root) or root
        by_instance.setdefault(id(instance_root), []).append((_z_path_from(instance_root, node), node_id))

    sorted_by_instance = []
    for items in by_instance.values():
        # Later siblings paint above earlier siblings in SVG, so export top-most nodes first.
        sorted_by_instance.append([node_id for _path, node_id in sorted(items, key=lambda item: item[0], reverse=True)])

    passes: list[list[str]] = []
    max_len = max((len(items) for items in sorted_by_instance), default=0)
    for idx in range(max_len):
        pass_ids = [items[idx] for items in sorted_by_instance if idx < len(items)]
        if pass_ids:
            passes.append(pass_ids)
    return passes


def _prepare_area_raster_work_items(doc, export_nodes: list, export_node_ids: list[str], bbox_by_id: dict, raster_dir: str, output_format: str, hidden_template_image_ids: set[str]) -> tuple[list[tuple], dict[str, object], set[str]]:
    exports: list[tuple] = []
    prepared_nodes: dict[str, object] = {}
    temp_svgs: set[str] = set()
    ext = ".jpg" if output_format == "jpeg" else ".png"
    passes = _build_area_export_passes(doc.getroot(), export_nodes, export_node_ids)
    for pass_index, pass_ids in enumerate(passes, start=1):
        work_svg_path = _build_area_export_pass_svg(doc, pass_ids, hidden_template_image_ids) or ""
        if not work_svg_path:
            continue
        temp_svgs.add(work_svg_path)
        for export_node_id in pass_ids:
            bbox = bbox_by_id.get(export_node_id)
            export_area = _bbox_to_export_area(bbox or {})
            if not export_area:
                continue
            raster_path = os.path.join(raster_dir, f"{_safe_raster_filename(export_node_id)}{ext}")
            exports.append((pass_index, work_svg_path, export_area, raster_path, export_node_id))
            prepared_nodes[export_node_id] = bbox
    return exports, prepared_nodes, temp_svgs


def _rasterize_filtered_nodes_png_alpha(
    doc,
    svg_path: str,
    inkscape_exe: str,
    env: dict[str, str],
    *,
    nodes: list,
    progress_callback=None,
    target_dpi: int,
    max_raster_dpi: int,
    max_workers: int,
) -> dict:
    import svg as SVG

    node_ids = _ensure_node_ids(nodes)
    root = doc.getroot()
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
    raster_dir = TEMPPATHS.make_work_dir("filter_raster", stem=Path(svg_path).stem)
    exe_dir = os.path.dirname(inkscape_exe) or None
    png_antialias = int(prefs.get_export_png_antialias(2))
    png_use_dithering = False
    background_color = str(prefs.get_export_png_background("#ffffff"))
    background_opacity = str(prefs.get_export_png_background_opacity("0.0"))

    jobs = []
    node_by_id = {str(node.get("id") or ""): node for node in nodes}
    replace_to_export = {str(node.get("id") or ""): str(export.get("id") or "") for node, export in zip(nodes, export_nodes)}
    for node_id in node_ids:
        export_node_id = replace_to_export.get(node_id) or node_id
        if export_node_id not in bbox_by_id:
            continue
        raster_dpi = int(max_raster_dpi)
        png_path = os.path.join(raster_dir, f"{_safe_raster_filename(node_id)}.png")
        argv = INKSCAPE.build_export_id_png_argv(
            inkscape_exe,
            svg_path,
            export_node_id,
            png_path,
            dpi=raster_dpi,
            png_antialias=png_antialias,
            png_use_dithering=png_use_dithering,
            background_color=background_color,
            background_opacity=background_opacity,
        )
        jobs.append((node_id, export_node_id, png_path, raster_dpi, argv))
    _l.i(
        "[raster] pipeline=png_alpha jobs=%d antialias=%d dithering=%s background=%s background_opacity=%s ids=%s",
        len(jobs),
        png_antialias,
        "false",
        background_color,
        background_opacity,
        ",".join(node_id for node_id, *_ in jobs[:12]),
    )
    if not jobs:
        return {"rasterized_filters": 0, "raster_dir": raster_dir}

    results = {}
    worker_groups = _partition_jobs(jobs, max(1, min(int(max_workers or 1), len(jobs) or 1)))
    with ThreadPoolExecutor(max_workers=len(worker_groups)) as pool:
        futs = {}
        for group in worker_groups:
            exports = [(export_node_id, png_path, raster_dpi) for _node_id, export_node_id, png_path, raster_dpi, _argv in group]
            commands = INKSCAPE.build_shell_export_id_png_commands(
                svg_path,
                exports,
                png_antialias=png_antialias,
                png_use_dithering=png_use_dithering,
                background_color=background_color,
                background_opacity=background_opacity,
            )
            fut = pool.submit(
                INKSCAPE.run_shell_commands,
                inkscape_exe,
                commands,
                exe_dir=exe_dir,
                env=env,
            )
            futs[fut] = list(group)
        jobs_total = len(jobs)
        last_progress = -1
        while True:
            done_count = _count_ready_outputs([png_path for _node_id, _export_node_id, png_path, _raster_dpi, _argv in jobs])
            if progress_callback is not None and done_count != last_progress:
                try:
                    progress_callback(done_count, jobs_total)
                except Exception:
                    pass
                last_progress = done_count
            done_futs, pending_futs = wait(list(futs.keys()), timeout=0.15)
            if not pending_futs:
                break
        for fut, group in futs.items():
            rc, msg = fut.result()
            for node_id, export_node_id, png_path, raster_dpi, _argv in group:
                ok = rc == 0 and os.path.isfile(png_path) and os.path.getsize(png_path) > 0
                results[node_id] = {
                    "ok": ok,
                    "raster_path": png_path,
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
        _replace_node_with_raster_image(node, str(item["raster_path"]), bbox, dpi=dpi)
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


def _rasterize_filtered_nodes_area(
    doc,
    svg_path: str,
    inkscape_exe: str,
    env: dict[str, str],
    *,
    output_format: str,
    nodes: list,
    progress_callback=None,
    target_dpi: int,
    max_raster_dpi: int,
    max_workers: int,
) -> dict:
    import svg as SVG

    output_format = str(output_format or "png").strip().lower()
    ext = ".jpg" if output_format == "jpeg" else ".png"
    pipeline_name = "jpeg" if output_format == "jpeg" else "png"

    root = doc.getroot()
    hidden_template_image_ids = _collect_hidden_template_image_ids(root)
    promoted_nodes = [_promote_filter_raster_target(node, root) for node in nodes]
    promoted_ids = _ensure_node_ids(promoted_nodes)
    export_nodes = []
    export_node_ids = []
    export_ids_seen = set()
    for export_node, export_id in zip(promoted_nodes, promoted_ids):
        if export_id in export_ids_seen:
            continue
        export_nodes.append(export_node)
        export_node_ids.append(export_id)
        export_ids_seen.add(export_id)

    try:
        doc.write(svg_path, encoding="utf-8", xml_declaration=True)
    except TypeError:
        doc.write(svg_path)

    all_bboxes = SVG.query_all(doc, set(export_node_ids), inkscape_bin=inkscape_exe, minimize_for_ids=False)
    bbox_by_id = {node_id: all_bboxes[node_id] for node_id in export_node_ids if node_id in all_bboxes}
    raster_dir = TEMPPATHS.make_work_dir("filter_raster_area", stem=Path(svg_path).stem)
    exe_dir = os.path.dirname(inkscape_exe) or None
    background_color = str(prefs.get_export_png_background("#ffffff"))
    export_node_by_id = {str(node.get("id") or ""): node for node in export_nodes}
    prepared_exports, _prepared_boxes, temp_svgs = _prepare_area_raster_work_items(
        doc,
        export_nodes,
        export_node_ids,
        bbox_by_id,
        raster_dir,
        output_format,
        hidden_template_image_ids,
    )
    jobs = [
        (pass_i, work_svg_i, area_i, raster_path_i, export_node_id_i, int(max_raster_dpi))
        for (pass_i, work_svg_i, area_i, raster_path_i, export_node_id_i) in prepared_exports
    ]
    _l.i(
        "[raster] pipeline=%s jobs=%d passes=%d background=%s ids=%s",
        pipeline_name,
        len(jobs),
        len({pass_i for pass_i, *_rest in jobs}),
        background_color,
        ",".join(export_node_id for *_a, export_node_id, _dpi in jobs[:12]),
    )
    if not jobs:
        return {"rasterized_filters": 0, "raster_dir": raster_dir}

    results = {}
    jobs_total = len(jobs)
    progress_paths = [
        raster_path_i if output_format == "png" else str(Path(raster_path_i).with_suffix(".png"))
        for _pass_i, _work_svg_i, _area_i, raster_path_i, _export_node_id_i, _dpi_i in jobs
    ]
    last_progress = -1
    jobs_by_pass: dict[int, list[tuple]] = {}
    for job in jobs:
        jobs_by_pass.setdefault(int(job[0]), []).append(job)
    try:
        for pass_i in sorted(jobs_by_pass):
            pass_jobs = jobs_by_pass[pass_i]
            worker_groups = _partition_jobs(pass_jobs, max(1, min(int(max_workers or 1), len(pass_jobs) or 1)))
            with ThreadPoolExecutor(max_workers=len(worker_groups)) as pool:
                futs = {}
                for group in worker_groups:
                    shell_exports = [
                        (
                            work_svg_i,
                            area_i,
                            raster_path_i if output_format == "png" else str(Path(raster_path_i).with_suffix(".png")),
                            dpi_i,
                        )
                        for _pass_i, work_svg_i, area_i, raster_path_i, _export_node_id_i, dpi_i in group
                    ]
                    commands = INKSCAPE.build_shell_export_area_png_commands(shell_exports)
                    fut = pool.submit(
                        INKSCAPE.run_shell_commands,
                        inkscape_exe,
                        commands,
                        exe_dir=exe_dir,
                        env=env,
                    )
                    futs[fut] = list(group)
                while True:
                    done_count = _count_ready_outputs(progress_paths)
                    if progress_callback is not None and done_count != last_progress:
                        try:
                            progress_callback(done_count, jobs_total)
                        except Exception:
                            pass
                        last_progress = done_count
                    _done_futs, pending_futs = wait(list(futs.keys()), timeout=0.15)
                    if not pending_futs:
                        break
                for fut, group in futs.items():
                    rc, msg = fut.result()
                    for _pass_i, _work_svg_i, _area_i, raster_path_i, export_node_id_i, dpi_i in group:
                        exported_png = raster_path_i if output_format == "png" else str(Path(raster_path_i).with_suffix(".png"))
                        ok = rc == 0 and os.path.isfile(exported_png) and os.path.getsize(exported_png) > 0
                        convert_ok = ok
                        if ok and output_format == "jpeg":
                            convert_ok = _convert_png_to_jpeg(exported_png, raster_path_i, matte_color=background_color)
                        elif ok and output_format == "png":
                            convert_ok = _flatten_png_to_opaque(exported_png, raster_path_i, matte_color=background_color)
                        try:
                            if output_format == "jpeg" and os.path.isfile(exported_png):
                                os.remove(exported_png)
                        except Exception:
                            pass
                        results[export_node_id_i] = {
                            "ok": bool(ok and convert_ok),
                            "raster_path": raster_path_i,
                            "dpi": dpi_i,
                            "returncode": rc,
                            "message": msg if ok or convert_ok else (msg or "Raster conversion failed"),
                            "export_node_id": export_node_id_i,
                        }
        done_count = _count_ready_outputs(progress_paths)
        if progress_callback is not None and done_count != last_progress:
            try:
                progress_callback(done_count, jobs_total)
            except Exception:
                pass
    finally:
        for temp_svg in temp_svgs:
            try:
                if os.path.isfile(temp_svg):
                    os.remove(temp_svg)
            except Exception:
                pass

    replaced = 0
    used_dpis = []
    rasterized_ids = []
    for export_node_id in export_node_ids:
        item = results.get(export_node_id) or {}
        if not item.get("ok"):
            continue
        node = export_node_by_id.get(export_node_id)
        bbox = bbox_by_id.get(export_node_id)
        if node is None or not bbox:
            continue
        dpi = float(item.get("dpi") or max_raster_dpi)
        _replace_node_with_raster_image(node, str(item["raster_path"]), bbox, dpi=dpi)
        used_dpis.append(int(dpi))
        rasterized_ids.append(export_node_id)
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


def rasterize_filtered_nodes_for_export(
    doc,
    svg_path: str,
    inkscape_exe: str,
    env: dict[str, str],
    *,
    pipeline: str | None = None,
    progress_callback=None,
    target_dpi: int = 300,
    max_raster_dpi: int = 600,
    max_workers: int = 3,
) -> dict:
    root = doc.getroot()
    hidden_template_image_ids = _collect_hidden_template_image_ids(root)
    nodes = _filtered_export_nodes(root, hidden_template_image_ids)
    _l.i("[raster] filtered node candidates=%d", len(nodes))
    if not nodes:
        return {"rasterized_filters": 0, "raster_dir": ""}
    pipeline = str(pipeline or prefs.get_pdf_raster_mode("png")).strip().lower()
    _l.i("[raster] selected pipeline=%s", pipeline)
    if pipeline == "png_alpha":
        return _rasterize_filtered_nodes_png_alpha(
            doc,
            svg_path,
            inkscape_exe,
            env,
            nodes=nodes,
            progress_callback=progress_callback,
            target_dpi=target_dpi,
            max_raster_dpi=max_raster_dpi,
            max_workers=max_workers,
        )
    if pipeline == "png":
        return _rasterize_filtered_nodes_area(
            doc,
            svg_path,
            inkscape_exe,
            env,
            output_format="png",
            nodes=nodes,
            progress_callback=progress_callback,
            target_dpi=target_dpi,
            max_raster_dpi=max_raster_dpi,
            max_workers=max_workers,
        )
    return _rasterize_filtered_nodes_area(
        doc,
        svg_path,
        inkscape_exe,
        env,
        output_format="jpeg",
        nodes=nodes,
        progress_callback=progress_callback,
        target_dpi=target_dpi,
        max_raster_dpi=max_raster_dpi,
        max_workers=max_workers,
    )
