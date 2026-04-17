# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Optional

import inkex

import log as LOG
import svg as SVG

_l = LOG


def is_rect_elem(e):
    try:
        return (e is not None) and ((e.tag == "rect") or str(e.tag).endswith("}rect"))
    except Exception:
        return False


def flatten_group_transform(g):
    if not isinstance(g, inkex.Group):
        return
    gT = inkex.Transform(g.get("transform") or "")
    if gT == inkex.Transform():
        return
    for ch in list(g):
        cT = inkex.Transform(ch.get("transform") or "")
        ch.set("transform", str(gT @ cT))
    if "transform" in g.attrib:
        del g.attrib["transform"]


def row_cells(row) -> list:
    if isinstance(row, dict):
        c = row.get("cells")
        if isinstance(c, list):
            return c
    return []


def build_row_map(headers: list, row: dict) -> Dict[str, str]:
    out: Dict[str, str] = {}
    cells = row_cells(row)
    for i, h in enumerate(headers or []):
        if not h:
            continue
        v = cells[i] if i < len(cells) else ""
        if v is None:
            v = ""
        out[str(h)] = str(v)
    if isinstance(row, dict):
        for k, v in row.items():
            if k == "cells":
                continue
            if not isinstance(k, str):
                continue
            if v is None:
                continue
            if isinstance(v, (str, int, float)):
                out[k] = str(v)
    return out


def iter_row_fields(headers: list, row: dict):
    cells = row_cells(row)
    headers = list(headers or [])
    by_header = {}
    for i, h in enumerate(headers):
        by_header.setdefault(h, []).append(i)
    yielded = set()
    order = []
    for i, h in enumerate(headers):
        if i in yielded:
            continue
        idxs = by_header.get(h) or [i]
        if len(idxs) > 1:
            idxs = list(reversed(idxs))
        for j in idxs:
            if j in yielded:
                continue
            yielded.add(j)
            order.append(j)
    for i in order:
        h = headers[i]
        raw = cells[i] if i < len(cells) else ""
        yield h, ("" if raw is None else str(raw))


def ensure_wrap_symbol_for_src(doc_root, src):
    SVG.ensure_xlink_ns(doc_root)
    src_id = src.get("id")
    if not src_id:
        raise inkex.AbortExtension("Source element for wrap has no id.")
    wrap_id = f"wrap_{src_id}"
    bb = src.bounding_box()
    bw, bh = float(bb.width), float(bb.height)
    bx, by = float(bb.left), float(bb.top)
    if bw <= 0 or bh <= 0:
        raise inkex.AbortExtension(f"Invalid bbox for '{src_id}'.")
    if doc_root.xpath(f".//*[@id='{wrap_id}']"):
        return wrap_id, bw, bh
    defs = SVG.ensure_defs(doc_root)
    sym = SVG.etree.SubElement(defs, inkex.addNS("symbol", "svg"))
    sym.set("id", wrap_id)
    sym.set("viewBox", f"0 0 {bw} {bh}")
    inner = SVG.etree.SubElement(sym, inkex.addNS("use", "svg"))
    inner.set(inkex.addNS("href", "xlink"), f"#{src_id}")
    if bx or by:
        inner.set("transform", f"translate({-bx:.6f},{-by:.6f})")
    return wrap_id, bw, bh


def make_use_for_wrap(wrap_id: str, w: float, h: float, use_id: Optional[str] = None) -> SVG.etree._Element:
    u = SVG.etree.Element(inkex.addNS("use", "svg"))
    u.set(inkex.addNS("href", "xlink"), f"#{wrap_id}")
    u.set("width", f"{w:.6f}")
    u.set("height", f"{h:.6f}")
    u.set("preserveAspectRatio", "xMidYMid meet")
    if use_id:
        u.set("id", use_id)
    return u


def center_use_over_placeholder(u, placeholder, *, dbg_fa_rect_ids=None):
    bb_t = placeholder.bounding_box()
    cx_t = float(bb_t.left) + float(bb_t.width) * 0.5
    cy_t = float(bb_t.top) + float(bb_t.height) * 0.5
    w = float(u.get("width") or "0")
    h = float(u.get("height") or "0")
    x = cx_t - w / 2
    y = cy_t - h / 2
    u.set("x", f"{x:.6f}")
    u.set("y", f"{y:.6f}")
    par = placeholder.getparent()
    try:
        pid = placeholder.get("id") or ""
        in_fa = bool(dbg_fa_rect_ids) and pid in dbg_fa_rect_ids
        par_id = (par.get("id") if par is not None else None)
        _l.d(f"[dbg.use_rm] placeholder id='{pid}' par='{par_id}' in_fa={in_fa}")
    except Exception:
        pass
    if par is not None:
        if is_rect_elem(placeholder):
            return
        try:
            if str(placeholder.get("data-dm-keep-paths") or "").strip() == "1":
                return
        except Exception:
            pass
        try:
            par.remove(placeholder)
        except Exception as ex:
            _l.w(f"removing placeholder '{placeholder.get('id')}' failed: {ex}")


__all__ = [
    "is_rect_elem",
    "flatten_group_transform",
    "row_cells",
    "build_row_map",
    "iter_row_fields",
    "ensure_wrap_symbol_for_src",
    "make_use_for_wrap",
    "center_use_over_placeholder",
]
