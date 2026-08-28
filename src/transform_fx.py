# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import re
from copy import deepcopy
from typing import Iterable

import inkex
import gradients as GRD
import log as LOG
import svg as SVG

try:
    import dsl as DSL
except Exception:
    DSL = None

_l = LOG


def merge_specs(specs: Iterable[object]) -> object | None:
    items = [s for s in (specs or []) if s is not None]
    if not items:
        return None
    if DSL is None:
        return items[-1]
    out = DSL.TransformSpec()
    for sp in items:
        if getattr(sp, "rotate", None) not in (None, 0, 0.0):
            out.rotate = (out.rotate or 0.0) + float(getattr(sp, "rotate") or 0.0)
        if getattr(sp, "mirror", None):
            out.mirror = str(getattr(sp, "mirror") or "").strip().lower()
        if getattr(sp, "opacity", None):
            out.opacity = str(getattr(sp, "opacity") or "").strip()
        if getattr(sp, "scale", None):
            out.scale = [str(v).strip() for v in (getattr(sp, "scale") or []) if str(v).strip()]
        if getattr(sp, "soft", None):
            out.soft = [str(v).strip() for v in (getattr(sp, "soft") or []) if str(v).strip()]
        if getattr(sp, "filter_ref", None):
            out.filter_ref = str(getattr(sp, "filter_ref") or "").strip()
        if getattr(sp, "text", None) is not None:
            out.text = [str(v) for v in (getattr(sp, "text") or [])]
        if getattr(sp, "inside", None):
            out.inside = str(getattr(sp, "inside") or "").strip().lower()
    return out


def has_text(spec) -> bool:
    return bool(spec is not None and getattr(spec, "text", None) is not None)


def has_inside(spec) -> bool:
    return bool(spec is not None and getattr(spec, "inside", None) in ("x", "y", "a"))


def _shape_inside_id(node) -> str:
    style = str(node.get("style") or "")
    match = re.search(r"shape-inside\s*:\s*url\(\s*#([^)]+)\s*\)", style, re.IGNORECASE)
    return (match.group(1) or "").strip() if match else ""


def shape_inside_dependency_ids(scope, target_ids) -> set[str]:
    dependencies = set()
    if scope is None:
        return dependencies
    for target_id in target_ids or ():
        node = SVG.find_target_exact_in(scope, str(target_id or "").strip())
        if node is None:
            continue
        frame_id = _shape_inside_id(node)
        if frame_id:
            dependencies.add(frame_id)
    return dependencies


_RELATED_TARGET_RE = re.compile(
    r"^(?P<id>[A-Za-z_][-A-Za-z0-9_:.]*)(?:\[(?P<relation>[A-Za-z_][A-Za-z0-9_-]*)\])?$",
    re.IGNORECASE,
)


def split_related_target(reference: str) -> tuple[str, str]:
    match = _RELATED_TARGET_RE.fullmatch(str(reference or "").strip())
    if not match:
        return str(reference or "").strip(), ""
    return (match.group("id") or "").strip(), (match.group("relation") or "").strip().lower()


def _rewrite_shape_inside_id(text_node, frame_id: str) -> bool:
    style = str(text_node.get("style") or "")
    match = re.search(r"shape-inside\s*:\s*url\(\s*#([^)]+)\s*\)", style, re.IGNORECASE)
    if not match:
        return False
    text_node.set("style", style[:match.start(1)] + str(frame_id) + style[match.end(1):])
    return True


def _inside_defs(node) -> bool:
    current = node
    while current is not None:
        if str(getattr(current, "tag", "") or "").endswith("defs"):
            return True
        current = current.getparent() if hasattr(current, "getparent") else None
    return False


def _move_shape_source_to_defs(frame_source) -> None:
    parent = frame_source.getparent() if hasattr(frame_source, "getparent") else None
    if parent is None or _inside_defs(frame_source):
        return
    defs = next(
        (child for child in list(parent) if str(getattr(child, "tag", "") or "").endswith("defs")),
        None,
    )
    if defs is None:
        defs = SVG.etree.SubElement(parent, inkex.addNS("defs", "svg"))
    parent.remove(frame_source)
    frame_source.set("data-dm-shape-inside-visible-source", "1")
    defs.append(frame_source)


def ensure_private_shape_inside(root, scope, text_node):
    """Return an instance-private shape-inside frame, its owner, and the text."""
    if root is None or text_node is None:
        return None, None, text_node
    frame_id = _shape_inside_id(text_node)
    if not frame_id:
        return None, None, text_node

    current = text_node.getparent() if hasattr(text_node, "getparent") else None
    while current is not None:
        if current.get("data-dm-shape-inside-owner") == "1":
            frame = SVG.find_id(current, frame_id, include_defs=True)
            if frame is not None:
                return frame, current, text_node
        if current is scope:
            break
        current = current.getparent() if hasattr(current, "getparent") else None

    frame_source = SVG.find_id(scope, frame_id, include_defs=True) if scope is not None else None
    if frame_source is None and scope is not None:
        frame_source = SVG.find_target_exact_in(scope, frame_id)
    if frame_source is None:
        frame_source = SVG.find_id(root, frame_id, include_defs=True)
    if frame_source is None:
        frame_source = SVG.find_target_exact_in(root, frame_id)
    if frame_source is None or not str(getattr(frame_source, "tag", "") or "").endswith("rect"):
        return None, None, text_node
    parent = text_node.getparent() if hasattr(text_node, "getparent") else None
    if parent is None:
        return None, None, text_node

    text_key = str(text_node.get("data-origid") or text_node.get("id") or "text").strip()
    frame_key = str(frame_source.get("data-origid") or frame_source.get("id") or "frame").strip()
    try:
        private_id = root.get_unique_id(f"dm_shape_{text_key}_{frame_key}")
        owner_id = root.get_unique_id(f"dm_shape_owner_{text_key}")
    except Exception:
        private_id = f"dm_shape_{text_key}_{frame_key}_{id(text_node)}"
        owner_id = f"dm_shape_owner_{text_key}_{id(text_node)}"

    owner = inkex.Group()
    owner.set("id", owner_id)
    owner.set("data-dm-shape-inside-owner", "1")
    source_was_visible = frame_source.get("data-dm-shape-inside-visible-source") == "1" or not _inside_defs(frame_source)
    frame = deepcopy(frame_source)
    frame.set("id", private_id)
    frame.set("data-origid", frame_key)
    frame.set("data-dm-shape-inside-private", "1")
    frame.attrib.pop("data-dm-shape-inside-visible-source", None)
    try:
        GRD.normalize_user_space_gradients(
            root,
            frame,
            (
                float(frame.get("x") or 0.0),
                float(frame.get("y") or 0.0),
                float(frame.get("width") or 0.0),
                float(frame.get("height") or 0.0),
            ),
        )
    except Exception as ex:
        _l.w(f"[transform.inside] gradient normalization failed frame='{frame_key}': {ex}")
    if source_was_visible:
        owner.append(frame)
    else:
        defs = SVG.etree.SubElement(owner, inkex.addNS("defs", "svg"))
        defs.append(frame)

    index = parent.index(text_node)
    parent.remove(text_node)
    parent.insert(index, owner)
    owner.append(text_node)
    if not _rewrite_shape_inside_id(text_node, private_id):
        parent.remove(owner)
        parent.insert(index, text_node)
        return None, None, text_node
    if source_was_visible:
        _move_shape_source_to_defs(frame_source)
    return frame, owner, text_node


def resolve_related_target(root, scope, reference: str, *, private: bool = False):
    target_id, relation = split_related_target(reference)
    target = SVG.find_target_exact_in(scope, target_id) if scope is not None else None
    if target is None:
        target = SVG.find_target_exact_in(root, target_id)
    if not relation:
        return target, None, target, ""
    if relation != "shape-inside" or target is None:
        return None, None, target, relation
    if private:
        frame, owner, text_node = ensure_private_shape_inside(root, scope, target)
    else:
        frame_id = _shape_inside_id(target)
        frame = SVG.find_id(scope, frame_id, include_defs=True) if scope is not None else None
        if frame is None:
            frame = SVG.find_id(root, frame_id, include_defs=True)
        owner, text_node = None, target
    return frame, owner, text_node, relation


def mark_inside_owner(owner, frame, text_node, spec) -> bool:
    mode = str(getattr(spec, "inside", None) or "").strip().lower()
    if mode not in ("x", "y", "a") or owner is None or frame is None or text_node is None:
        return False
    owner.set("data-dm-inside-owner", mode)
    frame.set("data-dm-inside-frame", mode)
    text_node.set("data-dm-inside-text", "1")
    try:
        bx, by, bw, bh = _rect_world_bbox(frame)
        owner.set("data-bbox", f"{bx} {by} {bw} {bh}")
    except Exception:
        pass
    return True


def _node_world_transform(node):
    chain = []
    current = node
    while current is not None:
        chain.append(current)
        current = current.getparent() if hasattr(current, "getparent") else None
    transform = inkex.Transform()
    for item in reversed(chain):
        try:
            transform = transform @ inkex.Transform(item.get("transform") or "")
        except Exception:
            pass
    return transform


def _world_copy(node):
    out = deepcopy(node)
    try:
        out.set("transform", str(_node_world_transform(node)))
    except Exception:
        out.set("transform", str(inkex.Transform(node.get("transform") or "")))
    return out


def _point_xy(point):
    try:
        return float(point.x), float(point.y)
    except Exception:
        return float(point[0]), float(point[1])


def _rect_world_bbox(frame):
    x = float(frame.get("x") or 0.0)
    y = float(frame.get("y") or 0.0)
    width = float(frame.get("width") or 0.0)
    height = float(frame.get("height") or 0.0)
    transform = _node_world_transform(frame)
    points = [
        _point_xy(transform.apply_to_point(point))
        for point in ((x, y), (x + width, y), (x, y + height), (x + width, y + height))
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def prepare_inside_source(root, scope, base, spec):
    mode = str(getattr(spec, "inside", None) or "").strip().lower()
    if mode not in ("x", "y", "a") or base is None:
        return base
    if not str(getattr(base, "tag", "") or "").endswith("rect"):
        _l.w(f"[transform.inside] id='{base.get('id') or ''}' is not a rect")
        return base

    frame_ids = {
        str(base.get("id") or "").strip(),
        str(base.get("data-origid") or "").strip(),
    }
    frame_ids.discard("")
    search_scope = scope if scope is not None else root
    linked = []
    try:
        for node in search_scope.iter():
            tag = str(getattr(node, "tag", "") or "")
            if tag.endswith("text") and _shape_inside_id(node) in frame_ids:
                linked.append(node)
    except Exception:
        linked = []
    if not linked and search_scope is not root:
        try:
            for node in root.iter():
                tag = str(getattr(node, "tag", "") or "")
                if tag.endswith("text") and _shape_inside_id(node) in frame_ids:
                    linked.append(node)
        except Exception:
            pass
    if not linked:
        _l.w(f"[transform.inside] no shape-inside text links to frame='{base.get('id') or ''}'")
        return base

    group = inkex.Group()
    group.set("data-dm-inside-owner", mode)
    group_transform = _node_world_transform(linked[0])
    group.set("transform", str(group_transform))
    frame = deepcopy(base)
    frame.set("data-dm-inside-frame", mode)
    group.append(frame)
    try:
        bx, by, bw, bh = _rect_world_bbox(frame)
        group.set("data-bbox", f"{bx} {by} {bw} {bh}")
    except Exception:
        pass
    try:
        inverse_group = group_transform.inverse()
    except AttributeError:
        inverse_group = -group_transform
    for text_node in linked:
        text_copy = deepcopy(text_node)
        relative_transform = inverse_group @ _node_world_transform(text_node)
        if str(relative_transform).strip():
            text_copy.set("transform", str(relative_transform))
        else:
            text_copy.attrib.pop("transform", None)
        text_copy.set("data-dm-inside-text", "1")
        group.append(text_copy)
    return group


def _style_value(node, name: str) -> str:
    try:
        value = SVG.style_map(node).get(name)
        return str(value or "").strip()
    except Exception:
        return ""


def _separate_inside_flow_frame(root, owner, frame, texts) -> object | None:
    frame_id = str(frame.get("id") or "").strip()
    if not frame_id:
        return None
    linked = [text for text in texts if _shape_inside_id(text) == frame_id]
    if not linked:
        return None
    try:
        flow_id = root.get_unique_id(f"{frame_id}_flow")
    except Exception:
        flow_id = f"{frame_id}_flow_{id(frame)}"
    flow = deepcopy(frame)
    flow.set("id", flow_id)
    flow.set("data-dm-shape-inside-flow", frame_id)
    for attr in ("data-dm-inside-frame", "data-dm-shape-inside-private"):
        flow.attrib.pop(attr, None)
    defs = next(
        (child for child in list(owner) if str(getattr(child, "tag", "") or "").endswith("defs")),
        None,
    )
    if defs is None:
        defs = SVG.etree.Element(inkex.addNS("defs", "svg"))
        owner.insert(0, defs)
    elif owner.index(defs) != 0:
        owner.remove(defs)
        owner.insert(0, defs)
    defs.append(flow)
    for text in linked:
        _rewrite_shape_inside_id(text, flow_id)
    return flow


def _set_rect_world_bbox(frame, left, top, right, bottom) -> bool:
    try:
        transform = _node_world_transform(frame)
        if abs(float(getattr(transform, "b", 0.0))) > 1e-9 or abs(float(getattr(transform, "c", 0.0))) > 1e-9:
            _l.w(f"[transform.inside] rotated/skewed frame='{frame.get('id') or ''}' is not supported")
            return False
        try:
            inverse = transform.inverse()
        except AttributeError:
            inverse = -transform
        p1 = inverse.apply_to_point((left, top))
        p2 = inverse.apply_to_point((right, bottom))
        x1, y1 = _point_xy(p1)
        x2, y2 = _point_xy(p2)
        frame.set("x", f"{min(x1, x2):.9g}")
        frame.set("y", f"{min(y1, y2):.9g}")
        frame.set("width", f"{abs(x2 - x1):.9g}")
        frame.set("height", f"{abs(y2 - y1):.9g}")
        return True
    except Exception:
        return False


def _inside_array_ancestor(node):
    current = node.getparent() if node is not None else None
    while current is not None:
        if current.get("data-dm-array-cols"):
            return current
        current = current.getparent() if hasattr(current, "getparent") else None
    return None


def _array_item_ancestor(node, array_group):
    current = node
    while current is not None and current is not array_group:
        if current.get("data-dm-array-item") is not None:
            return current
        current = current.getparent() if hasattr(current, "getparent") else None
    return None


def _world_bbox_in_group(bbox, group):
    left, top, width, height = bbox
    transform = _node_world_transform(group)
    try:
        inverse = transform.inverse()
    except AttributeError:
        inverse = -transform
    points = [
        _point_xy(inverse.apply_to_point(point))
        for point in ((left, top), (left + width, top), (left, top + height), (left + width, top + height))
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _bbox_in_group(frame, group):
    return _world_bbox_in_group(_rect_world_bbox(frame), group)


def _repack_inside_arrays(array_groups, empty_items) -> None:
    for group in array_groups:
        try:
            old_left, old_top, old_width, old_height = [
                float(value) for value in str(group.get("data-bbox") or "").replace(",", " ").split()
            ]
        except Exception:
            old_left = old_top = old_width = old_height = 0.0
        for item in list(empty_items.get(group, ())):
            parent = item.getparent()
            if parent is not None:
                parent.remove(item)
        entries = []
        for item in group:
            if item.get("data-dm-array-item") is None:
                continue
            frame = next((node for node in item.iter() if node.get("data-dm-inside-frame") in ("x", "y", "a")), None)
            try:
                bbox = _bbox_in_group(frame, group) if frame is not None else _world_bbox_in_group(SVG.visual_bbox(item), group)
                entries.append((int(item.get("data-dm-array-item") or 0), item, bbox))
            except Exception:
                continue
        if not entries:
            continue
        entries.sort(key=lambda entry: entry[0])
        cols = max(1, int(group.get("data-dm-array-cols") or 1))
        rows = max(1, int(group.get("data-dm-array-rows") or 1))
        sweep_rows = group.get("data-dm-array-sweep-rows") != "0"
        gap_x = float(group.get("data-dm-array-gap-x") or 0.0)
        gap_y = float(group.get("data-dm-array-gap-y") or 0.0)
        assigned = []
        for position, entry in enumerate(entries):
            if sweep_rows:
                row, col = divmod(position, cols)
            else:
                col, row = divmod(position, rows)
            assigned.append((entry, row, col))
        used_rows = max(row for _entry, row, _col in assigned) + 1
        used_cols = max(col for _entry, _row, col in assigned) + 1
        row_heights = [0.0] * used_rows
        col_widths = [0.0] * used_cols
        for (_index, _item, (_x, _y, width, height)), row, col in assigned:
            row_heights[row] = max(row_heights[row], height)
            col_widths[col] = max(col_widths[col], width)
        current_left = min(entry[2][0] for entry in entries)
        current_top = min(entry[2][1] for entry in entries)
        total_width = sum(col_widths) + gap_x * max(0, used_cols - 1)
        total_height = sum(row_heights) + gap_y * max(0, used_rows - 1)
        try:
            anchor = int(group.get("data-dm-array-anchor") or 5)
        except Exception:
            anchor = 5
        if old_width <= 0.0 or old_height <= 0.0:
            old_left, old_top, old_width, old_height = current_left, current_top, total_width, total_height
        anchor_x, anchor_y = SVG.keypad_to_anchor(anchor)
        origin_x = old_left + (old_width - total_width) * anchor_x
        origin_y = old_top + (old_height - total_height) * anchor_y
        col_starts = [origin_x]
        row_starts = [origin_y]
        for col in range(1, used_cols):
            col_starts.append(col_starts[-1] + col_widths[col - 1] + gap_x)
        for row in range(1, used_rows):
            row_starts.append(row_starts[-1] + row_heights[row - 1] + gap_y)
        for (_index, item, (left, top, _width, _height)), row, col in assigned:
            dx = col_starts[col] - left
            dy = row_starts[row] - top
            old_transform = inkex.Transform(item.get("transform") or "")
            item.set("transform", str(inkex.Transform(f"translate({dx},{dy})") @ old_transform))
        group.set("data-bbox", f"{origin_x} {origin_y} {total_width} {total_height}")


def pending_inside_text_ids(root) -> set[str]:
    ids = set()
    for node in root.iter():
        if node.get("data-dm-inside-text") == "1":
            ids.add(SVG.ensure_id(root, node, "dm_inside_text"))
    return ids


def discard_empty_inside(root) -> int:
    owners = [node for node in root.iter() if node.get("data-dm-inside-owner") in ("x", "y", "a")]
    if not owners:
        return 0
    changed = 0
    affected_arrays = set()
    empty_array_items = {}
    for owner in owners:
        texts = [node for node in owner.iter() if node.get("data-dm-inside-text") == "1"]
        if texts and any("".join(node.itertext()).strip() for node in texts):
            continue
        array_group = _inside_array_ancestor(owner)
        if array_group is not None:
            affected_arrays.add(array_group)
            item = _array_item_ancestor(owner, array_group)
            if item is not None:
                empty_array_items.setdefault(array_group, set()).add(item)
                changed += 1
                continue
        parent = owner.getparent()
        if parent is not None:
            parent.remove(owner)
            changed += 1
    if affected_arrays:
        _repack_inside_arrays(affected_arrays, empty_array_items)
    return changed


def apply_deferred_inside(root, bboxes) -> int:
    owners = [node for node in root.iter() if node.get("data-dm-inside-owner") in ("x", "y", "a")]
    if not owners:
        return 0
    text_ids = pending_inside_text_ids(root)
    changed = 0
    affected_arrays = set()
    empty_array_items = {}
    cleanup_nodes = []
    for owner in owners:
        mode = owner.get("data-dm-inside-owner") or ""
        frame = next((node for node in owner.iter() if node.get("data-dm-inside-frame") == mode), None)
        texts = [node for node in owner.iter() if node.get("data-dm-inside-text") == "1"]
        if frame is None or not texts:
            continue
        array_group = _inside_array_ancestor(owner)
        if array_group is not None:
            affected_arrays.add(array_group)
        if not any("".join(node.itertext()).strip() for node in texts):
            item = _array_item_ancestor(owner, array_group) if array_group is not None else None
            if item is not None:
                empty_array_items.setdefault(array_group, set()).add(item)
            else:
                parent = owner.getparent()
                if parent is not None:
                    parent.remove(owner)
            changed += 1
            continue
        measured = [bboxes[node.get("id")] for node in texts if node.get("id") in bboxes]
        if not measured:
            _l.w(f"[transform.inside] query-all returned no bbox for frame='{frame.get('id') or ''}'")
            continue
        text_left = min(float(bb["x"]) for bb in measured)
        text_right = max(float(bb["x"]) + float(bb["width"]) for bb in measured)
        text_bottom = max(float(bb["y"]) + float(bb["height"]) for bb in measured)
        frame_left, frame_top, frame_width, frame_height = _rect_world_bbox(frame)
        frame_right = frame_left + frame_width
        frame_bottom = frame_top + frame_height
        original_left, original_right, original_bottom = frame_left, frame_right, frame_bottom
        try:
            padding = max(float(_style_value(texts[0], "shape-padding") or 0.0), 0.0)
        except Exception:
            padding = 0.0
        padding_x = padding_y = padding
        try:
            transform = _node_world_transform(frame)
            padding_x *= math.hypot(float(transform.a), float(transform.b))
            padding_y *= math.hypot(float(transform.c), float(transform.d))
        except Exception:
            pass
        epsilon = 0.01
        if mode in ("x", "a"):
            align = (_style_value(texts[0], "text-align") or "start").lower()
            direction = (_style_value(texts[0], "direction") or "ltr").lower()
            trim_from_left = align == "right" or (align == "end" and direction != "rtl") or (align == "start" and direction == "rtl")
            if trim_from_left:
                frame_left = max(original_left, min(frame_right, text_left - padding_x - epsilon))
            elif align in ("center", "middle"):
                center = (frame_left + frame_right) * 0.5
                half = min(frame_width * 0.5, max(center - text_left, text_right - center) + padding_x + epsilon)
                frame_left, frame_right = center - half, center + half
            elif align not in ("justify",):
                frame_right = min(original_right, max(frame_left, text_right + padding_x + epsilon))
        if mode in ("y", "a"):
            frame_bottom = min(original_bottom, max(frame_top, text_bottom + padding_y + epsilon))
        flow = _separate_inside_flow_frame(root, owner, frame, texts)
        if flow is None:
            _l.w(f"[transform.inside] could not preserve flow frame='{frame.get('id') or ''}'")
            continue
        if _set_rect_world_bbox(frame, frame_left, frame_top, frame_right, frame_bottom):
            changed += 1
        cleanup_nodes.extend([owner, frame, *texts])
    _repack_inside_arrays(affected_arrays, empty_array_items)
    for node in cleanup_nodes:
        if node.getparent() is None and node is not root:
            continue
        for attr in ("data-dm-inside-owner", "data-dm-inside-frame", "data-dm-inside-text"):
            node.attrib.pop(attr, None)
    for group in affected_arrays:
        for node in group.iter():
            node.attrib.pop("data-dm-array-item", None)
        for attr in ("data-dm-array-cols", "data-dm-array-rows", "data-dm-array-gap-x", "data-dm-array-gap-y", "data-dm-array-sweep-rows", "data-dm-array-anchor"):
            group.attrib.pop(attr, None)
    _l.i(f"[transform.inside] frames={len(owners)} changed={changed} queried_texts={len(text_ids)}")
    return changed


def _is_text_root(node) -> bool:
    tag = str(getattr(node, "tag", "") or "")
    return tag.endswith("text") or tag.endswith("flowRoot")


def _iter_text_roots(node):
    if node is None:
        return
    if _is_text_root(node):
        yield node
        return
    for child in node.iter():
        if child is node:
            continue
        if _is_text_root(child):
            yield child


def _copy_use_ref(root, use_el):
    href = (SVG.get_href(use_el) or "").strip()
    ref = SVG.find_id(root, href[1:], include_defs=True) if href.startswith("#") else None
    if ref is None:
        return None
    if str(getattr(ref, "tag", "") or "").endswith("symbol"):
        out = SVG.etree.Element(inkex.addNS("g", "svg"), nsmap=getattr(ref, "nsmap", None))
        for child in ref:
            out.append(deepcopy(child))
    else:
        out = deepcopy(ref)
    try:
        out.attrib.pop("id", None)
        use_t = inkex.Transform(use_el.get("transform") or "")
        x = float(use_el.get("x") or 0.0)
        y = float(use_el.get("y") or 0.0)
        if x or y:
            use_t = use_t @ inkex.Transform(f"translate({x},{y})")
        base_t = inkex.Transform(out.get("transform") or "")
        out.set("transform", str(use_t @ base_t))
    except Exception:
        pass
    return out


def _expand_uses(root, node):
    for _ in range(16):
        changed = False
        for use_el in list(node.iter()):
            if not str(getattr(use_el, "tag", "") or "").endswith("use"):
                continue
            parent = use_el.getparent()
            repl = _copy_use_ref(root, use_el)
            if parent is None or repl is None:
                continue
            idx = parent.index(use_el)
            parent.remove(use_el)
            parent.insert(idx, repl)
            if use_el is node:
                node = repl
            changed = True
        if not changed:
            break
    return node


def _apply_text(root, node, values) -> bool:
    vals = [str(v) for v in (values or [])]
    if not vals:
        return False
    changed = False
    for text_el, value in zip(_iter_text_roots(node), vals):
        _replace_text_preserving_runs(text_el, value)
        changed = True
    return changed


def _text_run_nodes(text_el):
    for n in text_el.iter():
        tag = str(getattr(n, "tag", "") or "")
        if n is text_el or tag.endswith("tspan") or tag.endswith("textPath") or tag.endswith("flowPara"):
            yield n


def _replace_text_preserving_runs(text_el, value: str) -> None:
    runs = list(_text_run_nodes(text_el))
    if len(runs) <= 1:
        SVG.replace_text(text_el, value)
        return

    first = None
    for n in runs:
        if n is text_el:
            continue
        if len(list(n)) == 0:
            first = n
            break
    if first is None:
        SVG.replace_text(text_el, value)
        return

    text_el.text = None
    for n in runs:
        n.tail = None
        if n is first:
            n.text = "" if value is None else str(value)
        elif n is not text_el:
            n.text = None


def _parse_percent_value(raw, *, name: str) -> float:
    s = str(raw or "").strip()
    if not s.endswith("%"):
        raise ValueError(f"{name} requires percentage values")
    try:
        v = float(s[:-1].strip()) / 100.0
    except Exception as ex:
        raise ValueError(f"{name} invalid percentage '{s}'") from ex
    if v < 0.0:
        v = 0.0
    if v > 1.0:
        v = 1.0
    return float(v)


def _normalize_soft_values(raw_vals) -> tuple[float, float, float, float] | None:
    vals = [str(v).strip() for v in (raw_vals or []) if str(v).strip()]
    if not vals:
        return None
    if len(vals) == 1:
        v = _parse_percent_value(vals[0], name="soft")
        return (v, v, v, v)
    if len(vals) == 2:
        # [horizontal vertical]
        hx = _parse_percent_value(vals[0], name="soft")
        vy = _parse_percent_value(vals[1], name="soft")
        return (vy, hx, vy, hx)
    if len(vals) == 4:
        t = _parse_percent_value(vals[0], name="soft")
        r = _parse_percent_value(vals[1], name="soft")
        b = _parse_percent_value(vals[2], name="soft")
        l = _parse_percent_value(vals[3], name="soft")
        return (t, r, b, l)
    raise ValueError("soft requires 1, 2 or 4 percentage values")


def _opacity_value(raw) -> str | None:
    s = str(raw or "").strip()
    if not s:
        return None
    v = _parse_percent_value(s, name="opacity")
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _abs_to_uu(root, raw: str) -> float:
    s = str(raw or "").strip()
    if not s:
        return 0.0
    try:
        if any(ch.isalpha() for ch in s):
            return float(root.unittouu(s))
        return float(root.unittouu(s + "mm"))
    except Exception:
        return float(s or 0.0)


def _target_size_from_scale_token(root, token: str, base: float) -> float:
    s = str(token or "").strip().replace(" ", "")
    if not s:
        return float(base)
    terms = [m.group(0) for m in re.finditer(r"[+-]?[^+-]+", s)]
    pct = 0.0
    abs_uu = 0.0
    has_pct = False
    for term in terms:
        sign = -1.0 if term.startswith("-") else 1.0
        body = term[1:] if term[:1] in "+-" else term
        if not body:
            continue
        if body.endswith("%"):
            pct += sign * (float(body[:-1] or "0") / 100.0)
            has_pct = True
        else:
            abs_uu += sign * _abs_to_uu(root, body)
    return (float(base) * pct + abs_uu) if has_pct else (float(base) + abs_uu)


def _normalize_scale_values(raw_vals) -> tuple[str, str] | None:
    vals = [str(v).strip() for v in (raw_vals or []) if str(v).strip()]
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0], vals[0]
    if len(vals) == 2:
        return vals[0], vals[1]
    raise ValueError("scale requires 1 or 2 values")


def _num_sig(v: float) -> str:
    return f"{v * 100.0:.3f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p")


def _fmt_num(v: float) -> str:
    return f"{float(v):.6f}".rstrip("0").rstrip(".")


def _resolve_filter_value(root, raw_ref) -> str | None:
    ref = str(raw_ref or "").strip()
    if not ref or root is None:
        return None
    if ref.startswith("url(#") and ref.endswith(")"):
        return ref

    def _find_by_inkscape_label(label: str):
        want = str(label or "").strip()
        if not want:
            return None
        label_keys = (
            inkex.addNS("label", "inkscape"),
            "inkscape:label",
        )
        try:
            for el in root.iter():
                if any(str(el.get(k) or "").strip() == want for k in label_keys):
                    return el
        except Exception:
            return None
        return None

    try:
        node = SVG.find_target_exact_in(root, ref)
    except Exception:
        node = root.find(f".//*[@id='{ref}']")
    if node is None:
        node = _find_by_inkscape_label(ref)
    if node is None:
        return f"url(#{ref})"

    try:
        if str(getattr(node, "tag", "")).endswith("filter"):
            fid = str(node.get("id") or "").strip()
            return f"url(#{fid})" if fid else None
    except Exception:
        pass

    direct = str(node.get("filter") or "").strip()
    if direct:
        return direct

    style = str(node.get("style") or "").strip()
    if style:
        for part in style.split(";"):
            k, sep, v = part.partition(":")
            if sep and k.strip().lower() == "filter":
                vv = v.strip()
                if vv:
                    return vv
    return None


def _resolve_fx_rect(target) -> tuple[float, float, float, float] | None:
    if target is None:
        return None
    try:
        raw = str(target.get("data-fx-rect") or "").strip()
        if not raw:
            return None
        parts = [float(p) for p in raw.replace(",", " ").split() if p]
        if len(parts) == 4 and parts[2] > 0.0 and parts[3] > 0.0:
            return (parts[0], parts[1], parts[2], parts[3])
    except Exception:
        return None
    return None


def _normalize_bbox(bbox) -> tuple[float, float, float, float] | None:
    try:
        if bbox is None or len(bbox) != 4:
            return None
        x, y, w, h = [float(v) for v in bbox]
        if w > 0.0 and h > 0.0:
            return (x, y, w, h)
    except Exception:
        return None
    return None


def _apply_visual_matrix(root, node, *, rotate=None, mirror=None, scale=None, bbox=None) -> bool:
    if node is None:
        return False
    rot = float(rotate or 0.0)
    mir = str(mirror or "").strip().lower()
    scale_vals = _normalize_scale_values(scale)
    if rot == 0.0 and mir not in ("h", "v") and scale_vals is None:
        return False
    parent = node.getparent() if hasattr(node, "getparent") else None
    if parent is None:
        return False
    bb = _normalize_bbox(bbox)
    if bb is None:
        try:
            bb = SVG.visual_bbox(node)
        except Exception:
            return False
    x, y, w, h = bb
    if w <= 0.0 or h <= 0.0:
        return False
    try:
        inv_parent = parent.composed_transform().inverse()
    except Exception:
        inv_parent = inkex.Transform()
    cx, cy = inv_parent.apply_to_point((float(x) + float(w) * 0.5, float(y) + float(h) * 0.5))
    L = inkex.Transform()
    if mir == "h":
        L = L @ inkex.Transform("scale(-1,1)")
    elif mir == "v":
        L = L @ inkex.Transform("scale(1,-1)")
    if scale_vals is not None:
        target_w = max(1e-9, _target_size_from_scale_token(root, scale_vals[0], w))
        target_h = max(1e-9, _target_size_from_scale_token(root, scale_vals[1], h))
        L = L @ inkex.Transform(f"scale({target_w / w},{target_h / h})")
    if rot:
        L = L @ inkex.Transform(f"rotate({rot})")
    extra = inkex.Transform(f"translate({cx},{cy})") @ L @ inkex.Transform(f"translate({-cx},{-cy})")
    try:
        old = inkex.Transform(node.get("transform") or "")
    except Exception:
        old = inkex.Transform()
    node.set("transform", str(extra @ old))
    return True


def _ensure_soft_gradients(root) -> dict[str, str]:
    defs = SVG.ensure_defs(root)

    def _mk_grad(gid: str, attrs: dict[str, str], stops: list[tuple[str, str, str | None]]):
        old = root.find(f".//svg:linearGradient[@id='{gid}']", namespaces=SVG.NSS)
        if old is not None:
            try:
                p = old.getparent()
                if p is not None:
                    p.remove(old)
            except Exception:
                pass
        grad = SVG.etree.SubElement(defs, inkex.addNS("linearGradient", "svg"))
        grad.set("id", gid)
        grad.set("gradientUnits", "objectBoundingBox")
        for k, v in attrs.items():
            grad.set(k, v)
        for offset, color, opacity in stops:
            st = SVG.etree.SubElement(grad, inkex.addNS("stop", "svg"))
            st.set("offset", offset)
            st.set("stop-color", color)
            if opacity is not None:
                st.set("stop-opacity", opacity)
        return gid

    out = {}
    out["left"] = _mk_grad(
        "tf_soft_grad_left",
        {"x1": "0", "y1": "0", "x2": "1", "y2": "0"},
        [("0%", "#000000", "1"), ("100%", "#808080", "0")],
    )
    out["right"] = _mk_grad(
        "tf_soft_grad_right",
        {"x1": "0", "y1": "0", "x2": "1", "y2": "0"},
        [("0%", "#808080", "0"), ("100%", "#000000", "1")],
    )
    out["top"] = _mk_grad(
        "tf_soft_grad_top",
        {"x1": "0", "y1": "0", "x2": "0", "y2": "1"},
        [("0%", "#000000", "1"), ("100%", "#808080", "0")],
    )
    out["bottom"] = _mk_grad(
        "tf_soft_grad_bottom",
        {"x1": "0", "y1": "0", "x2": "0", "y2": "1"},
        [("0%", "#808080", "0"), ("100%", "#000000", "1")],
    )
    return out


def _ensure_soft_mask(root, geom_node, soft_vals: tuple[float, float, float, float]) -> str | None:
    t, r, b, l = [max(0.0, min(1.0, float(v))) for v in soft_vals]
    sig = f"{_num_sig(t)}_{_num_sig(r)}_{_num_sig(b)}_{_num_sig(l)}"
    rect = _resolve_fx_rect(geom_node)
    if rect is not None:
        x, y, w, h = rect
        rsig = f"{_num_sig(x)}_{_num_sig(y)}_{_num_sig(w)}_{_num_sig(h)}"
        mid = f"tf_soft_mask_{sig}_{rsig}"
    else:
        x = y = 0.0
        w = h = 1.0
        mid = f"tf_soft_mask_{sig}"
    old = root.find(f".//svg:mask[@id='{mid}']", namespaces=SVG.NSS)
    if old is not None:
        try:
            p = old.getparent()
            if p is not None:
                p.remove(old)
        except Exception:
            pass

    defs = SVG.ensure_defs(root)
    grads = _ensure_soft_gradients(root)
    mask = SVG.etree.SubElement(defs, inkex.addNS("mask", "svg"))
    mask.set("id", mid)
    if rect is not None:
        mask.set("maskUnits", "userSpaceOnUse")
        mask.set("maskContentUnits", "userSpaceOnUse")
    else:
        mask.set("maskUnits", "objectBoundingBox")
        mask.set("maskContentUnits", "objectBoundingBox")
    mask.set("mask-type", "luminance")
    base = SVG.etree.SubElement(mask, inkex.addNS("rect", "svg"))
    base.set("x", _fmt_num(x))
    base.set("y", _fmt_num(y))
    base.set("width", _fmt_num(w))
    base.set("height", _fmt_num(h))
    base.set("fill", "#ffffff")
    base.set("fill-opacity", "1")

    def _edge_rect(x, y, w, h, gid):
        if w <= 0.0 or h <= 0.0:
            return
        rr = SVG.etree.SubElement(mask, inkex.addNS("rect", "svg"))
        rr.set("x", _fmt_num(x))
        rr.set("y", _fmt_num(y))
        rr.set("width", _fmt_num(w))
        rr.set("height", _fmt_num(h))
        rr.set("fill", f"url(#{gid})")

    _edge_rect(x, y, w * l, h, grads["left"])
    _edge_rect(x + w * (1.0 - r), y, w * r, h, grads["right"])
    _edge_rect(x, y, w, h * t, grads["top"])
    _edge_rect(x, y + h * (1.0 - b), w, h * b, grads["bottom"])
    return mid


def apply_transform_spec(root, node, spec, *, bbox=None) -> bool:
    if root is None or node is None or spec is None:
        return False
    if has_text(spec):
        try:
            node = _expand_uses(root, node)
        except Exception as ex:
            _l.w(f"[transform] text use expansion failed on id='{node.get('id') or ''}': {ex}")
        try:
            import text as TXT
            TXT._normalize_rich_visible_for_all_texts(node)
            if _is_text_root(node):
                TXT._maybe_parse_rich_visible_into_dom(node)
        except Exception:
            pass
    opacity_target = node
    soft_target = node
    filter_target = node
    try:
        parent = node.getparent() if hasattr(node, "getparent") else None
        pid = (parent.get("id") or "").strip() if parent is not None else ""
        gp = parent.getparent() if parent is not None and hasattr(parent, "getparent") else None
        gpid = (gp.get("id") or "").strip() if gp is not None else ""
        ggp = gp.getparent() if gp is not None and hasattr(gp, "getparent") else None
        ggpid = (ggp.get("id") or "").strip() if ggp is not None else ""
        if parent is not None and parent.get("clip-path") and pid.endswith("_clip"):
            if gp is not None and gpid.endswith("_soft"):
                soft_target = gp
                filter_target = gp
                if ggp is not None and (ggpid.startswith("fa_clipwrap_") or ggpid.endswith("_postshift")):
                    opacity_target = ggp
                else:
                    opacity_target = gp
            elif gp is not None and gpid.startswith("fa_clipwrap_"):
                soft_target = parent
                filter_target = parent
                opacity_target = gp
            else:
                soft_target = parent
                filter_target = parent
                opacity_target = parent
    except Exception:
        opacity_target = node
        soft_target = node
        filter_target = node
    changed = False

    try:
        if has_text(spec):
            changed = _apply_text(root, node, getattr(spec, "text", None)) or changed
    except Exception as ex:
        _l.w(f"[transform] text failed on id='{node.get('id') or ''}': {ex}")

    try:
        if _apply_visual_matrix(root, opacity_target, rotate=getattr(spec, "rotate", None), mirror=getattr(spec, "mirror", None), scale=getattr(spec, "scale", None), bbox=bbox):
            changed = True
    except Exception as ex:
        _l.w(f"[transform] visual matrix failed on id='{opacity_target.get('id') or ''}': {ex}")

    try:
        if getattr(spec, "opacity", None):
            op = _opacity_value(getattr(spec, "opacity"))
            if op is not None:
                opacity_target.set("opacity", op)
                if opacity_target is not node:
                    try:
                        node.attrib.pop("opacity", None)
                    except Exception:
                        pass
                changed = True
    except Exception as ex:
        _l.w(f"[transform] opacity failed on id='{opacity_target.get('id') or ''}': {ex}")

    try:
        raw_filter = getattr(spec, "filter_ref", None)
        if raw_filter:
            filt = _resolve_filter_value(root, raw_filter)
            if filt:
                filter_target.set("filter", filt)
                if filter_target is not node:
                    try:
                        node.attrib.pop("filter", None)
                    except Exception:
                        pass
                changed = True
    except Exception as ex:
        _l.w(f"[transform] filter failed on id='{filter_target.get('id') or ''}': {ex}")

    try:
        soft_vals = _normalize_soft_values(getattr(spec, "soft", None))
        if soft_vals is not None:
            mid = _ensure_soft_mask(root, soft_target, soft_vals)
            if mid:
                soft_target.set("mask", f"url(#{mid})")
                if soft_target is not node:
                    try:
                        node.attrib.pop("mask", None)
                    except Exception:
                        pass
                changed = True
    except Exception as ex:
        _l.w(f"[transform] soft failed on id='{soft_target.get('id') or ''}': {ex}")

    try:
        if changed:
            _l.d(
                f"[transform] applied opacity_target='{opacity_target.get('id') or ''}' "
                f"filter_target='{filter_target.get('id') or ''}' "
                f"soft_target='{soft_target.get('id') or ''}' "
                f"node='{node.get('id') or ''}' soft={getattr(spec, 'soft', None)} "
                f"scale={getattr(spec, 'scale', None)} "
                f"filter={getattr(spec, 'filter_ref', None)} "
                f"opacity={getattr(spec, 'opacity', None)} "
                f"text={getattr(spec, 'text', None)} "
                f"inside={getattr(spec, 'inside', None)}"
            )
    except Exception:
        pass

    return changed


__all__ = [
    "merge_specs", "has_text", "has_inside", "split_related_target",
    "shape_inside_dependency_ids", "ensure_private_shape_inside", "resolve_related_target", "mark_inside_owner",
    "prepare_inside_source", "pending_inside_text_ids", "discard_empty_inside", "apply_deferred_inside", "apply_transform_spec",
]
