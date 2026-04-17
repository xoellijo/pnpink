# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict

import inkex

import log as LOG
import layouts as LYT
import svg as SVG

_l = LOG


def slot_index_to_rc(within: int, plan_obj, layout_obj):
    """Map slot_index within page to (r,c) in the logical grid."""
    cols = int(getattr(plan_obj, "cols", 0) or 0)
    rows = int(getattr(plan_obj, "rows", 0) or 0)
    if cols <= 0 or rows <= 0:
        return 0, 0
    sweep_rows_first = bool(getattr(layout_obj, "sweep_rows_first", True))
    if sweep_rows_first:
        r0 = within // cols
        c0 = within % cols
    else:
        c0 = within // rows
        r0 = within % rows
    if bool(getattr(layout_obj, "invert_rows", False)):
        r0 = (rows - 1) - r0
    if bool(getattr(layout_obj, "invert_cols", False)):
        c0 = (cols - 1) - c0
    return int(r0), int(c0)


def slot_rc_to_index_1based(r1: int, c1: int, plan_obj, layout_obj):
    cols = int(getattr(plan_obj, "cols", 0) or 0)
    rows = int(getattr(plan_obj, "rows", 0) or 0)
    if cols <= 0 or rows <= 0:
        return None
    r0 = int(r1) - 1
    c0 = int(c1) - 1
    if not (0 <= r0 < rows and 0 <= c0 < cols):
        return None
    if bool(getattr(layout_obj, "invert_rows", False)):
        r0 = (rows - 1) - r0
    if bool(getattr(layout_obj, "invert_cols", False)):
        c0 = (cols - 1) - c0
    sweep_rows_first = bool(getattr(layout_obj, "sweep_rows_first", True))
    if sweep_rows_first:
        within0 = (r0 * cols) + c0
    else:
        within0 = (c0 * rows) + r0
    return int(within0) + 1


def excel_col_to_num(s: str):
    txt = str(s or "").strip().upper()
    if not txt:
        return None
    n = 0
    for ch in txt:
        if not ("A" <= ch <= "Z"):
            return None
        n = n * 26 + (ord(ch) - 64)
    return n


def parse_slot_ref(tok: str):
    t = str(tok or "").strip()
    if t.isdigit():
        return ("index", int(t))
    import re
    m = re.fullmatch(r"([A-Za-z]+)([1-9]\d*)", t)
    if m:
        return ("cell", (excel_col_to_num(m.group(1)), int(m.group(2))))
    return (None, None)


def resolve_with_base(ctx, page, card, layout, gaps, doc_page_mm):
    fn = getattr(ctx, "resolve_with_base", None)
    if callable(fn):
        return fn(page, card, layout, gaps, doc_page_mm)
    return LYT.resolve(page, card, layout, gaps, doc_page_mm)


def page_attrs_from_resolved(resolved) -> Dict[str, str]:
    attrs = {}
    mg = SVG.coerce_margins_mm(resolved.page.margins_mm())
    if any(abs(v) > 1e-9 for v in (mg.top, mg.right, mg.bottom, mg.left)):
        attrs["margin"] = f"{mg.top} {mg.right} {mg.bottom} {mg.left}"
    try:
        dm_tag = str(getattr(resolved, "_dm_tag", "") or "").strip()
        if dm_tag:
            attrs["pnpink_dm_gen"] = dm_tag
    except Exception:
        pass
    return attrs


def ensure_page_for(page_index, pages, nv, current_resolved, doc_page_mm, page_gap_px, px_per_mm):
    pw_mm, ph_mm = current_resolved.page.resolved_size_mm(doc_page_mm)
    w_px, h_px = pw_mm * px_per_mm, ph_mm * px_per_mm
    attrs = page_attrs_from_resolved(current_resolved)
    SVG.ensure_page_for_or_update(nv, pages, page_index, w_px, h_px, gap_px=page_gap_px, attrs=attrs)


class CardPlanner:
    def __init__(self, *, root, nv, pages, px_per_mm, page_gap_px, doc_page_mm,
                 current_resolved, ensure_page_for_fn, plan_fn):
        self.root = root
        self.nv = nv
        self.pages = pages
        self.px_per_mm = px_per_mm
        self.page_gap_px = page_gap_px
        self.doc_page_mm = doc_page_mm
        self.current = current_resolved
        self.ensure_page_for = ensure_page_for_fn
        self._compute_plan_for = plan_fn
        self.page_index = 0
        self.slot_index = 0
        self.plan, self.local_slots = self._compute_plan_for(
            self.current,
            self.pages[0]["w"],
            self.pages[0]["h"],
        )
        if self.plan.per_page <= 0:
            raise inkex.AbortExtension("No caben cartas en la página con el preset/layout actual.")
        _l.d("planner.init", {"slots_per_page": self.plan.per_page})

    def _ensure_fallback_plan_for_split(self):
        if getattr(self.plan, "per_page", 0) > 0:
            return
        try:
            mg = SVG.coerce_margins_mm(self.current.page.margins_mm())
            ppm = float(self.px_per_mm or 1.0)
            page_w_px = float(self.pages[self.page_index]["w"])
            page_h_px = float(self.pages[self.page_index]["h"])
            cx = float(mg.left) * ppm
            cy = float(mg.top) * ppm
            cw = page_w_px - (float(mg.left) + float(mg.right)) * ppm
            ch = page_h_px - (float(mg.top) + float(mg.bottom)) * ppm
            if cw <= 0 or ch <= 0:
                _l.w(f"[split_boards] fallback slot invalid content size cw={cw:.2f} ch={ch:.2f}")
                return
        except Exception as ex:
            _l.w(f"[split_boards] fallback slot prep failed: {ex}")
            return
        try:
            self.plan.slots = [(0.0, 0.0, float(cw), float(ch))]
            self.plan.cols = 1
            self.plan.rows = 1
            self.plan.per_page = 1
            self.plan.content_x = float(cx)
            self.plan.content_y = float(cy)
            self.plan.left = 0.0
            self.plan.top = 0.0
            self.local_slots = [(0.0, 0.0, float(cw), float(ch))]
            _l.i("[split_boards] fallback slot enabled (plan.per_page=0)")
        except Exception:
            return

    def slots_per_page(self) -> int:
        return int(self.plan.per_page)

    def page_count(self) -> int:
        return len(self.pages)

    def page_size_px(self, idx: int = None) -> tuple[float, float]:
        i = self.page_index if idx is None else idx
        return self.pages[i]["w"], self.pages[i]["h"]

    def sync_page_attrs(self):
        self.ensure_page_for(
            self.page_index, self.pages, self.nv, self.current,
            self.doc_page_mm, self.page_gap_px, self.px_per_mm,
        )

    def jump_page(self):
        self.page_index += 1
        self.slot_index = 0
        self.ensure_page_for(
            self.page_index, self.pages, self.nv, self.current,
            self.doc_page_mm, self.page_gap_px, self.px_per_mm,
        )
        pw, ph = self.page_size_px()
        self.plan, self.local_slots = self._compute_plan_for(self.current, pw, ph)
        if self.plan.per_page <= 0:
            self._ensure_fallback_plan_for_split()
        _l.d("planner.jump_page", {"page": self.page_index + 1, "slots_per_page": self.plan.per_page})

    def apply_preset(self, new_resolved):
        def _sig(r):
            pg = r.page
            mg = SVG.coerce_margins_mm(pg.margins_mm())
            lay = r.layout
            card = r.card
            return (
                pg.name, pg.width_mm, pg.height_mm, pg.landscape,
                round(mg.top, 3), round(mg.right, 3), round(mg.bottom, 3), round(mg.left, 3),
                getattr(lay, "cols", None), getattr(lay, "rows", None),
                getattr(lay, "sweep_rows_first", None),
                tuple(getattr(lay, "gaps", None) or []),
                tuple(getattr(lay, "offset", None) or []),
                getattr(lay, "smart_shape", None),
                getattr(lay, "smart_hex_orient", None),
                r.gaps.h, r.gaps.v,
                card.name, card.width_mm, card.height_mm, card.landscape,
            )
        old_sig = _sig(self.current)
        new_sig = _sig(new_resolved)
        self.current = new_resolved
        self.sync_page_attrs()
        if self.slot_index != 0 and new_sig != old_sig:
            self.page_index += 1
            self.slot_index = 0
            self.ensure_page_for(
                self.page_index, self.pages, self.nv, self.current,
                self.doc_page_mm, self.page_gap_px, self.px_per_mm,
            )
        pw, ph = self.page_size_px()
        self.plan, self.local_slots = self._compute_plan_for(self.current, pw, ph)
        if self.plan.per_page <= 0:
            self._ensure_fallback_plan_for_split()
        if self.plan.per_page <= 0:
            raise inkex.AbortExtension("No caben cartas con el nuevo preset/layout.")
        _l.d("planner.apply_preset", {"page": self.page_index + 1, "slots_per_page": self.plan.per_page})

    def begin_slot(self):
        if self.slot_index >= len(self.local_slots):
            return None, None
        slot = self.local_slots[self.slot_index]
        local_x = float(slot[0])
        local_y = float(slot[1])
        if len(slot) >= 4:
            self._slot_wh = (float(slot[2]), float(slot[3]))
        try:
            p = self.pages[self.page_index]
            px = float(p.get("x", 0.0))
            py = float(p.get("y", 0.0))
        except Exception:
            px = py = 0.0
        cx = float(getattr(self.plan, "content_x", 0.0))
        cy = float(getattr(self.plan, "content_y", 0.0))
        left = float(getattr(self.plan, "left", 0.0))
        top = float(getattr(self.plan, "top", 0.0))
        slot_x_abs = px + cx + left + local_x
        slot_y_abs = py + cy + top + local_y
        return slot_x_abs, slot_y_abs

    def commit_slot(self):
        self.slot_index += 1


__all__ = [
    "slot_index_to_rc",
    "slot_rc_to_index_1based",
    "excel_col_to_num",
    "parse_slot_ref",
    "resolve_with_base",
    "page_attrs_from_resolved",
    "ensure_page_for",
    "CardPlanner",
]
