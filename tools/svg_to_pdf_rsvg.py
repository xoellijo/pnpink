#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from svg_to_pdf import (  # type: ignore
    PageBox,
    PageGroup,
    _as_float,
    _clone_page_svg,
    _inline_image_hrefs,
    _pair_groups_to_pages,
    _prepare_path,
    _read_page_groups,
    _read_pages,
    _read_svg_size,
    _length_to_points,
)

_LOG_FP = None
_HREF_LOCAL_BBOX: dict[str, tuple[float, float, float, float]] = {}


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    global _LOG_FP
    if _LOG_FP is not None:
        _LOG_FP.write(line + "\n")
        _LOG_FP.flush()


def _viewbox_values(viewbox: str) -> tuple[float, float, float, float] | None:
    parts = [p for p in str(viewbox or "").replace(",", " ").split() if p]
    if len(parts) != 4:
        return None
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    except Exception:
        return None


def _svg_user_unit_to_pt(svg_size, *, dpi: float = 96.0) -> float:
    vb = _viewbox_values(svg_size.viewbox)
    if vb is None:
        return 72.0 / float(dpi or 96.0)
    _vx, _vy, vw, vh = vb
    scales: list[float] = []
    if vw > 0:
        scales.append(_length_to_points(svg_size.width_attr, dpi=dpi) / vw)
    if vh > 0:
        scales.append(_length_to_points(svg_size.height_attr, dpi=dpi) / vh)
    scales = [s for s in scales if s > 0]
    if not scales:
        return 72.0 / float(dpi or 96.0)
    return sum(scales) / len(scales)


def _svg_points_from_page(page: PageBox, *, unit_to_pt: float) -> tuple[float, float]:
    return max(1.0, page.w * unit_to_pt), max(1.0, page.h * unit_to_pt)


def _clone_page_svg_fast(root: ET.Element, group: PageGroup, box: PageBox, *, svg_size) -> bytes:
    out_root = root.find(".//*[@id='pnpink-output']")
    if out_root is None:
        return _clone_page_svg(root, group, box, svg_size=svg_size)

    keep = None
    for child in list(out_root):
        if str(child.get("id") or "").strip() == group.group_id:
            keep = child
            break
    if keep is None:
        return _clone_page_svg(root, group, box, svg_size=svg_size)

    page_root = ET.Element(root.tag, dict(root.attrib))
    page_root.set("width", svg_size.width_attr)
    page_root.set("height", svg_size.height_attr)
    page_root.set("viewBox", svg_size.viewbox)
    page_root.set("overflow", "hidden")

    for child in list(root):
        if _tag_local(child) == "defs":
            page_root.append(copy.deepcopy(child))

    pmap = _parent_map(root)
    ancestors = []
    cur = pmap.get(out_root)
    while cur is not None and cur is not root:
        ancestors.append(cur)
        cur = pmap.get(cur)
    parent = page_root
    for ancestor in reversed(ancestors):
        clone = ET.Element(ancestor.tag, dict(ancestor.attrib))
        parent.append(clone)
        parent = clone

    out_clone = ET.Element(out_root.tag, dict(out_root.attrib))
    keep_clone = copy.deepcopy(keep)
    if box.x or box.y:
        shift = f"translate({-box.x:g},{-box.y:g})"
        existing = str(keep_clone.get("transform") or "").strip()
        keep_clone.set("transform", f"{shift} {existing}".strip() if existing else shift)
    out_clone.append(keep_clone)
    parent.append(out_clone)

    return ET.tostring(page_root, encoding="utf-8", xml_declaration=True)


def _render_pages_to_pdf_bytes(page_svgs: list[bytes], page_boxes: list[PageBox], *, unit_to_pt: float) -> bytes:
    import cairo
    import gi

    gi.require_version("Rsvg", "2.0")
    from gi.repository import Gio, GLib, Rsvg

    out = io.BytesIO()
    w0, h0 = _svg_points_from_page(page_boxes[0], unit_to_pt=unit_to_pt)
    surf = cairo.PDFSurface(out, w0, h0)
    try:
        for i, svg_bytes in enumerate(page_svgs):
            w, h = _svg_points_from_page(page_boxes[i], unit_to_pt=unit_to_pt)
            if i > 0:
                surf.set_size(w, h)
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(svg_bytes))
            handle = Rsvg.Handle.new_from_stream_sync(stream, None, Rsvg.HandleFlags.FLAGS_NONE, None)
            ctx = cairo.Context(surf)
            vp = Rsvg.Rectangle()
            vp.x = 0.0
            vp.y = 0.0
            vp.width = float(w)
            vp.height = float(h)
            handle.render_document(ctx, vp)
            surf.show_page()
    finally:
        surf.finish()
    return out.getvalue()


def _tag_local(el: ET.Element) -> str:
    t = str(getattr(el, "tag", "") or "")
    return t.rsplit("}", 1)[-1] if "}" in t else t


def _ns_tag(root: ET.Element, local: str) -> str:
    t = str(getattr(root, "tag", "") or "")
    if t.startswith("{") and "}" in t:
        return t.split("}", 1)[0] + "}" + local
    return local


def _use_href_id(u: ET.Element) -> str:
    href = str(u.get("href") or u.get("{http://www.w3.org/1999/xlink}href") or "").strip()
    return href[1:] if href.startswith("#") else ""


def _mul_affine(m1: tuple[float, float, float, float, float, float], m2: tuple[float, float, float, float, float, float]) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2, a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def _parse_transform(transform: str) -> tuple[float, float, float, float, float, float] | None:
    txt = str(transform or "").strip()
    if not txt:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    pos = 0
    out = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    found = False
    for m in re.finditer(r"(matrix|translate|scale)\(([^)]*)\)", txt):
        if txt[pos:m.start()].strip():
            return None
        pos = m.end()
        kind = m.group(1)
        parts = [p for p in m.group(2).replace(",", " ").split() if p]
        try:
            if kind == "translate":
                tx = float(parts[0]) if parts else 0.0
                ty = float(parts[1]) if len(parts) > 1 else 0.0
                local = (1.0, 0.0, 0.0, 1.0, tx, ty)
            elif kind == "scale":
                sx = float(parts[0]) if parts else 1.0
                sy = float(parts[1]) if len(parts) > 1 else sx
                local = (sx, 0.0, 0.0, sy, 0.0, 0.0)
            else:
                if len(parts) != 6:
                    return None
                local = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5]))
        except Exception:
            return None
        out = _mul_affine(out, local)
        found = True
    if txt[pos:].strip():
        return None
    return out if found else None


def _scale_affine_translate(m: tuple[float, float, float, float, float, float], coord_scale: float) -> tuple[float, float, float, float, float, float]:
    a, b, c, d, e, f = m
    return (a, b, c, d, e * coord_scale, f * coord_scale)


def _compose_affine(
    node: ET.Element,
    root: ET.Element,
    *,
    coord_scale: float = 1.0,
    pmap: dict[ET.Element, ET.Element] | None = None,
) -> tuple[float, float, float, float, float, float] | None:
    if pmap is None:
        pmap = _parent_map(root)
    chain = []
    cur = node
    while cur is not None:
        chain.append(cur)
        cur = pmap.get(cur)
    chain.reverse()
    out = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for n in chain:
        # <use> can position instances via x/y even without a transform attribute.
        # Model it as a pre-multiplied translate on that node.
        if _tag_local(n) == "use":
            ux = _as_float(n.get("x"), 0.0) * coord_scale
            uy = _as_float(n.get("y"), 0.0) * coord_scale
            if ux or uy:
                out = _mul_affine(out, (1.0, 0.0, 0.0, 1.0, ux, uy))
        tr = _parse_transform(str(n.get("transform") or ""))
        if tr is None:
            return None
        out = _mul_affine(out, _scale_affine_translate(tr, coord_scale))
    return out


def _apply_affine(m: tuple[float, float, float, float, float, float], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _invert_affine(m: tuple[float, float, float, float, float, float]) -> tuple[float, float, float, float, float, float] | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    ia = d / det
    ib = -b / det
    ic = -c / det
    id_ = a / det
    ie = -(ia * e + ic * f)
    iff = -(ib * e + id_ * f)
    return (ia, ib, ic, id_, ie, iff)


def _iter_uses(root: ET.Element) -> list[ET.Element]:
    return [el for el in root.iter() if _tag_local(el) == "use"]


def _is_composed_static_id(value: str | None) -> bool:
    return re.match(r"^pnp_tpl_.+_static_\d+$", str(value or "").strip()) is not None


def _is_card_group(el: ET.Element) -> bool:
    return _tag_local(el) == "g" and str(el.get("data-pnpink-row-index") or "").strip() != ""


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    out: dict[ET.Element, ET.Element] = {}
    for p in root.iter():
        for c in list(p):
            out[c] = p
    return out


def _ancestors(node: ET.Element, pmap: dict[ET.Element, ET.Element]) -> list[ET.Element]:
    out = []
    cur = pmap.get(node)
    while cur is not None:
        out.append(cur)
        cur = pmap.get(cur)
    return out


def _rect_bbox(el: ET.Element, root: ET.Element, *, coord_scale: float, pmap: dict[ET.Element, ET.Element]) -> tuple[float, float, float, float] | None:
    if _tag_local(el) != "rect":
        return None
    x = _as_float(el.get("x"), 0.0)
    y = _as_float(el.get("y"), 0.0)
    w = _as_float(el.get("width"), 0.0)
    h = _as_float(el.get("height"), 0.0)
    if w <= 0.0 or h <= 0.0:
        return None
    m = _compose_affine(el, root, coord_scale=coord_scale, pmap=pmap)
    if m is None:
        return None
    pts = [
        _apply_affine(m, x, y),
        _apply_affine(m, x + w, y),
        _apply_affine(m, x, y + h),
        _apply_affine(m, x + w, y + h),
    ]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _scale_rect(r: tuple[float, float, float, float], scale: float) -> tuple[float, float, float, float]:
    return (r[0] * scale, r[1] * scale, r[2] * scale, r[3] * scale)


def _best_card_bbox_rect(
    card: ET.Element,
    root: ET.Element,
    *,
    coord_scale: float,
    pmap: dict[ET.Element, ET.Element],
    excluded_ancestors: set[ET.Element],
) -> tuple[float, float, float, float] | None:
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for el in card.iter():
        if _tag_local(el) != "rect":
            continue
        if any(a in excluded_ancestors for a in _ancestors(el, pmap)):
            continue
        bb = _rect_bbox(el, root, coord_scale=coord_scale, pmap=pmap)
        if bb is None:
            continue
        area = max(0.0, bb[2] - bb[0]) * max(0.0, bb[3] - bb[1])
        if area <= 0.0:
            continue
        score = area + (1.0e18 if str(el.get("data-origid") or "").strip() else 0.0)
        if best is None or score > best[0]:
            best = (score, bb)
    return best[1] if best is not None else None


def _remove_static_composed_nodes(root: ET.Element, static_ids: set[str]) -> None:
    pmap = _parent_map(root)
    for el in list(root.iter()):
        eid = str(el.get("id") or "").strip()
        remove = eid in static_ids
        if not remove and _tag_local(el) == "use":
            remove = _use_href_id(el) in static_ids
        if remove:
            p = pmap.get(el)
            if p is not None:
                p.remove(el)


def _build_proto_from_static_groups(
    root: ET.Element,
    static_group_ids: set[str],
    src_rect: tuple[float, float, float, float],
) -> bytes | None:
    if not static_group_ids:
        return None
    proto = copy.deepcopy(root)
    x0, y0, x1, y1 = src_rect
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)
    proto.set("width", f"{w:g}")
    proto.set("height", f"{h:g}")
    proto.set("viewBox", f"0 0 {w:g} {h:g}")
    proto.set("overflow", "visible")
    pmap = _parent_map(proto)
    keep: set[ET.Element] = {proto}
    for el in proto.iter():
        if _tag_local(el) == "defs":
            keep.update(el.iter())
            continue
        if str(el.get("id") or "").strip() in static_group_ids:
            keep.update(el.iter())
            cur = el
            while cur is not None:
                keep.add(cur)
                cur = pmap.get(cur)
    if len(keep) <= 1:
        return None
    for parent in list(proto.iter()):
        for child in list(parent):
            if child not in keep:
                parent.remove(child)
    wrapper = ET.Element(_ns_tag(proto, "g"), {"transform": f"translate({-x0:g},{-y0:g})"})
    for child in list(proto):
        if _tag_local(child) == "defs":
            continue
        proto.remove(child)
        wrapper.append(child)
    proto.append(wrapper)
    _prune_unreferenced_defs(proto)
    return ET.tostring(proto, encoding="utf-8", xml_declaration=True)


def _build_page_variants_composed_instance(
    root: ET.Element,
    *,
    coord_scale: float,
) -> tuple[bytes, tuple[float, float, float, float] | None, list[tuple[float, float, float, float]], bytes | None] | None:
    pmap = _parent_map(root)
    cards = [el for el in root.iter() if _is_card_group(el)]
    if not cards:
        return None

    static_group_ids = {
        str(el.get("id") or "").strip()
        for el in root.iter()
        if _tag_local(el) == "g" and _is_composed_static_id(el.get("id"))
    }
    referenced_static_ids = {_use_href_id(u) for u in _iter_uses(root) if _is_composed_static_id(_use_href_id(u))}
    static_ids = static_group_ids | referenced_static_ids
    if not static_ids:
        return None

    static_groups = {el for el in root.iter() if str(el.get("id") or "").strip() in static_group_ids}
    rects: list[tuple[float, float, float, float]] = []
    proto_src_rect: tuple[float, float, float, float] | None = None
    proto_src_rect_user: tuple[float, float, float, float] | None = None
    has_source_card = False

    for card in cards:
        card_static_groups = {el for el in static_groups if el is card or card in _ancestors(el, pmap)}
        bb_user = _best_card_bbox_rect(
            card,
            root,
            coord_scale=1.0,
            pmap=pmap,
            excluded_ancestors=card_static_groups,
        )
        if bb_user is None:
            continue
        bb = _scale_rect(bb_user, coord_scale)
        rects.append(bb)
        if card_static_groups and proto_src_rect is None:
            proto_src_rect = bb
            proto_src_rect_user = bb_user
            has_source_card = True

    if not rects:
        return None

    base_root = copy.deepcopy(root)
    _remove_static_composed_nodes(base_root, static_ids)
    _prune_unreferenced_defs(base_root)
    proto_src = proto_src_rect or rects[0]
    proto_svg = _build_proto_from_static_groups(root, static_group_ids, proto_src_rect_user or proto_src) if has_source_card else None
    if proto_svg is not None:
        proto_src_rect = (0.0, 0.0, max(1.0, proto_src[2] - proto_src[0]), max(1.0, proto_src[3] - proto_src[1]))
    _log(
        "composed-instance scan: "
        f"cards={len(cards)} rects={len(rects)} static_sources={len(static_group_ids)} static_refs={len(referenced_static_ids)}"
    )
    return (
        ET.tostring(base_root, encoding="utf-8", xml_declaration=True),
        proto_src_rect or rects[0],
        rects,
        proto_svg,
    )


def _remove_all_uses(root: ET.Element) -> None:
    pmap = _parent_map(root)
    for u in _iter_uses(root):
        p = pmap.get(u)
        if p is not None:
            p.remove(u)


def _refs_from_text(text: str) -> set[str]:
    refs: set[str] = set()
    for m in re.finditer(r"url\(#([^)]+)\)", text):
        refs.add(m.group(1))
    href = text.strip()
    if href.startswith("#") and len(href) > 1:
        refs.add(href[1:])
    return refs


def _element_refs(el: ET.Element) -> set[str]:
    refs: set[str] = set()
    for value in el.attrib.values():
        refs.update(_refs_from_text(str(value)))
    return refs


def _prune_unreferenced_defs(root: ET.Element) -> None:
    pmap = _parent_map(root)
    defs_nodes = [el for el in root.iter() if _tag_local(el) == "defs"]
    defs_descendants: set[ET.Element] = set()
    id_map: dict[str, ET.Element] = {}
    for defs in defs_nodes:
        for el in defs.iter():
            defs_descendants.add(el)
            eid = str(el.get("id") or "").strip()
            if eid:
                id_map[eid] = el

    keep_ids: set[str] = set()
    for el in root.iter():
        if el in defs_descendants:
            continue
        keep_ids.update(_element_refs(el))

    changed = True
    while changed:
        changed = False
        for eid in list(keep_ids):
            el = id_map.get(eid)
            if el is None:
                continue
            for ref in _element_refs(el):
                if ref not in keep_ids:
                    keep_ids.add(ref)
                    changed = True

    for defs in defs_nodes:
        for child in list(defs):
            subtree_ids = {str(el.get("id") or "").strip() for el in child.iter()}
            subtree_ids.discard("")
            if subtree_ids and subtree_ids.isdisjoint(keep_ids):
                defs.remove(child)


def _keep_only_first_use(root: ET.Element) -> ET.Element | None:
    uses = _iter_uses(root)
    if not uses:
        return None
    first = uses[0]
    pmap = _parent_map(root)
    for u in uses[1:]:
        p = pmap.get(u)
        if p is not None:
            p.remove(u)
    return first


def _prune_proto_to_first_use(root: ET.Element, first_use: ET.Element) -> None:
    keep: set[ET.Element] = set()
    pmap = _parent_map(root)
    cur = first_use
    while cur is not None:
        keep.add(cur)
        cur = pmap.get(cur)
    for el in root.iter():
        if _tag_local(el) == "defs":
            for sub in el.iter():
                keep.add(sub)
    for parent in list(root.iter()):
        for child in list(parent):
            if child in keep:
                continue
            parent.remove(child)


def _find_inkscape() -> str | None:
    env = str(os.environ.get("PNPINK_INKSCAPE_BIN") or "").strip()
    cands = []
    if env:
        cands += [str(Path(env) / "inkscape.com"), str(Path(env) / "inkscape.exe")]
    hb = Path.home() / "inkscape" / "bin"
    cands += [str(hb / "inkscape.com"), str(hb / "inkscape.exe"), "inkscape"]
    for c in cands:
        p = Path(c)
        if c == "inkscape" or p.is_file():
            return c
    return None


def _bbox_map_query_all(root: ET.Element) -> dict[str, tuple[float, float, float, float]]:
    inkscape = _find_inkscape()
    if not inkscape:
        return {}
    uses = _iter_uses(root)
    ids = []
    i = 0
    for u in uses:
        uid = str(u.get("id") or "").strip()
        if not uid:
            i += 1
            uid = f"__u{i}"
            u.set("id", uid)
        ids.append(uid)
    if not ids:
        return {}

    import tempfile

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".svg") as tf:
            tf.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
            tmp = tf.name
        cp = subprocess.run([inkscape, "--query-all", tmp], capture_output=True, text=True, check=False)
        if cp.returncode != 0:
            return {}
        wanted = set(ids)
        out: dict[str, tuple[float, float, float, float]] = {}
        for line in cp.stdout.splitlines():
            parts = [p.strip() for p in line.strip().split(",")]
            if len(parts) < 5:
                continue
            uid = parts[0]
            if uid not in wanted:
                continue
            try:
                x, y, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            except Exception:
                continue
            if w > 0 and h > 0:
                out[uid] = (x, y, x + w, y + h)
        return out
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except Exception:
                pass


def _px_to_pt(r: tuple[float, float, float, float], dpi: float = 96.0) -> tuple[float, float, float, float]:
    f = 72.0 / float(dpi or 96.0)
    return (r[0] * f, r[1] * f, r[2] * f, r[3] * f)


def _proto_content_bbox(page, fitz):
    bbox = None
    for dr in page.get_drawings():
        rr = dr.get("rect")
        if not rr:
            continue
        r = fitz.Rect(rr)
        if r.is_empty or r.width <= 0 or r.height <= 0:
            continue
        bbox = r if bbox is None else (bbox | r)
    for b in page.get_text("blocks"):
        r = fitz.Rect(b[:4])
        if r.is_empty or r.width <= 0 or r.height <= 0:
            continue
        bbox = r if bbox is None else (bbox | r)
    # Include raster image placements, otherwise bbox can collapse to tiny vector marks.
    for im in page.get_images(full=True):
        try:
            xref = int(im[0])
        except Exception:
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        for rr in rects:
            r = fitz.Rect(rr)
            if r.is_empty or r.width <= 0 or r.height <= 0:
                continue
            bbox = r if bbox is None else (bbox | r)
    if bbox is None:
        bbox = page.rect
    return bbox


def _layout_key(page_rect, rects: list) -> tuple:
    rounded_page = tuple(round(float(v), 3) for v in (page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1))
    rounded_rects = tuple(tuple(round(float(v), 3) for v in r) for r in rects)
    return (rounded_page, rounded_rects)


def _build_overlay_templates(fitz, bdoc, pdoc, all_rects_pt, src_bbox, supports_reuse: bool):
    templates = fitz.open()
    template_by_key = {}
    template_by_page: list[int | None] = []

    for i in range(bdoc.page_count):
        rects = all_rects_pt[i] if i < len(all_rects_pt) else []
        if not rects:
            template_by_page.append(None)
            continue
        page_rect = bdoc[i].rect
        key = _layout_key(page_rect, rects)
        template_index = template_by_key.get(key)
        if template_index is None:
            tpage = templates.new_page(width=page_rect.width, height=page_rect.height)
            reuse = 0
            for r in rects:
                rr = fitz.Rect(*r)
                if supports_reuse:
                    reuse = tpage.show_pdf_page(rr, pdoc, 0, keep_proportion=False, overlay=True, clip=src_bbox, reuse_xref=reuse)
                else:
                    tpage.show_pdf_page(rr, pdoc, 0, keep_proportion=False, overlay=True, clip=src_bbox)
            template_index = templates.page_count - 1
            template_by_key[key] = template_index
        template_by_page.append(template_index)

    return templates, template_by_page, len(template_by_key)


def _build_proto_svg(page_svg: bytes) -> bytes | None:
    rep_root = ET.fromstring(page_svg)
    first = _keep_only_first_use(rep_root)
    if first is None:
        return None
    _prune_proto_to_first_use(rep_root, first)
    return ET.tostring(rep_root, encoding="utf-8", xml_declaration=True)


def _build_page_variants(page_svg: bytes, *, coord_scale: float = 1.0) -> tuple[bytes, tuple[float, float, float, float] | None, list[tuple[float, float, float, float]], bytes | None]:
    root = ET.fromstring(page_svg)
    composed = _build_page_variants_composed_instance(root, coord_scale=coord_scale)
    if composed is not None:
        return composed

    uses = _iter_uses(root)
    if not uses:
        return page_svg, None, [], None

    rects: list[tuple[float, float, float, float]] = []
    missing_for_query: list[ET.Element] = []
    pmap = _parent_map(root)
    for u in uses:
        href = _use_href_id(u)
        local = _HREF_LOCAL_BBOX.get(href) if href else None
        if local is not None:
            m = _compose_affine(u, root, coord_scale=coord_scale, pmap=pmap)
            if m is not None:
                x0, y0, x1, y1 = local
                p1 = _apply_affine(m, x0, y0)
                p2 = _apply_affine(m, x1, y0)
                p3 = _apply_affine(m, x0, y1)
                p4 = _apply_affine(m, x1, y1)
                xs = [p1[0], p2[0], p3[0], p4[0]]
                ys = [p1[1], p2[1], p3[1], p4[1]]
                rects.append((min(xs), min(ys), max(xs), max(ys)))
                continue
        missing_for_query.append(u)

    if missing_for_query:
        bbox_map = _bbox_map_query_all(root)
        _log(f"inkscape query-all uses={len(missing_for_query)} resolved={len(bbox_map)}")
        for u in uses:
            uid = str(u.get("id") or "").strip()
            bb = bbox_map.get(uid)
            if bb is not None and (u in missing_for_query):
                rects.append(bb)
                href = _use_href_id(u)
                if href and href not in _HREF_LOCAL_BBOX:
                    m = _compose_affine(u, root, coord_scale=coord_scale, pmap=pmap)
                    inv = _invert_affine(m) if m is not None else None
                    if inv is not None:
                        x0, y0, x1, y1 = bb
                        q1 = _apply_affine(inv, x0, y0)
                        q2 = _apply_affine(inv, x1, y0)
                        q3 = _apply_affine(inv, x0, y1)
                        q4 = _apply_affine(inv, x1, y1)
                        xs = [q1[0], q2[0], q3[0], q4[0]]
                        ys = [q1[1], q2[1], q3[1], q4[1]]
                        _HREF_LOCAL_BBOX[href] = (min(xs), min(ys), max(xs), max(ys))
    if not rects:
        return page_svg, None, [], None

    base_root = copy.deepcopy(root)
    _remove_all_uses(base_root)
    _prune_unreferenced_defs(base_root)

    return (
        ET.tostring(base_root, encoding="utf-8", xml_declaration=True),
        rects[0],
        rects,
        None,
    )


def convert_svg_to_pdf_rsvg(
    svg_path: str,
    pdf_path: str,
    *,
    inline_images: bool = True,
    dpi: float = 96.0,
    overlay_job_dir: str | None = None,
    skip_overlay: bool = False,
) -> tuple[int, int, dict]:
    t0 = time.perf_counter()
    svg_abs = os.path.abspath(svg_path)
    pdf_abs = os.path.abspath(pdf_path)

    _HREF_LOCAL_BBOX.clear()
    _log("start convert")
    tree = ET.parse(svg_abs)
    root = tree.getroot()
    pages = _read_pages(root)
    groups = _read_page_groups(root)
    svg_size = _read_svg_size(root)
    unit_to_pt = _svg_user_unit_to_pt(svg_size, dpi=dpi)
    coord_scale = unit_to_pt / (72.0 / float(dpi or 96.0))

    if not pages:
        w = _as_float(root.get("width"), 1.0)
        h = _as_float(root.get("height"), 1.0)
        pages = [PageBox(page_id="page1", x=0.0, y=0.0, w=max(1.0, w), h=max(1.0, h), order=1)]
    if not groups:
        groups = [PageGroup(element=root, group_id=str(root.get("id") or "root"), page_id=pages[0].page_id, page_index=0, order=1)]

    if inline_images:
        _inline_image_hrefs(root, svg_dir=os.path.dirname(svg_abs))

    pairs = _pair_groups_to_pages(groups, pages)
    page_svgs = [_clone_page_svg_fast(root, g, b, svg_size=svg_size) for g, b in pairs]
    page_boxes = [b for _g, b in pairs]
    _log(f"pages={len(page_svgs)}")

    base_svgs: list[bytes] = []
    all_rects_pt: list[list[tuple[float, float, float, float]]] = []
    proto_svg: bytes | None = None
    proto_box: PageBox | None = None
    proto_candidate: tuple[bytes, PageBox, int, tuple[float, float, float, float]] | None = None
    opt_pages = 0

    for i, psvg in enumerate(page_svgs):
        base_svg, first_px, rects_px, page_proto_svg = _build_page_variants(psvg, coord_scale=coord_scale)
        base_svgs.append(base_svg)
        if first_px is None or not rects_px:
            all_rects_pt.append([])
            continue
        # Keep prototype at full page size to avoid clipping strokes / filters / overflow
        # that can be lost when tightly cropping to the first use bbox.
        if page_proto_svg is not None and first_px is not None:
            proto_w = max(1.0, (first_px[2] - first_px[0]) / float(coord_scale or 1.0))
            proto_h = max(1.0, (first_px[3] - first_px[1]) / float(coord_scale or 1.0))
        else:
            proto_w = max(1.0, page_boxes[i].w)
            proto_h = max(1.0, page_boxes[i].h)
        cand_box = PageBox(
            page_id=f"proto_{i+1}",
            x=0.0,
            y=0.0,
            w=proto_w,
            h=proto_h,
            order=1,
        )
        # Pick prototype from the densest page (usually stable dm pages, not template-ish page 1).
        if proto_candidate is None or len(rects_px) > proto_candidate[2]:
            rep_svg = page_proto_svg if page_proto_svg is not None else _build_proto_svg(psvg)
            if rep_svg is not None:
                proto_candidate = (rep_svg, cand_box, len(rects_px), _px_to_pt(first_px, dpi=dpi))
        all_rects_pt.append([_px_to_pt(r, dpi=dpi) for r in rects_px])
        opt_pages += 1
        _log(f"page {i+1}: optimized uses={len(rects_px)}")

    if proto_candidate is not None:
        proto_svg, proto_box, _, proto_src_rect_pt = proto_candidate
    else:
        proto_src_rect_pt = None

    if opt_pages == 0 or proto_svg is None or proto_box is None:
        out = _render_pages_to_pdf_bytes(page_svgs, page_boxes, unit_to_pt=unit_to_pt)
        Path(pdf_abs).write_bytes(out)
        return len(page_svgs), len(page_svgs), {"engine": "rsvg", "optimized": False, "reason": "no-optimized-pages", "elapsed_sec": round(time.perf_counter() - t0, 3)}

    base_pdf = _render_pages_to_pdf_bytes(base_svgs, page_boxes, unit_to_pt=unit_to_pt)
    proto_pdf = _render_pages_to_pdf_bytes([proto_svg], [proto_box], unit_to_pt=unit_to_pt)

    if overlay_job_dir:
        jd = Path(overlay_job_dir)
        jd.mkdir(parents=True, exist_ok=True)
        if not skip_overlay:
            (jd / "base.pdf").write_bytes(base_pdf)
        (jd / "proto.pdf").write_bytes(proto_pdf)
        payload = {"all_rects_pt": [[list(r) for r in page] for page in all_rects_pt]}
        if skip_overlay:
            payload["base_pdf"] = pdf_abs
        if proto_src_rect_pt is not None:
            payload["proto_src_rect_pt"] = list(proto_src_rect_pt)
        (jd / "placements.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if skip_overlay:
        Path(pdf_abs).write_bytes(base_pdf)
        return len(page_svgs), len(page_svgs), {"engine": "rsvg", "optimized": True, "reason": "overlay-skipped", "overlay_job_dir": str(Path(overlay_job_dir).resolve()) if overlay_job_dir else None, "elapsed_sec": round(time.perf_counter() - t0, 3)}

    import fitz

    bdoc = fitz.open(stream=base_pdf, filetype="pdf")
    pdoc = fitz.open(stream=proto_pdf, filetype="pdf")
    supports_reuse = "reuse_xref" in __import__("inspect").signature(fitz.Page.show_pdf_page).parameters
    src_bbox = fitz.Rect(*proto_src_rect_pt) if proto_src_rect_pt is not None else _proto_content_bbox(pdoc[0], fitz)
    templates, template_by_page, template_count = _build_overlay_templates(
        fitz, bdoc, pdoc, all_rects_pt, src_bbox, supports_reuse
    )
    reuse_by_template: dict[int, int] = {}
    for i in range(bdoc.page_count):
        template_index = template_by_page[i] if i < len(template_by_page) else None
        if template_index is None:
            continue
        pg = bdoc[i]
        if supports_reuse:
            reuse = reuse_by_template.get(template_index, 0)
            reuse_by_template[template_index] = pg.show_pdf_page(
                pg.rect, templates, template_index, keep_proportion=False, overlay=True, reuse_xref=reuse
            )
        else:
            pg.show_pdf_page(pg.rect, templates, template_index, keep_proportion=False, overlay=True)

    bdoc.save(pdf_abs, garbage=3, deflate=True)
    templates.close()
    bdoc.close()
    pdoc.close()

    return len(page_svgs), len(page_svgs), {"engine": "rsvg+pymupdf", "optimized": True, "optimized_pages": opt_pages, "overlay_templates": template_count, "elapsed_sec": round(time.perf_counter() - t0, 3)}


def main(argv: list[str] | None = None) -> int:
    global _LOG_FP
    _prepare_path()
    ap = argparse.ArgumentParser(description="SVG->PDF rsvg exporter (simple rewind mode)")
    ap.add_argument("input_svg")
    ap.add_argument("output_pdf", nargs="?")
    ap.add_argument("-o", "--output")
    ap.add_argument("--no-inline-images", action="store_true")
    ap.add_argument("--overlay-job-dir")
    ap.add_argument("--skip-overlay", action="store_true")
    ap.add_argument("--no-debug", action="store_true", help="Accepted for compatibility; this exporter only writes the run log.")
    args = ap.parse_args(argv)

    inp = Path(args.input_svg).expanduser().resolve()
    out_arg = args.output if args.output else args.output_pdf
    outp = Path(out_arg).expanduser().resolve() if out_arg else inp.with_suffix(".pdf")
    _LOG_FP = open(str(outp) + ".log", "w", encoding="utf-8")

    try:
        rendered, total, meta = convert_svg_to_pdf_rsvg(
            str(inp),
            str(outp),
            inline_images=not bool(args.no_inline_images),
            overlay_job_dir=args.overlay_job_dir,
            skip_overlay=bool(args.skip_overlay),
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    run = outp.with_suffix(outp.suffix + ".run.json")
    run.write_text(json.dumps({"input_svg": str(inp), "output_pdf": str(outp), "rendered_pages": rendered, "total_pages": total, "mode": meta}, indent=2), encoding="utf-8")
    print(f"OK: rendered {rendered}/{total} page(s) -> {outp}")
    print(f"MODE: {meta}")
    print(f"RUN: {run}")
    if _LOG_FP is not None:
        _LOG_FP.close()
        _LOG_FP = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
