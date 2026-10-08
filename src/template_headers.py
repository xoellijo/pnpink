# -*- coding: utf-8 -*-
"""Extract a spreadsheet header row from an SVG template group."""

from __future__ import annotations

from collections import Counter

import const as CONST


INKSCAPE_LABEL = f"{{{CONST.NS_INKSCAPE}}}label"


def _local_name(node) -> str:
    tag = str(getattr(node, "tag", "") or "")
    return tag.rsplit("}", 1)[-1]


def _child_text(node, name: str) -> str:
    for child in list(node) if node is not None else []:
        if _local_name(child) == name:
            return str(child.text or "").strip()
    return ""


def _bbox_reference(datasets: list[dict]) -> str:
    for dataset in datasets or []:
        references = list((dataset.get("meta") or {}).get("templates_bbox_ids") or [])
        if references:
            return str(references[0] or "").strip()
    return ""


def _find_reference(root, reference: str):
    wanted = str(reference or "").strip()
    if not wanted:
        return None
    nodes = list(root.iter())
    for node in nodes:
        if str(node.get("id") or "").strip() == wanted:
            return node
    for node in nodes:
        if str(node.get(INKSCAPE_LABEL) or "").strip() == wanted:
            return node
    for node in nodes:
        if _child_text(node, "title") == wanted or _child_text(node, "desc") == wanted:
            return node
    return None


def _fallback_bbox(root):
    for node in root.iter():
        if _child_text(node, "desc").casefold().startswith("bbox"):
            return node
    return None


def _template_root(root, bbox):
    if bbox is None:
        return None
    if bbox.getparent() is root:
        return bbox
    current = bbox
    template = None
    while current is not None:
        parent = current.getparent()
        if _local_name(current) == "g" and current.get(CONST.INK_GROUPMODE) != "layer":
            template = current
        if parent is None or parent is root:
            break
        if _local_name(parent) == "g" and parent.get(CONST.INK_GROUPMODE) == "layer":
            break
        current = parent
    return template


def template_headers(root, datasets: list[dict]) -> dict:
    reference = _bbox_reference(datasets)
    bbox = _find_reference(root, reference) if reference else _fallback_bbox(root)
    if bbox is None:
        if reference:
            raise ValueError(f"Dataset bbox '{reference}' was not found in the SVG template")
        raise ValueError("No dataset bbox is configured and no SVG description starts with 'bbox'")

    template = _template_root(root, bbox)
    if template is None:
        raise ValueError("The bbox must be inside an SVG group")

    bbox_id = str(bbox.get("id") or "").strip()
    if not bbox_id:
        raise ValueError("The template bbox has no SVG id")

    labels = Counter(
        str(node.get(INKSCAPE_LABEL) or "").strip()
        for node in root.iter()
        if str(node.get(INKSCAPE_LABEL) or "").strip()
    )
    headers = [bbox_id]
    seen = {bbox_id}
    for node in list(template):
        if node is bbox or _local_name(node) in {"title", "desc"}:
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        label = str(node.get(INKSCAPE_LABEL) or "").strip()
        value = label if label and labels[label] == 1 else node_id
        if value not in seen:
            headers.append(value)
            seen.add(value)

    return {
        "bbox": bbox_id,
        "template": str(template.get("id") or "").strip(),
        "headers": headers,
        "text": "\t".join(headers),
    }
