# -*- coding: utf-8 -*-
"""Simplify DeckMaker/Inkscape SVG into a more rsvg-friendly SVG.

Current passes:
1. Expand <use> references to inline cloned content.
2. Normalize image hrefs to file:/// absolute URIs when local.
"""

from __future__ import annotations

import argparse
import copy
import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET

XLINK_NS = "http://www.w3.org/1999/xlink"
SVG_NS = "http://www.w3.org/2000/svg"


def _is_tag(el: ET.Element, local: str) -> bool:
    t = str(el.tag or "")
    return t == f"{{{SVG_NS}}}{local}" or t.endswith("}" + local) or t == local


def _norm_uri_or_path(raw: str, *, svg_dir: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return s
    if s.startswith("#") or s.startswith("data:") or s.startswith("http://") or s.startswith("https://"):
        return s
    s = s.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", s):
        return Path(s).as_uri()
    if s.lower().startswith("file://") and not s.lower().startswith("file:///"):
        tail = s[7:].replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", tail):
            return f"file:///{tail}"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", s):
        ap = os.path.abspath(os.path.join(svg_dir, s))
        return Path(ap).as_uri()
    return s


def _append_transform(el: ET.Element, t: str) -> None:
    t_old = str(el.get("transform") or "").strip()
    if t_old:
        el.set("transform", f"{t} {t_old}")
    else:
        el.set("transform", t)


def _build_id_index(root: ET.Element) -> dict[str, ET.Element]:
    out: dict[str, ET.Element] = {}
    for el in root.iter():
        node_id = str(el.get("id") or "").strip()
        if node_id:
            out[node_id] = el
    return out


def _expand_uses(root: ET.Element) -> int:
    changed = 0
    id_index = _build_id_index(root)
    parent_map = {c: p for p in root.iter() for c in p}
    uses = [el for el in root.iter() if _is_tag(el, "use")]
    for use in uses:
        href = str(use.get("href") or use.get(f"{{{XLINK_NS}}}href") or "").strip()
        if not href.startswith("#"):
            continue
        ref = id_index.get(href[1:])
        if ref is None:
            continue
        parent = parent_map.get(use)
        if parent is None:
            continue
        idx = list(parent).index(use)
        clone = copy.deepcopy(ref)
        x = str(use.get("x") or "").strip()
        y = str(use.get("y") or "").strip()
        t = str(use.get("transform") or "").strip()
        tx = 0.0
        ty = 0.0
        try:
            tx = float(x) if x else 0.0
            ty = float(y) if y else 0.0
        except Exception:
            tx = 0.0
            ty = 0.0
        if tx or ty:
            _append_transform(clone, f"translate({tx:.12g},{ty:.12g})")
        if t:
            _append_transform(clone, t)
        for k, v in use.attrib.items():
            if k in {"href", f"{{{XLINK_NS}}}href", "x", "y", "transform"}:
                continue
            clone.set(k, v)
        parent.remove(use)
        parent.insert(idx, clone)
        changed += 1
    return changed


def _normalize_image_hrefs(root: ET.Element, *, svg_dir: str) -> int:
    changed = 0
    for el in root.iter():
        if not _is_tag(el, "image"):
            continue
        for k in ("href", f"{{{XLINK_NS}}}href"):
            v = el.get(k)
            if not v:
                continue
            nv = _norm_uri_or_path(v, svg_dir=svg_dir)
            if nv != v:
                el.set(k, nv)
                changed += 1
    return changed


def simplify_svg(input_svg: str, output_svg: str) -> tuple[int, int]:
    svg_abs = os.path.abspath(input_svg)
    out_abs = os.path.abspath(output_svg)
    tree = ET.parse(svg_abs)
    root = tree.getroot()
    use_count = _expand_uses(root)
    href_count = _normalize_image_hrefs(root, svg_dir=os.path.dirname(svg_abs))
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
    tree.write(out_abs, encoding="utf-8", xml_declaration=True)
    return use_count, href_count


def main() -> int:
    ap = argparse.ArgumentParser(description="Simplify SVG for librsvg/cairo rendering.")
    ap.add_argument("input_svg")
    ap.add_argument("output_svg")
    args = ap.parse_args()
    use_count, href_count = simplify_svg(args.input_svg, args.output_svg)
    print(f"[simplify_svg_for_rsvg] OK uses_expanded={use_count} href_normalized={href_count} out='{os.path.abspath(args.output_svg)}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

