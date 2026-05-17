# -*- coding: utf-8 -*-
"""Convert DeckMaker SVG output into a multi-page PDF using librsvg + cairo.

This converter reads page groups created by DeckMaker under the `pnpink-output`
group (children with `data-pnpink-page-id`) and recreates a PDF page for each
group in order.

If the SVG has no generated page groups, it falls back to a single-page render
of the full document using the root SVG size.
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import sys
import urllib.parse
import io
from pathlib import Path
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Gio, GLib, Rsvg

try:
    import cairo  # type: ignore
except Exception:
    import cairocffi as cairo  # type: ignore


INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_UNIT_TO_PT = {
    "pt": 1.0,
    "px": 72.0 / 96.0,
    "in": 72.0,
    "cm": 72.0 / 2.54,
    "mm": 72.0 / 25.4,
    "q": 72.0 / 101.6,
    "pc": 12.0,
}


@dataclass(frozen=True)
class PageBox:
    page_id: str
    x: float
    y: float
    w: float
    h: float
    order: int


@dataclass(frozen=True)
class PageGroup:
    group_id: str
    page_id: str
    page_index: int | None


def _as_float(value: str | None, default: float = 0.0) -> float:
    text = str(value or "").strip()
    if not text:
        return float(default)
    m = re.match(r"^\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))", text)
    if not m:
        return float(default)
    return float(m.group(1))


def _split_length(value: str | None) -> tuple[float, str]:
    text = str(value or "").strip()
    if not text:
        return 0.0, ""
    m = re.match(r"^\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*([A-Za-z%]*)\s*$", text)
    if not m:
        return _as_float(text, 0.0), ""
    return float(m.group(1)), m.group(2).lower()


def _length_to_points(value: str | None, *, fallback_unit: str = "px") -> float:
    num, unit = _split_length(value)
    if not unit:
        unit = fallback_unit
    return float(num) * float(_UNIT_TO_PT.get(unit, _UNIT_TO_PT.get(fallback_unit, 72.0 / 96.0)))


def _root_page_size_points(root: ET.Element) -> tuple[float, float]:
    width_pt = _length_to_points(root.get("width"), fallback_unit="px")
    height_pt = _length_to_points(root.get("height"), fallback_unit="px")
    return max(1.0, width_pt), max(1.0, height_pt)


def _doc_user_unit_scale_to_points(root: ET.Element) -> float:
    view_box = str(root.get("viewBox") or "").strip()
    if not view_box:
        return 72.0 / 96.0
    parts = [p for p in re.split(r"[,\s]+", view_box) if p]
    if len(parts) != 4:
        return 72.0 / 96.0
    vb_w = _as_float(parts[2], 0.0)
    vb_h = _as_float(parts[3], 0.0)
    root_w = str(root.get("width") or "").strip()
    root_h = str(root.get("height") or "").strip()
    sx = _length_to_points(root_w) / vb_w if vb_w > 0.0 and root_w else 72.0 / 96.0
    sy = _length_to_points(root_h) / vb_h if vb_h > 0.0 and root_h else 72.0 / 96.0
    if sx > 0.0 and sy > 0.0:
        return (sx + sy) * 0.5
    return sx if sx > 0.0 else (sy if sy > 0.0 else 72.0 / 96.0)


def _read_pages(root: ET.Element) -> dict[str, PageBox]:
    pages: dict[str, PageBox] = {}
    order = 0
    for el in root.findall(f".//{{{INKSCAPE_NS}}}page"):
        page_id = str(el.get("id") or "").strip()
        if not page_id:
            continue
        order += 1
        pages[page_id] = PageBox(
            page_id=page_id,
            x=_as_float(el.get("x"), 0.0),
            y=_as_float(el.get("y"), 0.0),
            w=max(1.0, _as_float(el.get("width"), 1.0)),
            h=max(1.0, _as_float(el.get("height"), 1.0)),
            order=order,
        )
    return pages


def _read_page_groups(root: ET.Element) -> list[PageGroup]:
    out_root = root.find(".//*[@id='pnpink-output']")
    if out_root is None:
        return []
    groups: list[PageGroup] = []
    for child in list(out_root):
        if not isinstance(getattr(child, "tag", None), str):
            continue
        page_id = str(child.get("data-pnpink-page-id") or "").strip()
        if not page_id:
            continue
        group_id = str(child.get("id") or "").strip()
        if not group_id:
            continue
        idx_txt = str(child.get("data-pnpink-page-index") or "").strip()
        try:
            page_index = int(idx_txt) if idx_txt else None
        except Exception:
            page_index = None
        groups.append(PageGroup(group_id=group_id, page_id=page_id, page_index=page_index))
    return groups


def _sort_groups(groups: list[PageGroup], pages_by_id: dict[str, PageBox]) -> list[PageGroup]:
    def key(item: PageGroup) -> tuple[int, int, str]:
        p = pages_by_id.get(item.page_id)
        page_order = p.order if p is not None else 10**9
        idx = int(item.page_index) if item.page_index is not None else 10**9
        return (page_order, idx, item.group_id)

    return sorted(groups, key=key)


def _build_single_page_svg_bytes(root: ET.Element, *, group_id: str, box: PageBox | None = None) -> bytes:
    import copy

    page_root = copy.deepcopy(root)

    out_root = page_root.find(".//*[@id='pnpink-output']")
    if out_root is not None:
        keep = None
        for child in list(out_root):
            child_id = str(child.get("id") or "").strip()
            child_page_id = str(child.get("data-pnpink-page-id") or "").strip()
            if child_id == group_id:
                keep = child
                continue
            if child_page_id:
                # Other page groups are removed entirely.
                out_root.remove(child)
                continue
            # Keep helper nodes for refs, but never render them directly.
            style = str(child.get("style") or "").strip()
            if "display:none" not in style.replace(" ", ""):
                child.set("style", (style + (";" if style and not style.endswith(";") else "") + "display:none").strip(";"))
        if keep is None:
            raise ValueError(f"Page group not found in output root: {group_id}")
        if box is not None:
            # Rebase selected page-group into local page coordinates.
            t0 = f"translate({-box.x:.12g},{-box.y:.12g})"
            old_t = str(keep.get("transform") or "").strip()
            keep.set("transform", (t0 + (" " + old_t if old_t else "")).strip())
    return ET.tostring(page_root, encoding="utf-8", xml_declaration=True)


def _normalize_href_value(raw: str, *, svg_dir: str) -> str:
    v = str(raw or "").strip()
    if not v:
        return v
    if v.startswith("#") or v.startswith("data:") or v.startswith("http://") or v.startswith("https://"):
        return v
    v = v.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", v):
        return Path(v).as_uri()
    if v.lower().startswith("file://") and not v.lower().startswith("file:///"):
        tail = v[7:].replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", tail):
            return f"file:///{tail}"
    # Relative filesystem path: resolve against the original SVG directory.
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", v):
        try:
            abs_p = os.path.abspath(os.path.join(svg_dir, v))
            return Path(abs_p).as_uri()
        except Exception:
            return v
    return v


def _normalize_svg_hrefs(root: ET.Element, *, svg_dir: str) -> int:
    changed = 0
    href_keys = ("href", f"{{{XLINK_NS}}}href")
    for el in root.iter():
        for key in href_keys:
            val = el.get(key)
            if not val:
                continue
            nv = _normalize_href_value(val, svg_dir=svg_dir)
            if nv != val:
                el.set(key, nv)
                changed += 1
    return changed


def _uri_to_local_path(uri_or_path: str) -> str:
    s = str(uri_or_path or "").strip()
    if not s:
        return ""
    if s.lower().startswith("file://"):
        parsed = urllib.parse.urlparse(s)
        p = urllib.parse.unquote(parsed.path or "")
        if re.match(r"^/[A-Za-z]:/", p):
            p = p[1:]
        return p.replace("/", os.sep)
    return s


def _collect_images_debug(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    for el in root.findall(f".//{{{SVG_NS}}}image"):
        node_id = str(el.get("id") or "").strip()
        href = str(el.get("href") or el.get(f"{{{XLINK_NS}}}href") or "").strip()
        x = _as_float(el.get("x"), 0.0)
        y = _as_float(el.get("y"), 0.0)
        w = _as_float(el.get("width"), 0.0)
        h = _as_float(el.get("height"), 0.0)
        out.append({"id": node_id, "href": href, "x": x, "y": y, "w": w, "h": h})
    return out


def _parse_transform_to_matrix(transform: str) -> tuple[float, float, float, float, float, float]:
    # Cairo matrix components: xx, yx, xy, yy, x0, y0
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    txt = str(transform or "").strip()
    if not txt:
        return m
    for name, args in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", txt):
        vals = [float(x) for x in re.split(r"[,\s]+", args.strip()) if x]
        name = name.lower()
        if name == "matrix" and len(vals) == 6:
            a, b, c, d, e, f = vals
            t = (a, b, c, d, e, f)
        elif name == "translate" and len(vals) >= 1:
            tx = vals[0]
            ty = vals[1] if len(vals) > 1 else 0.0
            t = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale" and len(vals) >= 1:
            sx = vals[0]
            sy = vals[1] if len(vals) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        else:
            continue
        m = _mat_mul(m, t)
    return m


def _mat_mul(m1: tuple[float, float, float, float, float, float], m2: tuple[float, float, float, float, float, float]) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _set_surface_dedup_mime(surface: object, *, unique_id: str, png_bytes: bytes | None) -> None:
    mime_uid = getattr(cairo, "MIME_TYPE_UNIQUE_ID", None)
    mime_png = getattr(cairo, "MIME_TYPE_PNG", None)
    if mime_uid:
        try:
            surface.set_mime_data(mime_uid, unique_id.encode("utf-8"))
        except Exception:
            pass
    if mime_png and png_bytes:
        try:
            surface.set_mime_data(mime_png, png_bytes)
        except Exception:
            pass


def _load_png_surface_from_href(href: str, *, svg_dir: str, cache: dict[str, object]) -> object | None:
    key = str(href or "")
    if key in cache:
        return cache[key]
    try:
        if key.startswith("data:image/png;base64,"):
            raw = base64.b64decode(key.split(",", 1)[1])
            surf = cairo.ImageSurface.create_from_png(io.BytesIO(raw))
            _set_surface_dedup_mime(surf, unique_id=f"pnpink:{hash(key)}", png_bytes=raw)
            cache[key] = surf
            return surf
        local = _uri_to_local_path(key)
        if not os.path.isabs(local):
            local = os.path.abspath(os.path.join(svg_dir, local))
        if os.path.isfile(local) and local.lower().endswith(".png"):
            with open(local, "rb") as fh:
                raw = fh.read()
            surf = cairo.ImageSurface.create_from_png(local)
            _set_surface_dedup_mime(surf, unique_id=f"pnpink:{os.path.normcase(local)}", png_bytes=raw)
            cache[key] = surf
            return surf
    except Exception:
        pass
    cache[key] = None
    return None


def _iter_images_with_transform(node: ET.Element, parent_m: tuple[float, float, float, float, float, float]):
    my_m = _mat_mul(parent_m, _parse_transform_to_matrix(node.get("transform") or ""))
    tag = str(node.tag or "")
    if tag == f"{{{SVG_NS}}}image":
        yield node, my_m
    for ch in list(node):
        yield from _iter_images_with_transform(ch, my_m)


def _inline_image_hrefs(root: ET.Element, *, svg_dir: str) -> tuple[int, int]:
    changed = 0
    failed = 0
    href_keys = ("href", f"{{{XLINK_NS}}}href")
    cache: dict[str, str] = {}
    for el in root.findall(f".//{{{SVG_NS}}}image"):
        key_used = ""
        raw = ""
        for k in href_keys:
            v = str(el.get(k) or "").strip()
            if v:
                key_used = k
                raw = v
                break
        if not raw:
            continue
        if raw.startswith("data:") or raw.startswith("#") or raw.startswith("http://") or raw.startswith("https://"):
            continue
        local = _uri_to_local_path(raw)
        if not os.path.isabs(local):
            local = os.path.abspath(os.path.join(svg_dir, local))
        if not os.path.isfile(local):
            failed += 1
            continue
        cache_key = os.path.normcase(os.path.abspath(local))
        data_uri = cache.get(cache_key)
        if data_uri is None:
            mime, _enc = mimetypes.guess_type(local)
            if not mime:
                mime = "application/octet-stream"
            try:
                with open(local, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("ascii")
                data_uri = f"data:{mime};base64,{b64}"
                cache[cache_key] = data_uri
            except Exception:
                failed += 1
                continue
        mime, _enc = mimetypes.guess_type(local)
        if not mime:
            mime = "application/octet-stream"
        try:
            el.set(key_used, data_uri)
            if key_used == "href":
                el.set(f"{{{XLINK_NS}}}href", data_uri)
            else:
                el.set("href", data_uri)
            changed += 1
        except Exception:
            failed += 1
    return changed, failed


_RSVG_KEEP_IMAGE_DATA = getattr(getattr(Rsvg, "HandleFlags", None), "FLAG_KEEP_IMAGE_DATA", 0)


def _load_rsvg_handle(svg_bytes: bytes, *, base_path: str | None = None):
    base_file = None
    if base_path:
        try:
            base_file = Gio.File.new_for_path(base_path)
        except Exception:
            base_file = None
    if _RSVG_KEEP_IMAGE_DATA:
        try:
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(svg_bytes))
            return Rsvg.Handle.new_from_stream_sync(stream, base_file, _RSVG_KEEP_IMAGE_DATA, None)
        except Exception:
            pass
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(svg_bytes))
    return Rsvg.Handle.new_from_stream_sync(stream, base_file, Rsvg.HandleFlags.FLAGS_NONE, None)


def _bbox_intersects_page(x: float, y: float, w: float, h: float, box: PageBox) -> bool:
    if w <= 0.0 or h <= 0.0:
        return False
    x2 = x + w
    y2 = y + h
    px1 = box.x
    py1 = box.y
    px2 = box.x + box.w
    py2 = box.y + box.h
    return (x < px2) and (x2 > px1) and (y < py2) and (y2 > py1)


def convert_svg_pages_to_pdf(svg_path: str, pdf_path: str, *, inline_images: bool = False) -> tuple[int, int]:
    svg_abs = os.path.abspath(str(svg_path or "").strip())
    pdf_abs = os.path.abspath(str(pdf_path or "").strip())
    if not os.path.isfile(svg_abs):
        raise FileNotFoundError(f"SVG not found: {svg_abs}")
    if not pdf_abs:
        raise ValueError("Empty output PDF path")

    tree = ET.parse(svg_abs)
    root = tree.getroot()
    pages_by_id = _read_pages(root)
    groups = _sort_groups(_read_page_groups(root), pages_by_id)
    page_groups_mode = bool(groups) and bool(pages_by_id)
    group_fallback_mode = bool(groups) and not pages_by_id
    single_page_mode = not groups and not pages_by_id
    if pages_by_id and not groups:
        raise ValueError("No page groups found (expected children in #pnpink-output with data-pnpink-page-id)")

    svg_dir = os.path.dirname(svg_abs)
    href_changes = _normalize_svg_hrefs(root, svg_dir=svg_dir)
    inlined = 0
    inline_failed = 0
    if inline_images:
        inlined, inline_failed = _inline_image_hrefs(root, svg_dir=svg_dir)

    if single_page_mode:
        first_w_pt, first_h_pt = _root_page_size_points(root)
        page_count = 1
    elif page_groups_mode:
        unit_scale = _doc_user_unit_scale_to_points(root)
        first_box = pages_by_id.get(groups[0].page_id)
        if first_box is None:
            raise ValueError(f"Missing page geometry for '{groups[0].page_id}'")
        first_w_pt = max(1.0, float(first_box.w) * unit_scale)
        first_h_pt = max(1.0, float(first_box.h) * unit_scale)
        page_count = len(groups)
    else:
        first_w_pt, first_h_pt = _root_page_size_points(root)
        page_count = len(groups)

    os.makedirs(os.path.dirname(pdf_abs) or ".", exist_ok=True)
    surface = cairo.PDFSurface(pdf_abs, first_w_pt, first_h_pt)
    ctx = cairo.Context(surface)

    rendered = 0
    if single_page_mode:
        ctx.save()
        ctx.rectangle(0.0, 0.0, first_w_pt, first_h_pt)
        ctx.clip()
        page_svg = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        handle = _load_rsvg_handle(page_svg, base_path=svg_abs)
        viewport = Rsvg.Rectangle()
        viewport.x = 0.0
        viewport.y = 0.0
        viewport.width = first_w_pt
        viewport.height = first_h_pt
        handle.render_document(ctx, viewport)
        ctx.restore()
        surface.show_page()
        rendered = 1
    else:
        unit_scale = _doc_user_unit_scale_to_points(root)
        for i, group in enumerate(groups):
            box = pages_by_id.get(group.page_id) if page_groups_mode else None
            page_w_pt, page_h_pt = (
                (max(1.0, float(box.w) * unit_scale), max(1.0, float(box.h) * unit_scale))
                if box is not None
                else (first_w_pt, first_h_pt)
            )
            if i > 0:
                surface.set_size(page_w_pt, page_h_pt)

            ctx.save()
            ctx.rectangle(0.0, 0.0, page_w_pt, page_h_pt)
            ctx.clip()
            page_svg = _build_single_page_svg_bytes(root, group_id=group.group_id, box=box)
            handle = _load_rsvg_handle(page_svg, base_path=svg_abs)
            viewport = Rsvg.Rectangle()
            viewport.x = 0.0
            viewport.y = 0.0
            viewport.width = page_w_pt
            viewport.height = page_h_pt
            handle.render_document(ctx, viewport)
            ctx.restore()
            surface.show_page()
            rendered += 1

    surface.finish()
    return rendered, page_count


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Convert DeckMaker page groups in SVG to a multi-page PDF (librsvg + cairo).")
    p.add_argument("svg", help="Input SVG path")
    p.add_argument("pdf", help="Output PDF path")
    p.add_argument("--no-inline-images", action="store_true", help="Keep external <image> hrefs instead of inlining them.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        rendered, total = convert_svg_pages_to_pdf(args.svg, args.pdf, inline_images=not bool(args.no_inline_images))
    except Exception as ex:
        print(f"[svg_pages_to_pdf] ERROR: {ex}", file=sys.stderr)
        return 1
    print(f"[svg_pages_to_pdf] OK: rendered {rendered}/{total} page groups -> {os.path.abspath(args.pdf)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
