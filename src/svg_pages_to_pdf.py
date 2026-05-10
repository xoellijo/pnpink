# -*- coding: utf-8 -*-
"""Convert DeckMaker SVG output into a multi-page PDF using librsvg + cairo.

This converter reads page groups created by DeckMaker under the `pnpink-output`
group (children with `data-pnpink-page-id`) and recreates a PDF page for each
group in order.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg

try:
    import cairo  # type: ignore
except Exception:
    import cairocffi as cairo  # type: ignore


INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"


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


def convert_svg_pages_to_pdf(svg_path: str, pdf_path: str) -> tuple[int, int]:
    svg_abs = os.path.abspath(str(svg_path or "").strip())
    pdf_abs = os.path.abspath(str(pdf_path or "").strip())
    if not os.path.isfile(svg_abs):
        raise FileNotFoundError(f"SVG not found: {svg_abs}")
    if not pdf_abs:
        raise ValueError("Empty output PDF path")

    tree = ET.parse(svg_abs)
    root = tree.getroot()
    pages_by_id = _read_pages(root)
    if not pages_by_id:
        raise ValueError("No inkscape:page nodes found in SVG")

    groups = _sort_groups(_read_page_groups(root), pages_by_id)
    if not groups:
        raise ValueError("No page groups found (expected children in #pnpink-output with data-pnpink-page-id)")

    normalized_svg_path = svg_abs
    temp_svg_to_cleanup = ""
    href_changes = _normalize_svg_hrefs(root, svg_dir=os.path.dirname(svg_abs))
    if href_changes > 0:
        fd, tmp_svg = tempfile.mkstemp(prefix="pnpink_norm_", suffix=".svg")
        os.close(fd)
        ET.ElementTree(root).write(tmp_svg, encoding="utf-8", xml_declaration=True)
        normalized_svg_path = tmp_svg
        temp_svg_to_cleanup = tmp_svg

    handle = Rsvg.Handle.new_from_file(normalized_svg_path)
    try:
        doc_w, doc_h = handle.get_intrinsic_size_in_pixels()
    except Exception:
        doc_w, doc_h = 0.0, 0.0
    if not doc_w or not doc_h:
        doc_w = max((p.x + p.w) for p in pages_by_id.values())
        doc_h = max((p.y + p.h) for p in pages_by_id.values())

    first_box = pages_by_id.get(groups[0].page_id)
    if first_box is None:
        raise ValueError(f"Missing page geometry for '{groups[0].page_id}'")

    os.makedirs(os.path.dirname(pdf_abs) or ".", exist_ok=True)
    surface = cairo.PDFSurface(pdf_abs, first_box.w, first_box.h)
    ctx = cairo.Context(surface)

    base_uri = Path(normalized_svg_path).as_uri()
    rendered = 0
    for i, group in enumerate(groups):
        box = pages_by_id.get(group.page_id)
        if box is None:
            continue
        if i > 0:
            surface.set_size(box.w, box.h)

        ctx.save()
        ctx.rectangle(0.0, 0.0, box.w, box.h)
        ctx.clip()
        ctx.translate(-box.x, -box.y)
        viewport = Rsvg.Rectangle()
        viewport.x = 0.0
        viewport.y = 0.0
        viewport.width = float(doc_w)
        viewport.height = float(doc_h)
        handle.render_layer(ctx, f"#{group.group_id}", viewport)
        ctx.restore()
        surface.show_page()
        rendered += 1

    surface.finish()
    if temp_svg_to_cleanup:
        try:
            os.remove(temp_svg_to_cleanup)
        except Exception:
            pass
    return rendered, len(groups)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Convert DeckMaker page groups in SVG to a multi-page PDF (librsvg + cairo).")
    p.add_argument("svg", help="Input SVG path")
    p.add_argument("pdf", help="Output PDF path")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    try:
        rendered, total = convert_svg_pages_to_pdf(args.svg, args.pdf)
    except Exception as ex:
        print(f"[svg_pages_to_pdf] ERROR: {ex}", file=sys.stderr)
        return 1
    print(f"[svg_pages_to_pdf] OK: rendered {rendered}/{total} page groups -> {os.path.abspath(args.pdf)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
