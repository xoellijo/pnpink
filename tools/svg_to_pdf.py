#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Convert an SVG file to a PDF, including multi-page SVGs.

This uses CairoSVG as the rendering engine, but drives Cairo directly so we can
create a multi-page PDF one page at a time.
"""

from __future__ import annotations

import argparse
import base64
import copy
import mimetypes
import os
import re
import sys
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
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
    element: ET.Element
    group_id: str
    page_id: str
    page_index: int | None
    order: int


@dataclass(frozen=True)
class SvgSize:
    width_attr: str
    height_attr: str
    viewbox: str
    document_units: str


def _default_inkscape_bin() -> Path | None:
    candidate = Path.home() / "inkscape" / "bin"
    return candidate if candidate.exists() else None


def _prepare_path() -> None:
    if os.name != "nt":
        return
    candidate = os.environ.get("PNPINK_INKSCAPE_BIN", "").strip()
    if candidate:
        bin_dir = Path(candidate)
        if bin_dir.exists():
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            return
    bin_dir = _default_inkscape_bin()
    if bin_dir is not None:
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _as_float(value: str | None, default: float = 0.0) -> float:
    text = str(value or "").strip()
    if not text:
        return float(default)
    m = re.match(r"^\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))", text)
    if not m:
        return float(default)
    return float(m.group(1))


def _length_to_px(value: str | None, default: float = 0.0) -> float:
    text = str(value or "").strip()
    if not text:
        return float(default)
    m = re.match(r"^\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+))(?:\s*([a-zA-Z%]+))?\s*$", text)
    if not m:
        return _as_float(text, default)
    num = float(m.group(1))
    unit = (m.group(2) or "px").lower()
    if unit == "px":
        return num
    if unit == "mm":
        return num * 96.0 / 25.4
    if unit == "cm":
        return num * 96.0 / 2.54
    if unit == "in":
        return num * 96.0
    if unit == "pt":
        return num * 96.0 / 72.0
    if unit == "pc":
        return num * 16.0
    return num


def _length_to_points(value: str | None, default: float = 0.0, *, dpi: float = 96.0) -> float:
    return _length_to_px(value, default=default) * 72.0 / float(dpi or 96.0)


def _read_pages(root: ET.Element) -> list[PageBox]:
    pages: list[PageBox] = []
    order = 0
    for el in root.findall(f".//{{{INKSCAPE_NS}}}page"):
        page_id = str(el.get("id") or "").strip()
        if not page_id:
            continue
        order += 1
        pages.append(
            PageBox(
                page_id=page_id,
                x=_length_to_px(el.get("x"), 0.0),
                y=_length_to_px(el.get("y"), 0.0),
                w=max(1.0, _length_to_px(el.get("width"), 1.0)),
                h=max(1.0, _length_to_px(el.get("height"), 1.0)),
                order=order,
            )
        )
    return pages


def _read_page_groups(root: ET.Element) -> list[PageGroup]:
    out_root = root.find(".//*[@id='pnpink-output']")
    containers: Iterable[ET.Element]
    if out_root is not None:
        containers = list(out_root)
    else:
        containers = [el for el in list(root) if isinstance(getattr(el, "tag", None), str)]

    groups: list[PageGroup] = []
    order = 0
    for child in containers:
        if not isinstance(getattr(child, "tag", None), str):
            continue
        tag = str(child.tag or "")
        if not tag.endswith("g"):
            continue
        order += 1
        page_id = str(child.get("data-pnpink-page-id") or "").strip()
        group_id = str(child.get("id") or "").strip()
        if not group_id:
            continue
        idx_txt = str(child.get("data-pnpink-page-index") or "").strip()
        try:
            page_index = int(idx_txt) if idx_txt else None
        except Exception:
            page_index = None
        groups.append(
            PageGroup(
                element=child,
                group_id=group_id,
                page_id=page_id,
                page_index=page_index,
                order=order,
            )
        )
    return groups


def _read_svg_size(root: ET.Element) -> SvgSize:
    width_attr = str(root.get("width") or "100%").strip()
    height_attr = str(root.get("height") or "100%").strip()
    viewbox = str(root.get("viewBox") or "").strip()
    namedview = root.find(f".//{{{SODIPODI_NS}}}namedview")
    document_units = ""
    if namedview is not None:
        document_units = str(namedview.get(f"{{{INKSCAPE_NS}}}document-units") or "").strip().lower()
    return SvgSize(width_attr=width_attr, height_attr=height_attr, viewbox=viewbox, document_units=document_units)


def _pair_groups_to_pages(groups: list[PageGroup], pages: list[PageBox]) -> list[tuple[PageGroup, PageBox]]:
    if not pages:
        raise ValueError("No inkscape:page nodes found in SVG")
    if not groups:
        raise ValueError("No page groups found")

    by_page_id = {page.page_id: page for page in pages}
    ordered_pages = sorted(pages, key=lambda p: (p.order, p.page_id))

    result: list[tuple[PageGroup, PageBox]] = []
    free_pages = ordered_pages[:]
    for group in groups:
        page = by_page_id.get(group.page_id)
        if page is None and group.page_index is not None:
            if 0 <= group.page_index < len(ordered_pages):
                page = ordered_pages[group.page_index]
        if page is None and free_pages:
            page = free_pages.pop(0)
        if page is None:
            raise ValueError(f"Could not associate group '{group.group_id}' with a page box")
        result.append((group, page))
    return result


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
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", v):
        try:
            abs_p = os.path.abspath(os.path.join(svg_dir, v))
            return Path(abs_p).as_uri()
        except Exception:
            return v
    return v


def _inline_image_hrefs(root: ET.Element, *, svg_dir: str) -> tuple[int, int]:
    changed = 0
    failed = 0
    href_keys = ("href", f"{{{XLINK_NS}}}href")
    for el in root.findall(f".//{{{SVG_NS}}}image"):
        key_used = ""
        raw = ""
        for key in href_keys:
            val = str(el.get(key) or "").strip()
            if val:
                key_used = key
                raw = val
                break
        if not raw:
            continue
        if raw.startswith("data:") or raw.startswith("#") or raw.startswith("http://") or raw.startswith("https://"):
            continue
        local = _normalize_href_value(raw, svg_dir=svg_dir)
        if local.startswith("file://"):
            from urllib.parse import urlparse, unquote

            parsed = urlparse(local)
            local = unquote(parsed.path or "")
            if re.match(r"^/[A-Za-z]:/", local):
                local = local[1:]
            local = local.replace("/", os.sep)
        if not os.path.isabs(local):
            local = os.path.abspath(os.path.join(svg_dir, local))
        if not os.path.isfile(local):
            failed += 1
            continue
        mime, _enc = mimetypes.guess_type(local)
        if not mime:
            mime = "application/octet-stream"
        try:
            with open(local, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            data_uri = f"data:{mime};base64,{b64}"
            # CairoSVG may consult either href or xlink:href depending on the file.
            el.set("href", data_uri)
            el.set(f"{{{XLINK_NS}}}href", data_uri)
            changed += 1
        except Exception:
            failed += 1
    return changed, failed


def _clone_page_svg(root: ET.Element, group: PageGroup, box: PageBox, *, svg_size: SvgSize) -> bytes:
    page_root = copy.deepcopy(root)
    page_root.set("width", svg_size.width_attr)
    page_root.set("height", svg_size.height_attr)
    page_root.set("viewBox", svg_size.viewbox)
    page_root.set("overflow", "hidden")

    keep = None
    out_root = page_root.find(".//*[@id='pnpink-output']")
    if out_root is not None:
        for child in list(out_root):
            if not isinstance(getattr(child, "tag", None), str):
                continue
            child_id = str(child.get("id") or "").strip()
            child_page_id = str(child.get("data-pnpink-page-id") or "").strip()
            if child_id == group.group_id:
                keep = child
                continue
            if child_page_id:
                out_root.remove(child)
                continue
            if child.tag.endswith("g"):
                style = str(child.get("style") or "").strip()
                if "display:none" not in style.replace(" ", ""):
                    child.set("style", (style + (";" if style and not style.endswith(";") else "") + "display:none").strip(";"))
    else:
        for child in list(page_root):
            if not isinstance(getattr(child, "tag", None), str):
                continue
            child_id = str(child.get("id") or "").strip()
            child_page_id = str(child.get("data-pnpink-page-id") or "").strip()
            if child_id == group.group_id:
                keep = child
                continue
            if child_page_id:
                page_root.remove(child)
                continue
            if child.tag.endswith("g"):
                style = str(child.get("style") or "").strip()
                if "display:none" not in style.replace(" ", ""):
                    child.set("style", (style + (";" if style and not style.endswith(";") else "") + "display:none").strip(";"))
    if keep is None:
        keep = page_root.find(f".//*[@id='{group.group_id}']")
    if keep is None:
        raise ValueError(f"Page group not found: {group.group_id}")
    if box.x or box.y:
        shift = f"translate({-box.x:g},{-box.y:g})"
        existing = str(keep.get("transform") or "").strip()
        keep.set("transform", f"{shift} {existing}".strip() if existing else shift)
    return ET.tostring(page_root, encoding="utf-8", xml_declaration=True)


def _load_cairo_stack():
    import cairosvg.surface as surface_mod
    from cairosvg.parser import Tree

    return surface_mod, Tree


class _PageRenderer:
    def __init__(self, surface_mod, cairo_surface, *, dpi: float = 96.0):
        self.surface_mod = surface_mod
        self.cairo = surface_mod.cairo
        self.surface = cairo_surface
        self.context = self.cairo.Context(cairo_surface)
        self.dpi = float(dpi)
        self.context_width = 0.0
        self.context_height = 0.0
        self.cursor_position = [0, 0]
        self.cursor_d_position = [0, 0]
        self.text_path_width = 0
        self.tree_cache = {}
        self.reference_count = 0
        self.markers = {}
        self.gradients = {}
        self.patterns = {}
        self.masks = {}
        self.paths = {}
        self.filters = {}
        self.images = {}
        self._old_parent_node = None
        self.parent_node = None
        self.font_size = self.surface_mod.size(self, "12pt")
        self.stroke_and_fill = True
        self.map_rgba = None
        self.map_image = None

    @property
    def device_units_per_user_units(self) -> float:
        return 1 / (self.dpi * self.surface_mod.UNITS["pt"])

    def map_color(self, string, opacity=1):
        rgba = self.surface_mod.color(string, opacity)
        return self.map_rgba(rgba) if self.map_rgba else rgba

    set_context_size = None  # bound below
    draw = None  # bound below


def _create_renderer(surface_mod, cairo_surface, *, dpi: float = 96.0) -> _PageRenderer:
    renderer = _PageRenderer(surface_mod, cairo_surface, dpi=dpi)
    renderer.set_context_size = surface_mod.Surface.set_context_size.__get__(renderer, _PageRenderer)
    renderer.draw = surface_mod.Surface.draw.__get__(renderer, _PageRenderer)
    renderer.context.scale(renderer.device_units_per_user_units, renderer.device_units_per_user_units)
    return renderer


def _document_unit_to_points_factor(document_units: str, *, dpi: float = 96.0) -> float:
    unit = str(document_units or "px").strip().lower()
    if unit == "mm":
        return 72.0 / 25.4
    if unit == "cm":
        return 72.0 / 2.54
    if unit == "in":
        return 72.0
    if unit == "pt":
        return 1.0
    if unit == "pc":
        return 12.0
    if unit == "px":
        return 72.0 / float(dpi or 96.0)
    return 72.0 / 25.4


def _svg_dimensions_points(svg_size: SvgSize, *, dpi: float = 96.0) -> tuple[float, float]:
    return _length_to_points(svg_size.width_attr, dpi=dpi), _length_to_points(svg_size.height_attr, dpi=dpi)


def convert_svg_to_pdf(svg_path: str, pdf_path: str, *, inline_images: bool = True, dpi: float = 96.0) -> tuple[int, int]:
    svg_abs = os.path.abspath(str(svg_path or "").strip())
    pdf_abs = os.path.abspath(str(pdf_path or "").strip())
    if not os.path.isfile(svg_abs):
        raise FileNotFoundError(f"SVG not found: {svg_abs}")
    if not pdf_abs:
        raise ValueError("Empty output PDF path")

    tree = ET.parse(svg_abs)
    root = tree.getroot()
    svg_size = _read_svg_size(root)
    pages = _read_pages(root)
    groups = _read_page_groups(root)
    if not pages:
        # Single-page fallback.
        width = _as_float(root.get("width"), 1.0)
        height = _as_float(root.get("height"), 1.0)
        pages = [PageBox(page_id="page1", x=0.0, y=0.0, w=max(1.0, width), h=max(1.0, height), order=1)]
    if not groups:
        # Treat the root SVG as a single page if no explicit groups exist.
        direct_groups = [el for el in list(root) if isinstance(getattr(el, "tag", None), str) and str(el.tag or "").endswith("g")]
        if direct_groups:
            groups = [
                PageGroup(
                    element=el,
                    group_id=str(el.get("id") or f"group{i+1}").strip(),
                    page_id=str(el.get("data-pnpink-page-id") or "").strip(),
                    page_index=None,
                    order=i + 1,
                )
                for i, el in enumerate(direct_groups)
            ]
        else:
            groups = [
                PageGroup(
                    element=root,
                    group_id=str(root.get("id") or "root").strip(),
                    page_id=pages[0].page_id,
                    page_index=0,
                    order=1,
                )
            ]

    svg_dir = os.path.dirname(svg_abs)
    href_changes = 0
    inline_failed = 0
    if inline_images:
        href_changes, inline_failed = _inline_image_hrefs(root, svg_dir=svg_dir)

    pairs = _pair_groups_to_pages(groups, pages)
    if not pairs:
        raise ValueError("No pages could be resolved from the SVG")

    surface_mod, Tree = _load_cairo_stack()
    first_w_pt, first_h_pt = _svg_dimensions_points(svg_size, dpi=dpi)
    pdf_surface = surface_mod.cairo.PDFSurface(pdf_abs, first_w_pt, first_h_pt)

    rendered = 0
    for idx, (group, box) in enumerate(pairs):
        if idx > 0:
            pdf_surface.set_size(first_w_pt, first_h_pt)
        renderer = _create_renderer(surface_mod, pdf_surface, dpi=dpi)
        page_svg = _clone_page_svg(root, group, box, svg_size=svg_size)
        page_tree = Tree(bytestring=page_svg, unsafe=False)
        renderer.tree_cache[(page_tree.url, page_tree.get("id"))] = page_tree
        width, height, viewbox = surface_mod.node_format(renderer, page_tree)
        renderer.font_size = surface_mod.size(renderer, "12pt")
        renderer.parent_node = None
        renderer._old_parent_node = None
        renderer.cursor_position = [0, 0]
        renderer.cursor_d_position = [0, 0]
        renderer.text_path_width = 0
        renderer.set_context_size(width, height, viewbox, page_tree)
        renderer.draw(page_tree)
        pdf_surface.show_page()
        rendered += 1

    pdf_surface.finish()
    return rendered, len(pairs)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Convert an SVG file to a multi-page PDF using CairoSVG internals.")
    p.add_argument("input_svg", help="Path to the input SVG file")
    p.add_argument(
        "-o",
        "--output",
        help="Output PDF path. Defaults to the same name as the input with .pdf extension.",
    )
    p.add_argument(
        "--no-inline-images",
        action="store_true",
        help="Do not inline local <image> references as data: URIs before rendering.",
    )
    p.add_argument(
        "--debug-dir",
        help="Directory where per-page temporary SVGs and metadata will be written. Defaults to <output>.debug-svg",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    _prepare_path()

    try:
        args = _build_arg_parser().parse_args(argv)
        input_svg = Path(args.input_svg).expanduser().resolve()
        if not input_svg.is_file():
            print(f"ERROR: input SVG not found: {input_svg}", file=sys.stderr)
            return 2
        output_pdf = Path(args.output).expanduser().resolve() if args.output else input_svg.with_suffix(".pdf")
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        debug_dir = Path(args.debug_dir).expanduser().resolve() if args.debug_dir else output_pdf.with_suffix(output_pdf.suffix + ".debug-svg")

        rendered, total = convert_svg_to_pdf(
            str(input_svg),
            str(output_pdf),
            inline_images=not bool(args.no_inline_images),
        )

        # Rebuild the page SVGs for inspection and keep them on disk.
        debug_dir.mkdir(parents=True, exist_ok=True)
        tree = ET.parse(str(input_svg))
        root = tree.getroot()
        svg_size = _read_svg_size(root)
        pages = _read_pages(root)
        groups = _read_page_groups(root)
        if not pages:
            width = _as_float(root.get("width"), 1.0)
            height = _as_float(root.get("height"), 1.0)
            pages = [PageBox(page_id="page1", x=0.0, y=0.0, w=max(1.0, width), h=max(1.0, height), order=1)]
        if not groups:
            direct_groups = [el for el in list(root) if isinstance(getattr(el, "tag", None), str) and str(el.tag or "").endswith("g")]
            if direct_groups:
                groups = [
                    PageGroup(
                        element=el,
                        group_id=str(el.get("id") or f"group{i+1}").strip(),
                        page_id=str(el.get("data-pnpink-page-id") or "").strip(),
                        page_index=None,
                        order=i + 1,
                    )
                    for i, el in enumerate(direct_groups)
                ]
            else:
                groups = [
                    PageGroup(
                        element=root,
                        group_id=str(root.get("id") or "root").strip(),
                        page_id=pages[0].page_id,
                        page_index=0,
                        order=1,
                    )
                ]
        if bool(args.no_inline_images):
            inline_changed = 0
            inline_failed = 0
        else:
            inline_changed, inline_failed = _inline_image_hrefs(root, svg_dir=str(input_svg.parent))
        pairs = _pair_groups_to_pages(groups, pages)
        manifest = {
            "input_svg": str(input_svg),
            "output_pdf": str(output_pdf),
            "rendered_pages": rendered,
            "total_pages": total,
            "inline_changed": inline_changed,
            "inline_failed": inline_failed,
            "pages": [
                {
                    "index": i + 1,
                    "group_id": group.group_id,
                    "page_id": box.page_id,
                    "x": box.x,
                    "y": box.y,
                    "w": box.w,
                    "h": box.h,
                }
                for i, (group, box) in enumerate(pairs)
            ],
        }
        (debug_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for i, (group, box) in enumerate(pairs, start=1):
            page_svg = _clone_page_svg(root, group, box, svg_size=svg_size)
            name = f"{i:03d}_{group.group_id or 'page'}.svg"
            (debug_dir / name).write_bytes(page_svg)
        shutil.copy2(input_svg, debug_dir / "_input_original.svg")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"OK: rendered {rendered}/{total} page(s) -> {output_pdf}")
    print(f"DEBUG SVGs -> {debug_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
