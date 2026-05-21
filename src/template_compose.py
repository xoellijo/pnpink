# -*- coding: utf-8 -*-
"""Experimental composed-template renderer.

This module deliberately supports only a narrow, measurable subset:
- one template root at a time;
- plain text fields handled by render.py fast path;
- static top-level runs without internal id/url references.

Unsupported roots raise UnsupportedComposedTemplate; render.py decides whether
to fall back to the legacy clone path for that individual template.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable

import inkex
from lxml import etree

import svg as SVG


@dataclass
class PlanItem:
    kind: str
    ref: str | None = None
    node: object | None = None


@dataclass
class TemplatePlan:
    items: list[PlanItem]
    dynamic_ids: set[str]
    static_blocks: int
    dynamic_roots: int
    static_source_mode: str = "defs"
    instance_static_ready: bool = False


class UnsupportedComposedTemplate(RuntimeError):
    pass


def _node_id(node) -> str:
    try:
        return str(node.get("id") or "")
    except Exception:
        return ""


def _rewrite_static_ids(nodes: list, prefix: str) -> None:
    """Rename ids inside a static block copy and rewrite references to them.

    Static blocks are stored once in <defs>, so IDs may exist there. They just
    must not collide with the original template IDs still present in the source
    document. Only references to IDs inside this copied block are rewritten;
    references to shared defs such as filters/gradients remain untouched.
    """
    id_map = {}
    for node in nodes:
        for el in node.iter():
            if not hasattr(el, "tag") or not isinstance(el.tag, str):
                continue
            old = _node_id(el)
            if old:
                id_map.setdefault(old, f"{prefix}_{len(id_map) + 1}")
    if not id_map:
        return

    for node in nodes:
        for el in node.iter():
            if not hasattr(el, "tag") or not isinstance(el.tag, str):
                continue
            old = _node_id(el)
            if old in id_map:
                el.set("id", id_map[old])
                try:
                    if "data-origid" in el.attrib:
                        del el.attrib["data-origid"]
                except Exception:
                    pass
            for attr, value in list(getattr(el, "attrib", {}).items()):
                s = str(value or "")
                if not s:
                    continue
                new_s = s
                for old_id, new_id in id_map.items():
                    new_s = new_s.replace(f"url(#{old_id})", f"url(#{new_id})")
                    if new_s == f"#{old_id}":
                        new_s = f"#{new_id}"
                if new_s != s:
                    el.set(attr, new_s)


def _contains_any_id(node, ids: set[str]) -> bool:
    if not ids:
        return False
    for el in node.iter():
        cur = _node_id(el)
        if cur and (cur in ids or SVG.strip_pnp_suffix(cur) in ids or el.get("data-origid") in ids):
            return True
    return False


def _make_static_block_group(block_nodes: list, block_id: str) -> inkex.Group:
    g = inkex.Group()
    g.set("id", block_id)
    copied = []
    for src in block_nodes:
        cp = deepcopy(src)
        copied.append(cp)
    _rewrite_static_ids(copied, block_id)
    for cp in copied:
        g.append(cp)
    return g


def _append_static_block(root, block_nodes: list, block_id: str) -> None:
    defs = SVG.ensure_defs(root)
    g = _make_static_block_group(block_nodes, block_id)
    defs.append(g)


def build_plan(
    *,
    root,
    proto_root,
    dynamic_ids: Iterable[str],
    block_id_prefix: str,
    has_overlays: bool,
    has_back_templates: bool,
    has_page_templates: bool,
    has_clone_fields: bool,
    has_anchor_visibility: bool,
    static_source_mode: str = "defs",
) -> TemplatePlan:
    if proto_root is None:
        raise UnsupportedComposedTemplate("composed template requires proto_root")
    if has_clone_fields:
        raise UnsupportedComposedTemplate("composed template does not support clone_ fields yet")

    dyn = {str(x or "").strip() for x in dynamic_ids if str(x or "").strip()}
    if not dyn:
        raise UnsupportedComposedTemplate("composed template requires at least one dynamic field")

    children = [ch for ch in list(proto_root) if hasattr(ch, "tag") and isinstance(ch.tag, str)]
    if not children:
        raise UnsupportedComposedTemplate("composed template requires direct child nodes")

    mode = str(static_source_mode or "defs").strip().lower()
    if mode not in {"defs", "first_instance"}:
        mode = "defs"

    items: list[PlanItem] = []
    static_run: list = []
    static_count = 0
    dynamic_count = 0

    def flush_static() -> None:
        nonlocal static_run, static_count
        if not static_run:
            return
        static_count += 1
        block_id = f"{block_id_prefix}_static_{static_count}"
        if mode == "defs":
            _append_static_block(root, static_run, block_id)
        items.append(PlanItem("static", ref=block_id, node=list(static_run)))
        static_run = []

    for child in children:
        if _contains_any_id(child, dyn):
            flush_static()
            items.append(PlanItem("dynamic", node=child))
            dynamic_count += 1
        else:
            static_run.append(child)
    flush_static()

    if dynamic_count <= 0 or static_count <= 0:
        raise UnsupportedComposedTemplate("composed template needs both static and dynamic direct runs")

    return TemplatePlan(
        items=items,
        dynamic_ids=dyn,
        static_blocks=static_count,
        dynamic_roots=dynamic_count,
        static_source_mode=mode,
    )


def instantiate_plan(plan: TemplatePlan, suffix: str, *, root_doc) -> tuple[inkex.Group, dict]:
    group = inkex.Group()
    target_index = {}
    materialize_static = plan.static_source_mode == "first_instance" and not plan.instance_static_ready
    for item in plan.items:
        if item.kind == "static":
            if materialize_static:
                group.append(_make_static_block_group(list(item.node or []), str(item.ref or "")))
            else:
                use = etree.Element(inkex.addNS("use", "svg"))
                SVG.set_href(use, f"#{item.ref}", touch_plain=True)
                group.append(use)
            continue
        if item.kind == "dynamic":
            cp = deepcopy(item.node)
            idx = SVG.uniquify_ids_and_build_target_index(cp, suffix, getattr(root_doc, "get_unique_id", None))
            group.append(cp)
            target_index.update(idx)
            continue
        raise RuntimeError(f"unknown composed plan item kind: {item.kind}")
    if materialize_static:
        plan.instance_static_ready = True
    return group, target_index
