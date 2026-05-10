# -*- coding: utf-8 -*-
"""Split DeckMaker output SVGs into smaller SVG chunks by page weight."""

from __future__ import annotations

import copy
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import log as LOG
import temp_paths as TEMPPATHS

_l = LOG

URL_REF_RE = re.compile(r"url\(#([^)]+)\)")
MATRIX_RE = re.compile(r"matrix\(\s*([^)]+?)\s*\)", re.IGNORECASE)
TRANSLATE_RE = re.compile(r"translate\(\s*([^)]+?)\s*\)", re.IGNORECASE)
LENGTH_UNIT_RE = re.compile(r"^\s*[-+]?(?:\d+(?:\.\d+)?|\.\d+)([A-Za-z%]+)?\s*$")


@dataclass(frozen=True)
class SvgPageSlice:
    page_no: int
    page_id: str
    x: float
    y: float
    w: float
    h: float
    est_bytes: int
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class SvgChunk:
    index: int
    pages: tuple[int, ...]
    est_bytes: int
    svg_path: str
    pdf_path: str
    png_prefix: str


def _svg_page_count(svg_path: str) -> int:
    try:
        from xml.etree import ElementTree as ET

        tree = ET.parse(svg_path)
        root = tree.getroot()
        count = 0
        for _el in root.findall(".//{http://www.inkscape.org/namespaces/inkscape}page"):
            count += 1
        return count if count > 0 else 1
    except Exception:
        return 1


def _svg_page_numbers(svg_path: str) -> tuple[int, ...]:
    try:
        from xml.etree import ElementTree as ET

        tree = ET.parse(svg_path)
        root = tree.getroot()
        out: list[int] = []
        for page in root.findall(".//{http://www.inkscape.org/namespaces/inkscape}page"):
            page_id = str(page.get("id") or "").strip()
            m = re.search(r"(\d+)$", page_id)
            if m:
                out.append(int(m.group(1)))
        if out:
            return tuple(out)
        count = _svg_page_count(svg_path)
        return tuple(range(1, count + 1))
    except Exception:
        count = _svg_page_count(svg_path)
        return tuple(range(1, count + 1))


def _chunk_dir_for_output(out_path: str) -> tuple[str, str, str]:
    out_path = os.path.normpath(out_path)
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    stem = Path(out_path).stem
    suffix = Path(out_path).suffix or ".svg"
    chunk_dir = os.path.join(out_dir, f"{stem}_chunks")
    manifest_path = os.path.join(chunk_dir, f"{stem}.chunks.txt")
    return chunk_dir, manifest_path, suffix


def _chunk_svg_path(chunk_dir: str, stem: str, suffix: str, index: int) -> str:
    return os.path.normpath(os.path.join(chunk_dir, f"{stem}.chunk{index:02d}{suffix or '.svg'}"))


def _chunk_pdf_path(work_dir: str, stem: str, index: int) -> str:
    return os.path.normpath(os.path.join(work_dir, f"{stem}.chunk{index:02d}.pdf"))


def _chunk_png_prefix(work_dir: str, stem: str, index: int) -> str:
    return os.path.normpath(os.path.join(work_dir, f"{stem}.chunk{index:02d}.png"))


def build_chunk_outputs(
    chunk_paths: list[str],
    *,
    artifact_dir: str,
    artifact_stem: str,
) -> tuple[SvgChunk, ...]:
    chunks: list[SvgChunk] = []
    for idx, chunk_svg_path in enumerate(chunk_paths, start=1):
        page_numbers = _svg_page_numbers(chunk_svg_path)
        chunks.append(
            SvgChunk(
                index=idx,
                pages=tuple(int(p) for p in page_numbers),
                est_bytes=0,
                svg_path=os.path.normpath(chunk_svg_path),
                pdf_path=_chunk_pdf_path(artifact_dir, artifact_stem, idx),
                png_prefix=_chunk_png_prefix(artifact_dir, artifact_stem, idx),
            )
        )
    return tuple(chunks)


def resolve_chunked_output(svg_path: str) -> dict:
    src = os.path.normpath(os.path.abspath(str(svg_path or "").strip()))
    if not src:
        return {"svg_path": "", "chunk_paths": [], "manifest_path": "", "chunk_dir": ""}
    chunk_dir, manifest_path, _suffix = _chunk_dir_for_output(src)
    stem = Path(src).stem
    chunk_paths: list[str] = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                chunk_paths = [os.path.normpath(line.strip()) for line in fh if line.strip()]
        except Exception:
            chunk_paths = []
    if not chunk_paths and os.path.isdir(chunk_dir):
        try:
            chunk_paths = sorted(
                os.path.normpath(os.path.join(chunk_dir, name))
                for name in os.listdir(chunk_dir)
                if re.fullmatch(rf"{re.escape(stem)}\.chunk\d+\.svg", name, re.IGNORECASE)
            )
        except Exception:
            chunk_paths = []
    chunk_paths = [path for path in chunk_paths if os.path.isfile(path)]
    return {
        "svg_path": src if os.path.isfile(src) else "",
        "chunk_paths": chunk_paths,
        "manifest_path": manifest_path if os.path.isfile(manifest_path) else "",
        "chunk_dir": chunk_dir if os.path.isdir(chunk_dir) else "",
    }


def _style_dict(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(style or "").split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k = k.strip()
        if k:
            out[k] = v.strip()
    return out


def _set_style_value(el, key: str, value: str) -> None:
    style = _style_dict(el.get("style") or "")
    style[str(key)] = str(value)
    el.set("style", ";".join(f"{k}:{v}" for k, v in style.items()))


def _iter_page_elements(nv) -> list:
    out = []
    for el in list(nv or []):
        tag = getattr(el, "tag", "")
        if isinstance(tag, str) and tag.endswith("page"):
            out.append(el)
    return out


def _ensure_id(el, prefix: str, counter: list[int]) -> str:
    node_id = str(el.get("id") or "").strip()
    if node_id:
        return node_id
    counter[0] += 1
    node_id = f"{prefix}{counter[0]}"
    el.set("id", node_id)
    return node_id


def _find_output_groups(root) -> tuple[object | None, list]:
    out_root = root.find(".//*[@id='pnpink-output']")
    if out_root is None:
        return None, []
    run_groups = [child for child in list(out_root) if isinstance(getattr(child, "tag", None), str)]
    if not run_groups:
        return out_root, []
    return out_root, run_groups


def _find_output_page_groups(out_root) -> list:
    groups = []
    for child in list(out_root):
        if not isinstance(getattr(child, "tag", None), str):
            continue
        if str(child.get("data-pnpink-page-id") or "").strip():
            groups.append(child)
    return groups


def _find_pnpink_layer(root):
    for child in list(root):
        try:
            if str(child.get("{http://www.inkscape.org/namespaces/inkscape}label") or "") == "PnPInk":
                return child
        except Exception:
            continue
    return None


def _is_marks_layer(node) -> bool:
    try:
        label = str(node.get("{http://www.inkscape.org/namespaces/inkscape}label") or "").strip().lower()
    except Exception:
        label = ""
    try:
        node_id = str(node.get("id") or "").strip().lower()
    except Exception:
        node_id = ""
    return ("marks" in label) or node_id.startswith("marks")


def prepare_full_output_doc(doc, *, source_svg_path: str, absolutize_images: bool = True) -> dict:
    import svg as SVG

    root = doc.getroot()
    fixed_images = int(SVG.absolutize_all_linked_images(doc, source_svg_path, prefer="fileuri") or 0) if absolutize_images else 0
    nv = SVG.namedview(root)
    if nv is None:
        raise ValueError("Output SVG has no namedview")
    out_root, _run_groups = _find_output_groups(root)
    if out_root is None:
        raise ValueError("No 'pnpink-output' group found in output SVG")
    pnpink_layer = _find_pnpink_layer(root)

    for el in _iter_page_elements(nv):
        try:
            page_id = str(el.get("id") or "").strip()
        except Exception:
            page_id = ""
        if page_id.startswith("dm_page_"):
            continue
        try:
            nv.remove(el)
        except Exception:
            pass

    for child in list(root):
        if child is nv:
            continue
        tag = str(getattr(child, "tag", "") or "")
        if tag.endswith("defs"):
            continue
        if _is_marks_layer(child):
            try:
                st = _style_dict(child.get("style") or "")
                if st.get("display") == "none":
                    st.pop("display", None)
                    child.set("style", ";".join(f"{k}:{v}" for k, v in st.items()))
            except Exception:
                pass
            continue
        if pnpink_layer is not None and child is pnpink_layer:
            continue
        _set_style_value(child, "display", "none")

    if pnpink_layer is not None:
        for child in list(pnpink_layer):
            try:
                st = _style_dict(child.get("style") or "")
                if st.get("display") == "none":
                    st.pop("display", None)
                    child.set("style", ";".join(f"{k}:{v}" for k, v in st.items()))
            except Exception:
                pass

    return {
        "fixed_images": fixed_images,
        "root_width": str(root.get("width") or ""),
        "root_height": str(root.get("height") or ""),
        "root_viewbox": str(root.get("viewBox") or ""),
    }


def _output_generated_pages(pages: list[dict]) -> list[dict]:
    return [page for page in (pages or []) if str(page.get("id") or "").startswith("dm_page_")] or list(pages or [])


def _length_unit_suffix(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    m = LENGTH_UNIT_RE.match(raw)
    if not m:
        return ""
    return str(m.group(1) or "")


def _translate_in_place(node, dx: float, dy: float) -> None:
    if abs(dx) <= 1e-9 and abs(dy) <= 1e-9:
        return
    old = str(node.get("transform") or "").strip()
    if old:
        m = MATRIX_RE.fullmatch(old)
        if m:
            try:
                vals = [float(x) for x in re.split(r"[, \t]+", m.group(1).strip()) if x]
                if len(vals) == 6:
                    vals[4] += float(dx)
                    vals[5] += float(dy)
                    node.set(
                        "transform",
                        "matrix({:.12g} {:.12g} {:.12g} {:.12g} {:.12g} {:.12g})".format(*vals),
                    )
                    return
            except Exception:
                pass
        m = TRANSLATE_RE.fullmatch(old)
        if m:
            try:
                vals = [float(x) for x in re.split(r"[, \t]+", m.group(1).strip()) if x]
                if len(vals) == 1:
                    vals.append(0.0)
                if len(vals) >= 2:
                    vals[0] += float(dx)
                    vals[1] += float(dy)
                    node.set("transform", "translate({:.12g},{:.12g})".format(vals[0], vals[1]))
                    return
            except Exception:
                pass
    prefix = f"translate({dx:.6f},{dy:.6f})"
    node.set("transform", f"{prefix} {old}".strip())


def _extract_node_anchor_xy(node) -> tuple[float, float] | None:
    raw = str(node.get("transform") or "").strip()
    if not raw:
        return None
    m = MATRIX_RE.search(raw)
    if m:
        try:
            vals = [float(x) for x in re.split(r"[, \t]+", m.group(1).strip()) if x]
            if len(vals) == 6:
                return float(vals[4]), float(vals[5])
        except Exception:
            pass
    m = TRANSLATE_RE.search(raw)
    if m:
        try:
            vals = [float(x) for x in re.split(r"[, \t]+", m.group(1).strip()) if x]
            if len(vals) == 1:
                return float(vals[0]), 0.0
            if len(vals) >= 2:
                return float(vals[0]), float(vals[1])
        except Exception:
            pass
    return None


def _bbox_intersection_area(bbox: dict, page: dict) -> float:
    x1 = max(float(bbox.get("x") or 0.0), float(page["x"]))
    y1 = max(float(bbox.get("y") or 0.0), float(page["y"]))
    x2 = min(float(bbox.get("x") or 0.0) + float(bbox.get("width") or 0.0), float(page["x"]) + float(page["w"]))
    y2 = min(float(bbox.get("y") or 0.0) + float(bbox.get("height") or 0.0), float(page["y"]) + float(page["h"]))
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return (x2 - x1) * (y2 - y1)


def _best_page_for_bbox(bbox: dict, pages: list[dict]) -> int | None:
    best_page = None
    best_area = 0.0
    for idx, page in enumerate(pages, start=1):
        area = _bbox_intersection_area(bbox, page)
        if area > best_area:
            best_area = area
            best_page = idx
    if best_page is not None:
        return best_page
    cx = float(bbox.get("x") or 0.0) + (float(bbox.get("width") or 0.0) / 2.0)
    for idx, page in enumerate(pages, start=1):
        if float(page["x"]) <= cx <= float(page["x"]) + float(page["w"]):
            return idx
    return None


def _best_page_for_point(px: float, py: float, pages: list[dict]) -> int | None:
    for idx, page in enumerate(pages, start=1):
        x = float(page["x"])
        y = float(page["y"])
        w = float(page["w"])
        h = float(page["h"])
        if x <= px <= (x + w) and y <= py <= (y + h):
            return idx
    for idx, page in enumerate(pages, start=1):
        x = float(page["x"])
        w = float(page["w"])
        if x <= px <= (x + w):
            return idx
    return None


def _local_href(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("#"):
        return raw[1:]
    if raw.startswith("file:///"):
        try:
            return str(Path(raw[8:]).resolve())
        except Exception:
            return raw[8:]
    if os.path.isabs(raw) and os.path.exists(raw):
        return raw
    return None


def _collect_ref_ids_from_value(value: str) -> set[str]:
    out = set()
    raw = str(value or "")
    for ref in URL_REF_RE.findall(raw):
        if ref:
            out.add(ref)
    if raw.startswith("#") and len(raw) > 1:
        out.add(raw[1:])
    return out


def _iter_ref_values(node) -> Iterable[str]:
    for key, value in list(getattr(node, "attrib", {}).items()):
        if value:
            yield str(value)
    style = str(node.get("style") or "")
    if style:
        yield style


def _collect_used_asset_bytes(node, id_map: dict[str, object], seen_paths: set[str], seen_ids: set[str]) -> int:
    total = 0
    pending_ids: list[str] = []
    for el in node.iter():
        for value in _iter_ref_values(el):
            abs_path = _local_href(value)
            if abs_path and os.path.isfile(abs_path):
                norm = os.path.normcase(os.path.normpath(abs_path))
                if norm not in seen_paths:
                    seen_paths.add(norm)
                    try:
                        total += int(os.path.getsize(abs_path))
                    except Exception:
                        pass
            for ref_id in _collect_ref_ids_from_value(value):
                if ref_id and ref_id not in seen_ids:
                    pending_ids.append(ref_id)
    while pending_ids:
        ref_id = pending_ids.pop()
        if ref_id in seen_ids:
            continue
        seen_ids.add(ref_id)
        target = id_map.get(ref_id)
        if target is None:
            continue
        total += _collect_used_asset_bytes(target, id_map, seen_paths, seen_ids)
    return total


def _estimate_node_bytes(node, id_map: dict[str, object]) -> int:
    try:
        import svg as SVG

        xml_bytes = len(SVG.etree.tostring(node, encoding="utf-8"))
    except Exception:
        xml_bytes = len(str(node).encode("utf-8", errors="ignore"))
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    asset_bytes = _collect_used_asset_bytes(node, id_map, seen_paths, seen_ids)
    return int(xml_bytes + asset_bytes)


def _estimate_node_xml_bytes(node) -> int:
    try:
        import svg as SVG

        return int(len(SVG.etree.tostring(node, encoding="utf-8")))
    except Exception:
        return int(len(str(node).encode("utf-8", errors="ignore")))

def _assign_output_nodes_to_pages(doc, root, generated_pages: list[dict], candidate_nodes: list[tuple[str, object]], *, inkscape_exe: str | None = None) -> tuple[dict[int, list[str]], dict[int, int], tuple[str, ...]]:
    import svg as SVG

    query_ids = {node_id for node_id, _node in candidate_nodes}
    bboxes = SVG.query_all(doc, query_ids, inkscape_bin=inkscape_exe, minimize_for_ids=False) if inkscape_exe else {}
    id_map = {}
    for el in root.iter():
        el_id = str(el.get("id") or "").strip()
        if el_id:
            id_map[el_id] = el

    page_nodes: dict[int, list[str]] = {idx: [] for idx in range(1, len(generated_pages) + 1)}
    page_bytes: dict[int, int] = {idx: 0 for idx in range(1, len(generated_pages) + 1)}
    unassigned_nodes: list[str] = []
    for node_id, node in candidate_nodes:
        page_no = None
        anchor = _extract_node_anchor_xy(node)
        if anchor is not None:
            page_no = _best_page_for_point(float(anchor[0]), float(anchor[1]), generated_pages)
        bbox = bboxes.get(node_id)
        if page_no is None and bbox:
            page_no = _best_page_for_bbox(bbox, generated_pages)
        if page_no is None:
            unassigned_nodes.append(node_id)
            continue
        page_nodes[page_no].append(node_id)
        page_bytes[page_no] += _estimate_node_bytes(node, id_map)
    return page_nodes, page_bytes, tuple(unassigned_nodes)


def analyze_output_pages(svg_path: str, *, inkscape_exe: str | None = None) -> dict:
    import inkex
    with open(svg_path, "rb") as fh:
        doc = inkex.load_svg(fh.read())
    return analyze_output_doc(doc, source_svg_path=svg_path, inkscape_exe=inkscape_exe, analysis_label=svg_path)


def analyze_output_doc(doc, *, source_svg_path: str, inkscape_exe: str | None = None, analysis_label: str = "<doc>", absolutize_images: bool = True) -> dict:
    import svg as SVG

    t0 = time.perf_counter()
    root = doc.getroot()
    fixed_images = int(SVG.absolutize_all_linked_images(doc, source_svg_path, prefer="fileuri") or 0) if absolutize_images else 0
    nv = SVG.namedview(root)
    if nv is None:
        raise ValueError("No namedview found in output SVG")
    pages = SVG.list_existing_pages_px(root)
    if not pages:
        raise ValueError("No pages found in output SVG")

    out_root, run_groups = _find_output_groups(root)
    if out_root is None:
        raise ValueError("No 'pnpink-output' group found in output SVG")
    page_groups = _find_output_page_groups(out_root)

    if page_groups:
        generated_pages = _output_generated_pages(pages)
        slices: list[SvgPageSlice] = []
        t_est = time.perf_counter()
        for idx, page in enumerate(generated_pages, start=1):
            group = page_groups[idx - 1] if idx - 1 < len(page_groups) else None
            if group is None:
                est_bytes = 0
                node_ids = tuple()
            else:
                group_id = _ensure_id(group, "pnpink_page_group_", [0])
                # Page-grouped output is generated by us. For chunk planning, the
                # page group's XML size is the relevant variable; shared defs/assets
                # are document-level overhead and scanning them for every page is
                # disproportionately expensive on large decks.
                est_bytes = _estimate_node_xml_bytes(group)
                node_ids = (group_id,)
            slices.append(
                SvgPageSlice(
                    page_no=idx,
                    page_id=str(page.get("id") or f"page{idx}"),
                    x=float(page["x"]),
                    y=float(page["y"]),
                    w=float(page["w"]),
                    h=float(page["h"]),
                    est_bytes=int(est_bytes),
                    node_ids=node_ids,
                )
            )
        _l.i(
            "[svg_chunks] analyze svg='%s' fixed_images=%d generated_pages=%d page_groups=%d grouped_mode=page estimate_ms=%d total_ms=%d",
            analysis_label,
            fixed_images,
            len(slices),
            len(page_groups),
            int((time.perf_counter() - t_est) * 1000),
            int((time.perf_counter() - t0) * 1000),
        )
        for item in slices:
            _l.i(
                "[svg_chunks] page=%d page_id='%s' est_bytes=%d nodes=%d box=(%.2f,%.2f %.2fx%.2f)",
                int(item.page_no),
                item.page_id,
                int(item.est_bytes),
                len(item.node_ids),
                float(item.x),
                float(item.y),
                float(item.w),
                float(item.h),
            )
        return {
            "fixed_images": fixed_images,
            "pages": slices,
            "page_gap_px": float((generated_pages[1]["x"] - (generated_pages[0]["x"] + generated_pages[0]["w"])) if len(generated_pages) > 1 else 10.0),
            "unassigned_nodes": tuple(),
        }

    counter = [0]
    candidate_nodes = []
    containers = run_groups or [out_root]
    for container in containers:
        for child in list(container):
            if not isinstance(getattr(child, "tag", None), str):
                continue
            node_id = _ensure_id(child, "pnpink_chunk_node_", counter)
            candidate_nodes.append((node_id, child))

    generated_pages = _output_generated_pages(pages)
    candidate_count = len(candidate_nodes)
    page_nodes, page_bytes, unassigned_nodes = _assign_output_nodes_to_pages(
        doc,
        root,
        generated_pages,
        candidate_nodes,
        inkscape_exe=inkscape_exe,
    )

    raw_slices: list[SvgPageSlice] = []
    for page_no, page in enumerate(generated_pages, start=1):
        node_ids = tuple(page_nodes.get(page_no) or [])
        est_bytes = int(page_bytes.get(page_no) or 0)
        raw_slices.append(
            SvgPageSlice(
                page_no=page_no,
                page_id=str(page.get("id") or f"page{page_no + 1}"),
                x=float(page["x"]),
                y=float(page["y"]),
                w=float(page["w"]),
                h=float(page["h"]),
                est_bytes=est_bytes,
                node_ids=node_ids,
            )
        )

    trimmed_leading = 0
    slices = list(raw_slices)
    while len(slices) > 1 and not slices[0].node_ids and int(slices[0].est_bytes or 0) <= 0:
        trimmed_leading += 1
        slices.pop(0)
    if trimmed_leading:
        slices = [
            SvgPageSlice(
                page_no=idx,
                page_id=item.page_id,
                x=item.x,
                y=item.y,
                w=item.w,
                h=item.h,
                est_bytes=item.est_bytes,
                node_ids=item.node_ids,
            )
            for idx, item in enumerate(slices, start=1)
        ]
    _l.i(
        "[svg_chunks] analyze svg='%s' fixed_images=%d generated_pages=%d trimmed_leading=%d candidates=%d assigned=%d unassigned=%d",
        analysis_label,
        fixed_images,
        len(slices),
        trimmed_leading,
        candidate_count,
        sum(len(item.node_ids) for item in slices),
        len(unassigned_nodes),
    )
    for item in slices:
        _l.i(
            "[svg_chunks] page=%d page_id='%s' est_bytes=%d nodes=%d box=(%.2f,%.2f %.2fx%.2f)",
            int(item.page_no),
            item.page_id,
            int(item.est_bytes),
            len(item.node_ids),
            float(item.x),
            float(item.y),
            float(item.w),
            float(item.h),
        )
    return {
        "fixed_images": fixed_images,
        "pages": slices,
        "page_gap_px": float((generated_pages[1]["x"] - (generated_pages[0]["x"] + generated_pages[0]["w"])) if len(generated_pages) > 1 else 10.0),
        "unassigned_nodes": tuple(unassigned_nodes),
    }


def write_output_chunks_from_doc(
    doc,
    out_path: str,
    *,
    source_svg_path: str,
    inkscape_exe: str | None = None,
    target_chunk_bytes: int,
    analysis: dict | None = None,
    absolutize_images: bool = True,
) -> dict:
    import inkex
    import svg as SVG

    analysis_data = analysis or analyze_output_doc(
        doc,
        source_svg_path=source_svg_path,
        inkscape_exe=inkscape_exe,
        analysis_label=out_path,
        absolutize_images=absolutize_images,
    )
    page_slices = list(analysis_data.get("pages") or [])
    if not page_slices:
        raise ValueError("No generated pages available to export")

    groups = build_chunk_plan(page_slices, target_chunk_bytes=target_chunk_bytes)
    fixed_images = int(analysis_data.get("fixed_images") or 0)
    out_path = os.path.normpath(out_path)
    stem = Path(out_path).stem
    chunk_dir, manifest_path, suffix = _chunk_dir_for_output(out_path)
    if os.path.isdir(chunk_dir):
        shutil.rmtree(chunk_dir, ignore_errors=True)
    os.makedirs(chunk_dir, exist_ok=True)
    base_raw = SVG.etree.tostring(doc.getroot(), encoding="utf-8")
    _l.i("[svg_chunks] write_output chunk_dir='%s' target='%s'", chunk_dir, out_path)

    chunk_paths: list[str] = []
    all_page_nos = tuple(int(item.page_no) for item in page_slices)
    for group_index, group_pages in enumerate(groups, start=1):
        group_page_nos = tuple(int(item.page_no) for item in group_pages)
        chunk_doc = inkex.load_svg(base_raw)
        if len(groups) == 1 and group_page_nos == all_page_nos:
            chunk_info = {
                "root_width": str(chunk_doc.getroot().get("width") or ""),
                "root_height": str(chunk_doc.getroot().get("height") or ""),
            }
        else:
            chunk_info = normalize_output_doc(
                chunk_doc,
                source_svg_path=source_svg_path,
                keep_pages=set(group_page_nos),
                inkscape_exe=inkscape_exe,
                absolutize_images=absolutize_images,
            )
        chunk_svg_path = _chunk_svg_path(chunk_dir, stem, suffix, group_index)
        try:
            chunk_doc.write(chunk_svg_path, encoding="utf-8", xml_declaration=True)
        except TypeError:
            chunk_doc.write(chunk_svg_path)
        _l.i(
            "[svg_chunks] wrote output chunk=%d pages=%s svg='%s' est_bytes=%d root=(%s x %s)",
            group_index,
            ",".join(str(int(item.page_no)) for item in group_pages),
            chunk_svg_path,
            sum(int(item.est_bytes or 0) for item in group_pages),
            str(chunk_info.get("root_width") or ""),
            str(chunk_info.get("root_height") or ""),
        )
        chunk_paths.append(os.path.normpath(chunk_svg_path))

    with open(manifest_path, "w", encoding="utf-8") as fh:
        for path in chunk_paths:
            fh.write(path + "\n")

    return {
        "fixed_images": fixed_images,
        "page_slices": page_slices,
        "target_chunk_bytes": int(target_chunk_bytes),
        "chunk_count": len(chunk_paths),
        "chunk_dir": os.path.normpath(chunk_dir),
        "chunk_paths": tuple(chunk_paths),
        "manifest_path": os.path.normpath(manifest_path),
        "unassigned_nodes": tuple(analysis_data.get("unassigned_nodes") or ()),
    }


def build_chunk_plan(
    page_slices: list[SvgPageSlice],
    *,
    target_chunk_bytes: int,
) -> list[list[SvgPageSlice]]:
    groups: list[list[SvgPageSlice]] = []
    current: list[SvgPageSlice] = []
    current_bytes = 0
    for item in page_slices:
        item_bytes = max(1, int(item.est_bytes or 0))
        if current and (current_bytes + item_bytes) > max(1, int(target_chunk_bytes or 1)):
            groups.append(current)
            current = [item]
            current_bytes = item_bytes
            continue
        current.append(item)
        current_bytes += item_bytes
    if current:
        groups.append(current)
    if not groups and page_slices:
        groups = [[item] for item in page_slices]
    _l.i("[svg_chunks] chunk_plan target_bytes=%d chunks=%d", int(target_chunk_bytes), len(groups))
    for idx, group in enumerate(groups, start=1):
        _l.i(
            "[svg_chunks] chunk=%d pages=%s est_bytes=%d",
            idx,
            ",".join(str(int(item.page_no)) for item in group),
            sum(int(item.est_bytes or 0) for item in group),
        )
    return groups


def normalize_output_doc(doc, *, source_svg_path: str, keep_pages: set[int] | None = None, inkscape_exe: str | None = None, absolutize_images: bool = True) -> dict:
    import svg as SVG

    root = doc.getroot()
    width_unit = _length_unit_suffix(root.get("width"))
    height_unit = _length_unit_suffix(root.get("height")) or width_unit
    fixed_images = int(SVG.absolutize_all_linked_images(doc, source_svg_path, prefer="fileuri") or 0) if absolutize_images else 0
    nv = SVG.namedview(root)
    all_pages = SVG.list_existing_pages_px(root)
    if nv is None or not all_pages:
        raise ValueError("Output SVG has no namedview/pages")
    generated_pages = _output_generated_pages(all_pages)
    out_root, run_groups = _find_output_groups(root)
    if out_root is None:
        raise ValueError("No 'pnpink-output' group found in output SVG")
    pnpink_layer = _find_pnpink_layer(root)
    page_groups = _find_output_page_groups(out_root)

    keep_page_nos = set(int(p) for p in (keep_pages or set(range(1, len(generated_pages) + 1))) if int(p) > 0)
    selected_pages = [page for idx, page in enumerate(generated_pages, start=1) if idx in keep_page_nos]
    if not selected_pages:
        raise ValueError("No output pages selected")

    for child in list(root):
        if child is nv:
            continue
        tag = str(getattr(child, "tag", "") or "")
        if tag.endswith("defs"):
            continue
        if pnpink_layer is not None and child is pnpink_layer:
            continue
        _set_style_value(child, "display", "none")

    page_gap_px = float((generated_pages[1]["x"] - (generated_pages[0]["x"] + generated_pages[0]["w"])) if len(generated_pages) > 1 else 10.0)
    x_cursor = 0.0
    page_new_x: dict[int, float] = {}
    selected_page_items: list[tuple[int, dict]] = []
    for page_no, page in enumerate(generated_pages, start=1):
        if page_no not in keep_page_nos:
            continue
        page_new_x[page_no] = x_cursor
        selected_page_items.append((page_no, page))
        x_cursor += float(page["w"]) + page_gap_px

    if page_groups:
        unassigned_nodes: tuple[str, ...] = tuple()
        for page_no, page_group in enumerate(list(page_groups), start=1):
            if page_no not in keep_page_nos:
                out_root.remove(page_group)
                continue
            page = generated_pages[page_no - 1]
            dx = float(page_new_x[page_no]) - float(page["x"])
            dy = 0.0 - float(page["y"])
            _translate_in_place(page_group, dx, dy)
    else:
        counter = [0]
        candidate_nodes: list[tuple[str, object]] = []
        containers = run_groups or [out_root]
        for container in containers:
            for child in list(container):
                if not isinstance(getattr(child, "tag", None), str):
                    continue
                node_id = _ensure_id(child, "pnpink_chunk_node_", counter)
                candidate_nodes.append((node_id, child))

        page_nodes, _page_bytes, unassigned_nodes = _assign_output_nodes_to_pages(
            doc,
            root,
            generated_pages,
            candidate_nodes,
            inkscape_exe=inkscape_exe,
        )
        selected_page_by_node: dict[str, tuple[int, dict]] = {}
        for page_no, page in enumerate(generated_pages, start=1):
            if page_no not in keep_page_nos:
                continue
            for node_id in page_nodes.get(page_no) or []:
                selected_page_by_node[node_id] = (page_no, page)

        for container in containers:
            for child in list(container):
                if not isinstance(getattr(child, "tag", None), str):
                    continue
                node_id = str(child.get("id") or "").strip()
                page_info = selected_page_by_node.get(node_id)
                if page_info is None:
                    container.remove(child)
                    continue
                page_no, page = page_info
                dx = float(page_new_x[page_no]) - float(page["x"])
                dy = 0.0 - float(page["y"])
                _translate_in_place(child, dx, dy)
            if container is not out_root and len(container) == 0:
                try:
                    out_root.remove(container)
                except Exception:
                    pass

    for el in _iter_page_elements(nv):
        try:
            nv.remove(el)
        except Exception:
            pass
    for page_no, page in selected_page_items:
        old_el = page.get("el")
        if old_el is None:
            continue
        new_el = copy.deepcopy(old_el)
        new_el.set("x", f"{page_new_x[page_no]:.6f}")
        new_el.set("y", "0")
        nv.append(new_el)

    total_width = max(0.0, x_cursor - page_gap_px) if selected_page_items else float(selected_pages[0]["w"])
    total_height = max(float(page["h"]) for _page_no, page in selected_page_items) if selected_page_items else float(selected_pages[0]["h"])
    first_page_w = float(selected_pages[0]["w"])
    first_page_h = float(selected_pages[0]["h"])
    root.set("width", f"{first_page_w:.6f}{width_unit}")
    root.set("height", f"{first_page_h:.6f}{height_unit}")
    root.set("viewBox", f"0 0 {first_page_w:.6f} {first_page_h:.6f}")
    _l.i(
        "[svg_chunks] normalize pages=%s grouped_mode=%s out_children=%d root=(%s x %s)",
        ",".join(str(int(page_no)) for page_no, _page in selected_page_items),
        "page" if page_groups else "legacy",
        len([child for child in list(out_root) if isinstance(getattr(child, 'tag', None), str)]),
        str(root.get("width") or ""),
        str(root.get("height") or ""),
    )
    return {
        "fixed_images": fixed_images,
        "page_gap_px": page_gap_px,
        "page_count": len(selected_page_items),
        "pages": tuple(int(page_no) for page_no, _page in selected_page_items),
        "unassigned_nodes": tuple(unassigned_nodes),
        "extent_width": float(total_width),
        "extent_height": float(total_height),
        "root_width": str(root.get("width") or ""),
        "root_height": str(root.get("height") or ""),
        "root_viewbox": str(root.get("viewBox") or ""),
    }


def normalize_output_svg_file(svg_path: str, *, out_path: str | None = None, inkscape_exe: str | None = None) -> dict:
    import inkex

    with open(svg_path, "rb") as fh:
        doc = inkex.load_svg(fh.read())
    info = normalize_output_doc(doc, source_svg_path=svg_path, inkscape_exe=inkscape_exe)
    target = os.path.normpath(out_path or svg_path)
    try:
        doc.write(target, encoding="utf-8", xml_declaration=True)
    except TypeError:
        doc.write(target)
    info["svg_path"] = target
    return info


def write_svg_chunks(
    svg_path: str,
    pdf_path: str,
    *,
    inkscape_exe: str,
    target_chunk_bytes: int,
    artifact_dir: str | None = None,
) -> dict:
    import inkex
    import svg as SVG

    analysis = analyze_output_pages(svg_path, inkscape_exe=inkscape_exe)
    page_slices = list(analysis.get("pages") or [])
    if not page_slices:
        raise ValueError("No generated pages available to export")

    groups = build_chunk_plan(page_slices, target_chunk_bytes=target_chunk_bytes)
    fixed_images = int(analysis.get("fixed_images") or 0)

    with open(svg_path, "rb") as fh:
        base_raw = fh.read()

    stem = Path(pdf_path).stem
    chunk_dir = os.path.normpath(artifact_dir or TEMPPATHS.make_work_dir("svg_export_chunks", stem=stem))
    _l.i("[svg_chunks] write chunk_dir='%s' source_svg='%s' output_pdf='%s'", chunk_dir, svg_path, pdf_path)
    _l.i("[svg_chunks] plan total=%d target_chunk_bytes=%d", len(groups), int(target_chunk_bytes))
    chunks: list[SvgChunk] = []
    for group_index, group_pages in enumerate(groups, start=1):
        doc = inkex.load_svg(base_raw)
        chunk_info = normalize_output_doc(
            doc,
            source_svg_path=svg_path,
            keep_pages={int(item.page_no) for item in group_pages},
            inkscape_exe=inkscape_exe,
        )

        chunk_svg_path = _chunk_svg_path(chunk_dir, stem, ".svg", group_index)
        chunk_pdf_path = _chunk_pdf_path(chunk_dir, stem, group_index)
        chunk_png_prefix = _chunk_png_prefix(chunk_dir, stem, group_index)
        try:
            doc.write(chunk_svg_path, encoding="utf-8", xml_declaration=True)
        except TypeError:
            doc.write(chunk_svg_path)
        _l.i(
            "[svg_chunks] wrote chunk=%d pages=%s svg='%s' pdf='%s' est_bytes=%d root=(%s x %s) extent=(%.2f x %.2f)",
            group_index,
            ",".join(str(int(item.page_no)) for item in group_pages),
            chunk_svg_path,
            chunk_pdf_path,
            sum(int(item.est_bytes or 0) for item in group_pages),
            str(chunk_info.get("root_width") or ""),
            str(chunk_info.get("root_height") or ""),
            float(chunk_info.get("extent_width") or 0.0),
            float(chunk_info.get("extent_height") or 0.0),
        )
        chunks.append(
            SvgChunk(
                index=group_index,
                pages=tuple(int(item.page_no) for item in group_pages),
                est_bytes=sum(int(item.est_bytes or 0) for item in group_pages),
                svg_path=os.path.normpath(chunk_svg_path),
                pdf_path=os.path.normpath(chunk_pdf_path),
                png_prefix=os.path.normpath(chunk_png_prefix),
            )
        )

    launch_order = sorted(chunks, key=lambda item: (int(item.est_bytes or 0), min(item.pages or (10**9,)), item.index))
    _l.i(
        "[svg_chunks] launch_order=%s",
        " | ".join(
            f"chunk{int(item.index)} pages={','.join(str(p) for p in item.pages)} est={int(item.est_bytes)}"
            for item in launch_order
        ),
    )
    return {
        "fixed_images": fixed_images,
        "chunk_dir": os.path.normpath(chunk_dir),
        "chunks": launch_order,
        "page_slices": page_slices,
        "target_chunk_bytes": int(target_chunk_bytes),
        "unassigned_nodes": tuple(analysis.get("unassigned_nodes") or ()),
    }
