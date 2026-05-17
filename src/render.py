# -*- coding: utf-8 -*-
import log as LOG
_l = LOG
_DBG_FA_RECT_IDS = None
import re
import math
import time


# ---------------- spritesheet alias token parsing ----------------

from copy import deepcopy
import inkex
import svg as SVG
import prefs

# Iconify (icon://set/name) preload integration
try:
    import iconify as ICON
except Exception:
    ICON = None
TEXT_LIKE = set(getattr(SVG, "TEXT_LIKE", ()))
import layouts as LYT
import dsl as DSL
import fit_anchor as FA
import geometry_registry as GREG
import paths as PATHS
import transform_fx as TFX
import render_apply as RAP
import gui as PROGRESS
import render_helpers as RHP
import render_planner as RPL
import render_tokens as RTK
import template_compose as TCOMP
from typing import Dict, Optional, Tuple

_slot_index_to_rc = RPL.slot_index_to_rc
_slot_rc_to_index_1based = RPL.slot_rc_to_index_1based
_excel_col_to_num = RPL.excel_col_to_num
_parse_slot_ref = RPL.parse_slot_ref
_resolve_with_base = RPL.resolve_with_base
_page_attrs_from_resolved = RPL.page_attrs_from_resolved
ensure_page_for = RPL.ensure_page_for
CardPlanner = RPL.CardPlanner

_expand_index_expr = RTK.expand_index_expr
_parse_sprite_alias_token = RTK.parse_sprite_alias_token
_parse_object_token = RTK.parse_object_token
_split_paths_suffix = RTK.split_paths_suffix
_split_transform_suffixes = RTK.split_transform_suffixes
_fit_suffix_to_ops = RTK.fit_suffix_to_ops
_merge_fit_ops = RTK.merge_fit_ops
_normalize_ops_chain = RTK.normalize_ops_chain
_parse_index_selector_1based = RTK.parse_index_selector_1based
_select_1based_with_warning = RTK.select_1based_with_warning
_parse_source_like_token = RTK.parse_source_like_token
_parse_source_token_with_selector = RTK.parse_source_token_with_selector
_virtual_warn_tag = RTK.virtual_warn_tag
_resolve_virtual_source_urls = RTK.resolve_virtual_source_urls
_is_rect_elem = RHP.is_rect_elem
_flatten_group_transform = RHP.flatten_group_transform
_row_cells = RHP.row_cells
_build_row_map = RHP.build_row_map
_iter_row_fields = RHP.iter_row_fields
_ensure_wrap_symbol_for_src = RHP.ensure_wrap_symbol_for_src
_make_use_for_wrap = RHP.make_use_for_wrap
_parse_array_token = RAP._parse_array_token
_resolve_array_item = RAP._resolve_array_item
_build_array_group = RAP._build_array_group
_split_multivalue = RAP._split_multivalue
expand_value = RAP.expand_value
_parse_header_default_spec = RAP._parse_header_default_spec
_is_id_wildcard_token = RAP._is_id_wildcard_token
_expand_id_wildcard_in_scope = RAP._expand_id_wildcard_in_scope
_resolve_header_target_ids = RAP._resolve_header_target_ids
_split_leading_bracket_group = RAP._split_leading_bracket_group
_expand_wildcard_object_token = RAP._expand_wildcard_object_token
_expand_wildcard_ids_in_value = RAP._expand_wildcard_ids_in_value
_build_single_target_header_key = RAP._build_single_target_header_key
parse_header_key_full = RAP.parse_header_key_full
parse_header_key = RAP.parse_header_key
# Phase-1: per-instance set of rect ids to keep visible (from header '+' modifier)
_P1_KEEP_SET = None
_gaps_has_offsets = LYT.gaps_has_offsets


def _center_use_over_placeholder(u, placeholder):
    return RHP.center_use_over_placeholder(u, placeholder, dbg_fa_rect_ids=_DBG_FA_RECT_IDS)


def apply_field_in_clone(*args, **kwargs):
    RAP._P1_KEEP_SET = _P1_KEEP_SET
    return RAP.apply_field_in_clone(*args, **kwargs)

def render_phase(ctx):
    root = ctx.root
    SM = ctx.SM
    ss_registry = getattr(ctx, 'spritesheets', None)
    doc_path = getattr(ctx, 'doc_path', None)
    ds_idx = ctx.ds_idx
    headers = ctx.headers
    rows_data = ctx.rows_data
    dataset_count = int(getattr(ctx, 'dataset_count', 0) or 0)
    use_seq = ctx.use_seq
    next_n = ctx.next_n
    placed_total = ctx.placed_total
    start_page_index = ctx.start_page_index
    planner = ctx.planner
    pages = planner.pages  # list[dict] with {id,x,y,w,h,el}
    proto_root = ctx.proto_root
    out_layer = ctx.out_layer
    dm_tag = str(getattr(planner.current, "_dm_tag", "") or "")
    _marks_pending_by_page = ctx.marks_pending_by_page
    _flush_marks_for_page = ctx.flush_marks_for_page
    page = getattr(ctx, 'page', None)
    card = getattr(ctx, 'card', None)
    layout = getattr(ctx, 'layout', None)
    gaps = getattr(ctx, 'gaps', None)
    doc_page_mm = getattr(ctx, 'doc_page_mm', None)
    if doc_page_mm is None:
        doc_page_mm = getattr(planner, 'doc_page_mm', None)
    declared_bbox_id = getattr(ctx, 'declared_bbox_id', None)
    overlay_templates = getattr(ctx, 'overlay_templates', None)
    back_templates = getattr(ctx, 'back_templates', None)
    page_templates = getattr(ctx, 'page_templates', None)
    page_back_templates = getattr(ctx, 'page_back_templates', None)
    declared_bbox_node = getattr(ctx, 'declared_bbox_node', None)

    def _mark_generated(node):
        if node is not None and dm_tag:
            try:
                node.set("pnpink_dm_gen", dm_tag)
            except Exception:
                pass
        return node

    page_group_cache_by_id = {}
    try:
        for _child in list(out_layer):
            _page_id = str(_child.get('data-pnpink-page-id') or '').strip()
            if _page_id:
                page_group_cache_by_id.setdefault(_page_id, _child)
    except Exception:
        page_group_cache_by_id = {}

    def _page_group_for(page_index: int):
        page_index = int(page_index)
        pinfo = pages[int(page_index)]
        page_id = str(pinfo.get('id') or f"page_{int(page_index)+1}")
        cached = page_group_cache_by_id.get(page_id)
        if cached is not None:
            return cached
        group_id = f"_MDgp{int(page_index) + 1}"
        try:
            group_id = root.get_unique_id(group_id)
        except Exception:
            pass
        group = inkex.Group()
        group.set('id', group_id)
        group.set(inkex.addNS('label', 'inkscape'), group_id)
        group.set('data-pnpink-page-id', page_id)
        group.set('data-pnpink-page-index', str(int(page_index) + 1))
        _mark_generated(group)
        out_layer.append(group)
        page_group_cache_by_id[page_id] = group
        return group

    def _append_output(node, *, page_index: int | None = None):
        _mark_generated(node)
        parent = out_layer if page_index is None else _page_group_for(int(page_index))
        parent.append(node)
        return node

    def _compile_field_specs(headers_list):
        headers_local = list(headers_list or [])
        by_header = {}
        for i, h in enumerate(headers_local):
            by_header.setdefault(h, []).append(i)
        yielded = set()
        order = []
        for i, h in enumerate(headers_local):
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
        specs = []
        for i in order:
            key = headers_local[i] if i < len(headers_local) else ""
            if not key:
                continue
            key_s = str(key)
            hk = parse_header_key_full(key_s)
            target_ids = list((hk or {}).get("target_ids") or [])
            fast_text_target = ""
            fast_text_plain = False
            if (
                not key_s.startswith("clone_")
                and not (key_s.startswith("__dm_") or key_s.startswith("_"))
                and str((hk or {}).get("prop") or "text") == "text"
                and not bool((hk or {}).get("header_plus") or False)
                and not str((hk or {}).get("default_id") or "")
                and not str((hk or {}).get("default_ops") or "")
                and not str((hk or {}).get("global_ops") or "")
                and not str((hk or {}).get("default_expr") or "")
                and not str((hk or {}).get("default_raw") or "")
                and len(target_ids) == 1
                and not _is_id_wildcard_token(target_ids[0])
            ):
                fast_text_target = target_ids[0]
                try:
                    proto_tgt = SVG.find_target_exact_in(proto_root, fast_text_target)
                    if proto_tgt is not None and (SVG.is_text_like(proto_tgt) or (proto_tgt.tag in TEXT_LIKE)):
                        parent_has_style = bool(
                            (proto_tgt.get("style") or "").strip()
                            or (proto_tgt.get("class") or "").strip()
                        )
                        has_blocking_child_style = False
                        for child in proto_tgt.iter():
                            if child is proto_tgt:
                                continue
                            tag = str(getattr(child, "tag", "") or "")
                            if not tag.endswith("tspan"):
                                continue
                            if (
                                (child.get("style") or "").strip()
                                or (child.get("class") or "").strip()
                                or (child.get("stroke") or "").strip()
                                or (child.get("stroke-width") or "").strip()
                                or (child.get("fill") or "").strip()
                            ):
                                has_blocking_child_style = not parent_has_style
                                break
                        fast_text_plain = not has_blocking_child_style
                except Exception:
                    fast_text_plain = False
            specs.append({
                "index": int(i),
                "key": key_s,
                "hk": hk,
                "is_clone": key_s.startswith("clone_"),
                "is_internal": key_s.startswith("__dm_") or key_s.startswith("_"),
                "fast_text_target": fast_text_target,
                "fast_text_plain": fast_text_plain,
            })
        return specs

    def _set_card_group_identity(
        card_group,
        proto_id: str,
        page_index: int,
        row_index: int,
        face_tag: str = "front",
        face_ordinal: int = 1,
        item_no: int | None = None,
        col_index: int | None = None,
        row_slot_index: int | None = None,
    ):
        page1 = int(page_index) + 1
        dataset1 = int(ds_idx)
        row1 = int(row_index)
        face = str(face_tag or "front").strip().lower()
        ord1 = max(1, int(face_ordinal or 1))
        try:
            item1 = max(1, int(item_no or row1))
        except Exception:
            item1 = max(1, row1)
        try:
            col1 = max(1, int(col_index or 1))
        except Exception:
            col1 = 1
        try:
            slot_row1 = max(1, int(row_slot_index or 1))
        except Exception:
            slot_row1 = 1
        gid = f"_MDgc{item1}_{col1}_{slot_row1}"
        if face == "back":
            gid += "b" if ord1 == 1 else f"b{ord1}"
        card_group.set('data-pnpink-page-index', str(page1))
        card_group.set('data-pnpink-dataset-index', str(dataset1))
        card_group.set('data-pnpink-row-index', str(row1))
        card_group.set('data-pnpink-face', face)
        card_group.set('data-pnpink-template-id', str(proto_id or "card"))
        return gid

    def _flatten_card_group(card_group, main_group):
        if card_group is None or main_group is None:
            return
        try:
            parent = main_group.getparent()
            if parent is not card_group:
                return
        except Exception:
            return
        try:
            for attr_name, attr_value in list(getattr(main_group, 'attrib', {}).items()):
                if attr_name in ('id', inkex.addNS('label', 'inkscape')):
                    continue
                if attr_name not in card_group.attrib and attr_value not in (None, ''):
                    card_group.set(attr_name, attr_value)
        except Exception:
            pass
        for child in list(main_group):
            try:
                main_group.remove(child)
            except Exception:
                pass
            card_group.append(child)
        try:
            card_group.remove(main_group)
        except Exception:
            pass

    def _apply_page_cursor_from_page(planner_obj, ps_obj):
        """Apply Page{at=} / Page{a=} / Page{@...} to the global page cursor.
        Semantics (0-based internal):
          - at=+3 / @+3 : relative move (current + 3)
          - at=-1       : rewind
          - at=5 / @5   : absolute page number (1-based) => index 4
        """
        expr = (getattr(ps_obj, 'at', None) or '').strip()
        if not expr:
            return
        if expr.startswith('@'):
            expr = expr[1:].strip()
        try:
            if expr.startswith(('+', '-')):
                delta = int(float(expr))
                new_idx = int(planner_obj.page_index) + delta
            else:
                new_idx = int(float(expr)) - 1
        except Exception as ex:
            raise inkex.AbortExtension(f"Cursor de pÃ¡ginas invÃ¡lido at='{expr}': {ex}")
        if new_idx < 0:
            new_idx = 0
        planner_obj.page_index = int(new_idx)
        planner_obj.slot_index = 0
        planner_obj.ensure_page_for(planner_obj.page_index, planner_obj.pages, planner_obj.nv, planner_obj.current,
                                 planner_obj.doc_page_mm, planner_obj.page_gap_px, planner_obj.px_per_mm)
        pw, ph = planner_obj.page_size_px()
        planner_obj.plan, planner_obj.local_slots = planner_obj._compute_plan_for(planner_obj.current, pw, ph)
        planner_obj.sync_page_attrs()
    def _get_or_advance_slot(planner_obj):
        sx, sy = planner_obj.begin_slot()
        if sx is None:
            _flush_marks_for_page(planner_obj.page_index)
            planner_obj.jump_page()
            sx, sy = planner_obj.begin_slot()
        if sx is None:
            raise inkex.AbortExtension(
                f"No available slots left to place more cards. "
                f"per_page={getattr(planner_obj.plan,'per_page',-1)} "
                f"page={planner_obj.page_index+1} slot_index={planner_obj.slot_index}"
            )
        return sx, sy
    def _slot_geom_for_within(planner_obj, within0):
        try:
            slot = planner_obj.local_slots[int(within0)]
        except Exception:
            return None
        local_x = float(slot[0]); local_y = float(slot[1])
        slot_w = slot_h = None
        if len(slot) >= 4:
            slot_w = float(slot[2]); slot_h = float(slot[3])
        try:
            p = planner_obj.pages[planner_obj.page_index]
            px = float(p.get("x", 0.0)); py = float(p.get("y", 0.0))
        except Exception:
            px = py = 0.0
        cx = float(getattr(planner_obj.plan, "content_x", 0.0))
        cy = float(getattr(planner_obj.plan, "content_y", 0.0))
        left = float(getattr(planner_obj.plan, "left", 0.0))
        top = float(getattr(planner_obj.plan, "top", 0.0))
        return (px + cx + left + local_x, py + cy + top + local_y, slot_w, slot_h)
    def _jump_page_with_marks(planner_obj):
        """Jump to next page flushing pending Marks for the current page."""
        _flush_marks_for_page(planner_obj.page_index)
        planner_obj.jump_page()
    def _iter_instances(rows):
        """Expand dataset rows into per-copy instances.

        Iterators are auto-detected from row cells that start with '*' (or '**', ...).
        Copies policy:
          - no explicit copies in cell0:
              * no iterators -> 1 copy
              * with iterators -> N_iter copies (one per expanded iterator instance)
          - explicit copies in cell0:
              * copies > N_iter -> wrap
              * copies < N_iter -> truncate

        Hole-based slot skipping (e.g. "[2 - 1]") is applied after placement.
        """

        from pathlib import Path
        import glob as _glob

        def _slots_per_page():
            try:
                return int(planner.slots_per_page() or 0)
            except Exception:
                try:
                    return int(len(getattr(planner, 'local_slots', []) or []))
                except Exception:
                    return 0

        def _resolve_slot_ref_1based(tok: str):
            kind, val = _parse_slot_ref(tok)
            n = _slots_per_page()
            if kind == "index":
                return int(val) if 1 <= int(val) <= n else None
            if kind == "cell":
                c1, r1 = val
                return _slot_rc_to_index_1based(r1, c1, planner.plan, planner.current.layout)
            return None

        def _expand_declarative_slot_selector(sel_raw: str):
            body = str(sel_raw or "").strip()
            if body.startswith('[') and body.endswith(']'):
                body = body[1:-1].strip()
            toks = [t for t in re.split(r'[\s,]+', body) if t]
            n = _slots_per_page()
            out = []
            for t in toks:
                if t == '*':
                    out.extend(list(range(1, n + 1)))
                    continue
                m = re.fullmatch(r'(\d+)\s*\.\.\s*\?', t)
                if m:
                    a = int(m.group(1))
                    if a <= n:
                        out.extend(list(range(max(1, a), n + 1)))
                    else:
                        _l.w(f"[slots] selector out of range: {t} (size={n})")
                    continue
                m = re.fullmatch(r'([A-Za-z]+\d+)\s*\.\.\s*([A-Za-z]+\d+)', t)
                if m:
                    a = _resolve_slot_ref_1based(m.group(1))
                    b = _resolve_slot_ref_1based(m.group(2))
                    if a is None or b is None:
                        _l.w(f"[slots] invalid sequential range '{t}'")
                        continue
                    step = 1 if b >= a else -1
                    out.extend(list(range(a, b + step, step)))
                    continue
                m = re.fullmatch(r'([A-Za-z]+\d+)\s*:\s*([A-Za-z]+\d+)', t)
                if m:
                    ka, va = _parse_slot_ref(m.group(1))
                    kb, vb = _parse_slot_ref(m.group(2))
                    if ka != 'cell' or kb != 'cell':
                        _l.w(f"[slots] invalid matrix range '{t}'")
                        continue
                    c1a, r1a = va
                    c1b, r1b = vb
                    cells = []
                    for rr in range(min(r1a, r1b), max(r1a, r1b) + 1):
                        for cc in range(min(c1a, c1b), max(c1a, c1b) + 1):
                            s1 = _slot_rc_to_index_1based(rr, cc, planner.plan, planner.current.layout)
                            if s1 is None:
                                _l.w(f"[slots] selector cell out of range: {cc},{rr}")
                                continue
                            cells.append(int(s1))
                    out.extend(sorted(cells))
                    continue
                m = re.fullmatch(r'(\d+)\s*\.\.\s*(\d+)', t)
                if m:
                    a = int(m.group(1)); b = int(m.group(2))
                    step = 1 if b >= a else -1
                    for i1 in range(a, b + step, step):
                        if 1 <= i1 <= n:
                            out.append(int(i1))
                        else:
                            _l.w(f"[slots] selector index out of range: {i1} (size={n})")
                    continue
                s1 = _resolve_slot_ref_1based(t)
                if s1 is None:
                    _l.w(f"[slots] invalid selector token '{t}'")
                    continue
                out.append(int(s1))
            return out

        def _expand_procedural_slot_selector(sel_raw: str):
            toks = [t for t in re.split(r'[\s,]+', str(sel_raw or '').strip()) if t]
            n = _slots_per_page()
            out = []
            cursor = int(getattr(planner, 'slot_index', 0) or 0) + 1
            for t in toks:
                m_gap = re.fullmatch(r'(\d+)-', t)
                if m_gap:
                    cursor += int(m_gap.group(1))
                    continue
                if re.fullmatch(r'[A-Za-z]+[1-9]\d*', t):
                    s1 = _resolve_slot_ref_1based(t)
                    if s1 is None:
                        _l.w(f"[slots] invalid anchor '{t}'")
                        continue
                    cursor = int(s1)
                    continue
                if re.fullmatch(r'\d+', t):
                    k = int(t)
                    if k <= 0:
                        continue
                    for _ in range(k):
                        if 1 <= cursor <= n:
                            out.append(int(cursor))
                        else:
                            _l.w(f"[slots] procedural cursor out of range: {cursor} (size={n})")
                        cursor += 1
                    continue
                _l.w(f"[slots] invalid procedural token '{t}'")
            return out, max(0, cursor - 1)

        def _count_leading_stars(s: str) -> int:
            n = 0
            for ch in s:
                if ch == '*':
                    n += 1
                else:
                    break
            return n

        def _parse_range_or_list(br: str):
            """Parse a bracket list like '[1..4]' or '[1 2 3]' into list[str]."""
            body = (br or '').strip()
            if body.startswith('[') and body.endswith(']'):
                body = body[1:-1].strip()
            if not body:
                return ['']
            def _alpha_to_num(s: str):
                if not s:
                    return None
                n = 0
                for ch in s.upper():
                    if ch < 'A' or ch > 'Z':
                        return None
                    n = n * 26 + (ord(ch) - 64)
                return n
            def _num_to_alpha(n: int) -> str:
                if n <= 0:
                    return ""
                out = []
                while n > 0:
                    n, rem = divmod(n - 1, 26)
                    out.append(chr(rem + 65))
                return "".join(reversed(out))
            # split by top-level whitespace/comma, preserving (...) groups as one token
            toks_ws = _split_multivalue(body)
            toks = []
            for tw in (toks_ws or []):
                for tcom in [x for x in re.split(r"\s*,\s*", (tw or "").strip()) if x]:
                    toks.append(tcom)
            out = []
            for t in toks:
                t = (t or "").strip()
                if not t:
                    continue
                # Group repetition in iterator lists: K*(...)
                m_rep_grp = re.match(r"^(\d+)\*\((.*)\)$", t)
                if m_rep_grp:
                    k = int(m_rep_grp.group(1))
                    inner = (m_rep_grp.group(2) or "").strip()
                    inner_toks = _split_multivalue(inner) if inner else []
                    inner_item = " ".join([x for x in inner_toks if (x or "").strip()]).strip()
                    if inner_item:
                        out.extend([inner_item] * max(0, k))
                    continue
                # Grouped multivalue item: (id2 id3) -> one iterator item "id2 id3"
                if t.startswith("(") and t.endswith(")"):
                    inner = t[1:-1].strip()
                    inner_toks = _split_multivalue(inner) if inner else []
                    inner_item = " ".join([x for x in inner_toks if (x or "").strip()]).strip()
                    if inner_item:
                        out.append(inner_item)
                    continue
                # Scalar repetition: K*X
                m_rep = re.match(r"^(\d+)\*(.+)$", t)
                if m_rep:
                    k = int(m_rep.group(1))
                    rhs = (m_rep.group(2) or "").strip()
                    if rhs:
                        out.extend([rhs] * max(0, k))
                    continue
                m = re.match(r"^([A-Za-z]+|\d+)\s*\.\.\s*([A-Za-z]+|\d+)$", t)
                if m:
                    a_raw = m.group(1)
                    b_raw = m.group(2)
                    if a_raw.isdigit() and b_raw.isdigit():
                        a = int(a_raw); b = int(b_raw)
                        step = 1 if b >= a else -1
                        for x in range(a, b + step, step):
                            out.append(str(x))
                        continue
                    if a_raw.isalpha() and b_raw.isalpha():
                        a = _alpha_to_num(a_raw)
                        b = _alpha_to_num(b_raw)
                        if a is not None and b is not None:
                            step = 1 if b >= a else -1
                            lower = a_raw.islower()
                            for x in range(a, b + step, step):
                                s = _num_to_alpha(x)
                                out.append(s.lower() if lower else s)
                            continue
                out.append(str(t))
            return out if out else ['']

        def _expand_glob_from_at_brace(expr: str):
            """Expand '@{...}' iterator as a filesystem glob.

            - If pattern is an absolute Windows drive/UNC path, glob it directly.
            - Otherwise glob relative to candidate dirs.
            - Returns [] when there are no matches (iterator row yields 0 instances).
            """
            s = (expr or '').strip()
            if not (s.startswith('@{') and s.endswith('}')):
                return None
            pat = s[2:-1].strip()
            if not pat:
                return []

            is_abs_win = bool(re.match(r"^[A-Za-z]:[\/]", pat)) or pat.startswith('\\')
            hits = {}

            def _add_hit(x: str, base=None, keep_abs: bool = False):
                try:
                    p = Path(x)
                    if not p.is_file():
                        return
                    rp = p.resolve()
                    if keep_abs:
                        tok = f"@{{{str(rp)}}}"
                    else:
                        try:
                            rel = rp.relative_to(Path(base).resolve())
                            logical = rel.as_posix()
                        except Exception:
                            logical = rp.as_posix()
                        tok = f"@{{{logical}}}"
                    hits[str(rp)] = tok
                except Exception:
                    return

            if is_abs_win:
                try:
                    for x in _glob.glob(pat):
                        _add_hit(x, base=None, keep_abs=True)
                except Exception:
                    pass
            else:
                try:
                    bases = list((SM.resolver.candidate_dirs() if SM is not None and getattr(SM, 'resolver', None) is not None else []) or [])
                except Exception:
                    bases = []
                if not bases:
                    bases = [Path('.')]
                for base in bases:
                    try:
                        for x in _glob.glob(str((Path(base) / pat))):
                            _add_hit(x, base=base, keep_abs=False)
                    except Exception:
                        continue

            out = [hits[k] for k in sorted(hits.keys())]
            if not out:
                try:
                    _l.w(f"[iter] glob: no matches for pattern='{pat}'")
                except Exception:
                    pass
            return out

        def _expand_spritesheet_wildcard(expr: str):
            """Expand '@alias[*]' into ['@alias[1]', ... '@alias[N]'].

            Only triggers for the exact wildcard form. Returns None when not applicable.
            Returns [] when applicable but alias is missing/unregistered.
            """
            s = (expr or '').strip()
            if not s.startswith('@'):
                return None
            m = re.match(r"^@([A-Za-z0-9_\-]+)\[\*\]$", s)
            if not m:
                return None
            a_name = m.group(1)
            try:
                reg = ss_registry or {}
                ss = reg.get(a_name)
                if ss is None:
                    try:
                        _l.w(f"[iter] spritesheet wildcard: alias '@{a_name}' not registered")
                    except Exception:
                        pass
                    return []
                n = int(getattr(ss, 'cols', 0) or 0) * int(getattr(ss, 'rows', 0) or 0)
                if n <= 0:
                    return []
                return [f"@{a_name}[{i}]" for i in range(1, n + 1)]
            except Exception:
                return []

        def _iter_suffix_to_ops(tail: str) -> str:
            t = (tail or '').strip()
            if not t:
                return ''
            if t.startswith(".Fit"):
                t = _fit_suffix_to_ops(t)
            elif t.startswith("^") or t.startswith("!") or t.startswith("|"):
                t = "~" + t
            elif not t.startswith("~"):
                return ''
            t = _normalize_ops_chain(t)
            return t or ''

        def _split_bracket_core_and_tail(expr: str):
            s = (expr or '').strip()
            if not s.startswith('['):
                return None, None
            depth = 0
            end = -1
            for i, ch in enumerate(s):
                if ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end < 0:
                return None, None
            return s[:end + 1], s[end + 1:].strip()

        def _parse_iter_seq(expr: str):
            """Parse an iterator expression (without star prefix) into list[str]."""
            s = (expr or '').strip()
            if not s:
                return ['']

            def _merge_iter_item_with_global_ops(item: str, global_ops: str) -> str:
                """Apply iterator-global ops to one item token.

                Precedence policy:
                  - global ops act as defaults
                  - item-local ops override global ones
                """
                tok = (item or '').strip()
                gops = _normalize_ops_chain(global_ops or "")
                if not tok or not gops:
                    return tok

                # If one iterator item expands to multiple top-level tokens
                # (e.g. a snippet expanding to "id1 id2"), apply global ops to
                # each token independently, then rebuild the multivalue cell.
                if any(ch.isspace() for ch in tok):
                    toks = _split_multivalue(tok)
                    if len(toks) > 1:
                        merged_parts = []
                        for tt in toks:
                            mt = _merge_iter_item_with_global_ops(tt, gops)
                            if mt:
                                merged_parts.append(mt)
                        return " ".join(merged_parts).strip()

                # 1) Source-like token (supports optional selector + fit suffix).
                m_src = re.match(
                    r"^\s*(?P<core>(?:@\{[^}]*\}|(?:Source|S)\s*\{[^}]*\}|https?://\S+?))\s*"
                    r"(?P<sel>\[[^\]]*\])?\s*"
                    r"(?P<tmods>(?:\.(?:Transform|T)\s*\{[^{}]*\})*)\s*"
                    r"(?P<tail>(?:\.(?:Fit)\s*\{[^}]*\}|~.*|[\^!\|].*)?)\s*$",
                    tok,
                    re.IGNORECASE,
                )
                if m_src:
                    core = (m_src.group("core") or "").strip()
                    sel = (m_src.group("sel") or "").strip()
                    tmods = (m_src.group("tmods") or "").strip()
                    tail = (m_src.group("tail") or "").strip()
                    item_ops = ""
                    if tail:
                        if tail.startswith(".Fit"):
                            item_ops = _fit_suffix_to_ops(tail)
                        elif tail.startswith("~"):
                            item_ops = _normalize_ops_chain(tail)
                        elif tail.startswith("^") or tail.startswith("!") or tail.startswith("|"):
                            item_ops = _normalize_ops_chain("~" + tail)
                    merged = _merge_fit_ops(gops, item_ops)
                    base_txt = f"{core}{sel}{tmods}"
                    return f"{base_txt}{merged}" if merged else base_txt

                # 2) Object-like token (id[=|+][~ops]).
                try:
                    base_id, place, ops_tok = _parse_object_token(tok)
                    mod = "" if place == "clone" else ("=" if place == "copy" else "+")
                    item_ops = _normalize_ops_chain(("~" + ops_tok) if (ops_tok or "").strip() else "")
                    merged = _merge_fit_ops(gops, item_ops)
                    return f"{base_id}{mod}{merged}" if merged else f"{base_id}{mod}"
                except Exception:
                    pass

                # 3) Fallback: if token already has ~suffix, merge there.
                if "~" in tok:
                    base, _sep, tail = tok.partition("~")
                    item_ops = _normalize_ops_chain("~" + (tail or ""))
                    merged = _merge_fit_ops(gops, item_ops)
                    return f"{base}{merged}" if merged else base

                # 4) Plain scalar token: append global ops.
                return f"{tok}{gops}"

            s_for_src, s_tr_spec = _split_transform_suffixes(s)
            src_v, sel_v, _ops_v, _tag_v = _parse_source_token_with_selector(s_for_src)
            if src_v:
                v_urls = _resolve_virtual_source_urls(SM, src_v, sel_v, warn_tag=_virtual_warn_tag(src_v, "wkmc.iter"))
                if v_urls is not None:
                    ops_norm = _normalize_ops_chain(_ops_v or "")
                    tmods = ""
                    if s_tr_spec is not None:
                        if getattr(s_tr_spec, "opacity", None):
                            tmods += f".T{{o={getattr(s_tr_spec, 'opacity')}}}"
                        if getattr(s_tr_spec, "filter_ref", None):
                            tmods += f".T{{f={getattr(s_tr_spec, 'filter_ref')}}}"
                        if getattr(s_tr_spec, "soft", None):
                            _vals = [str(v).strip() for v in (getattr(s_tr_spec, "soft") or []) if str(v).strip()]
                            if _vals:
                                if len(_vals) == 1:
                                    tmods += f".T{{s={_vals[0]}}}"
                                else:
                                    tmods += ".T{s=[" + " ".join(_vals) + "]}"
                    if ops_norm:
                        return [f"{u}{tmods}{ops_norm}" for u in v_urls]
                    return [f"{u}{tmods}" for u in v_urls]

            br_core, br_tail = _split_bracket_core_and_tail(s)
            if br_core is not None:
                seq = _parse_range_or_list(br_core)
                ops_norm = _iter_suffix_to_ops(br_tail or "")
                if br_tail and not ops_norm:
                    return [f"{v}{br_tail}" for v in seq]
                if ops_norm:
                    return [_merge_iter_item_with_global_ops(v, ops_norm) for v in seq]
                return seq

            # '@{...}' filesystem glob, optionally with fit/anchor suffix.
            if s.startswith('@{'):
                m_glob = re.match(
                    r"^\s*(?P<core>@\{[^}]*\})\s*(?P<tmods>(?:\.(?:Transform|T)\s*\{[^{}]*\})*)\s*(?P<tail>(?:\.(?:Fit)\s*\{[^}]*\}|~.*|[\^!\|].*)?)\s*$",
                    s,
                    re.IGNORECASE,
                )
                if m_glob:
                    core = (m_glob.group("core") or "").strip()
                    tmods = (m_glob.group("tmods") or "").strip()
                    tail = (m_glob.group("tail") or "").strip()
                    seq = _expand_glob_from_at_brace(core)
                    if seq is not None:
                        ops_norm = _iter_suffix_to_ops(tail)
                        if tail and not ops_norm:
                            _l.w(f"[iter] invalid iterator suffix after glob: '{tail}'")
                        if ops_norm:
                            return [f"{v}{tmods}{ops_norm}" for v in seq]
                        return [f"{v}{tmods}" for v in seq]

            # '@alias[*]' spritesheet wildcard, optionally with fit/anchor suffix.
            m_ss = re.match(
                r"^\s*(?P<core>@[A-Za-z0-9_\-]+\[\*\])\s*(?P<tmods>(?:\.(?:Transform|T)\s*\{[^{}]*\})*)\s*(?P<tail>(?:\.(?:Fit)\s*\{[^}]*\}|~.*|[\^!\|].*)?)\s*$",
                s,
                re.IGNORECASE,
            )
            ss_core = (m_ss.group("core") if m_ss else s)
            ss_tmods = (m_ss.group("tmods") if m_ss else "")
            ss_tail = (m_ss.group("tail") if m_ss else "")
            ss = _expand_spritesheet_wildcard(ss_core)
            if ss is not None:
                ops_norm = _iter_suffix_to_ops(ss_tail)
                if ss_tail and not ops_norm:
                    _l.w(f"[iter] invalid iterator suffix after spritesheet wildcard: '{ss_tail}'")
                if ops_norm:
                    return [f"{v}{ss_tmods}{ops_norm}" for v in ss]
                return [f"{v}{ss_tmods}" for v in ss]
            # fallback: treat as a scalar token
            return [s]

        def _expand_row_iterators(row0: dict):
            """Return (expanded_rows, has_iterators)."""
            cells0 = row0.get('cells')
            if not isinstance(cells0, list):
                return [row0], False
            # Parse each cell into parts so a single multivalue cell may contain
            # several iterator tokens (e.g. "*[a b]~i4 *[c d]~i6").
            # Same-level iterators are synchronized by index (zip-like), not cartesian.
            max_lv = 0
            has_iter = False
            level_cols = {}   # k -> set[idx]
            level_len = {}    # k -> maxlen across all iterator parts at level k
            cell_parts = {}   # idx -> list[("lit", str) | ("iter", k, seq)]

            for idx, c in enumerate(cells0):
                st = (str(c or '').strip())
                if not st:
                    continue
                toks = _split_multivalue(st) if any(ch.isspace() for ch in st) else [st]
                parts = []
                cell_has_iter = False
                for tok in (toks or []):
                    tt = (tok or '').strip()
                    if not tt:
                        continue
                    if tt.startswith('*'):
                        k = _count_leading_stars(tt)
                        expr = tt[k:].strip()
                        seq = _parse_iter_seq(expr)
                        parts.append(("iter", k, seq))
                        has_iter = True
                        cell_has_iter = True
                        max_lv = max(max_lv, k)
                        level_cols.setdefault(k, set()).add(idx)
                        try:
                            mlen = int(len(seq))
                        except Exception:
                            mlen = 1
                        level_len[k] = max(int(level_len.get(k, 1) or 1), max(mlen, 1))
                    else:
                        parts.append(("lit", tt))
                if cell_has_iter:
                    cell_parts[idx] = parts

            if (not has_iter) or max_lv <= 0:
                return [row0], False

            for k in range(1, max_lv + 1):
                if k not in level_len:
                    level_len[k] = 1

            # build nested loops via recursion
            out_rows = []
            idx_stack = [0] * (max_lv + 1)  # 1-based per level

            def _recur(level: int):
                if level > max_lv:
                    # materialize one instance
                    rr = dict(row0)
                    rr_cells = list(cells0)
                    for col_idx, parts in cell_parts.items():
                        out_toks = []
                        for p in (parts or []):
                            if not p:
                                continue
                            if p[0] == "lit":
                                v = str(p[1] or '').strip()
                                if v:
                                    out_toks.append(v)
                                continue
                            # ("iter", k, seq)
                            _k = int(p[1] or 1)
                            _seq = p[2] if len(p) > 2 else ['']
                            i_k = idx_stack[_k] if _k < len(idx_stack) else 0
                            val = ''
                            try:
                                if _seq:
                                    val = str(_seq[i_k % len(_seq)] or '').strip()
                            except Exception:
                                val = ''
                            if val:
                                out_toks.append(val)
                        rr_cells[col_idx] = " ".join(out_toks).strip()
                    rr['cells'] = rr_cells
                    out_rows.append(rr)
                    return
                for i in range(0, int(level_len.get(level, 1) or 1)):
                    idx_stack[level] = i
                    _recur(level + 1)

            _recur(1)

            # logging summary
            try:
                parts = []
                for k in range(1, max_lv + 1):
                    cols = sorted(level_cols.get(k, set()) or [])
                    if cols:
                        parts.append(f"L{k}={level_len.get(k, 1)} cols={cols}")
                _l.i(f"[iter] expanded row: levels={max_lv} " + " ".join(parts) + f" -> {len(out_rows)} inst")
            except Exception:
                pass
            return (out_rows if out_rows else [row0]), True

        def _as_bool(v) -> bool:
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "y", "on")
            return False

        for _row in (rows or []):
            row_list, has_iter = _expand_row_iterators(_row)
            iter_select = _row.get('__dm_iter_select__', []) or []
            if has_iter and iter_select:
                idx_sel = []
                if isinstance(iter_select, (list, tuple)):
                    for _v in iter_select:
                        try:
                            idx_sel.append(int(_v))
                        except Exception:
                            pass
                else:
                    idx_sel = _parse_index_selector_1based(str(iter_select), size=len(row_list))
                if idx_sel:
                    row_list = [
                        row_list[i1 - 1]
                        for i1 in idx_sel
                        if 1 <= int(i1) <= len(row_list)
                    ]
                has_iter = bool(row_list)
            holes_list = []
            _holes_raw = _row.get('__dm_holes__', []) or []
            if isinstance(_holes_raw, (list, tuple)):
                for _h in _holes_raw:
                    try:
                        holes_list.append(int(_h))
                    except Exception:
                        pass
            elif isinstance(_holes_raw, str):
                try:
                    holes_list = [int(n) for n in re.findall(r"\d+", _holes_raw)]
                except Exception:
                    holes_list = []
            else:
                try:
                    holes_list = [int(_holes_raw)]
                except Exception:
                    holes_list = []
            n_iter = len(row_list) if has_iter else 1
            try:
                copies_decl = int(_row.get('__dm_copies__', 1) or 1)
            except Exception:
                copies_decl = 1
            copies_explicit = _as_bool(_row.get('__dm_copies_explicit__', False))
            slot_select_raw = str(_row.get('__dm_slot_select__', '') or '').strip()
            slot_select_mode = str(_row.get('__dm_slot_select_mode__', '') or '').strip().lower()
            if slot_select_raw:
                if slot_select_mode == 'declarative':
                    targets = _expand_declarative_slot_selector(slot_select_raw)
                    for _i, tgt in enumerate(targets):
                        src = row_list[(_i % max(n_iter, 1))] if row_list else _row
                        r = dict(src)
                        if isinstance(r.get('cells'), list):
                            r['cells'] = list(r.get('cells') or [])
                        r['_i'] = _i
                        r['__dm_target_slot__'] = int(tgt)
                        if _i > 0 and '__dm_page__' in r:
                            r['__dm_page__'] = ''
                        yield r
                    continue
                if slot_select_mode == 'procedural':
                    targets, cursor_after = _expand_procedural_slot_selector(slot_select_raw)
                    for _i, tgt in enumerate(targets):
                        src = row_list[(_i % max(n_iter, 1))] if row_list else _row
                        r = dict(src)
                        if isinstance(r.get('cells'), list):
                            r['cells'] = list(r.get('cells') or [])
                        r['_i'] = _i
                        r['__dm_target_slot__'] = int(tgt)
                        if _i == (len(targets) - 1):
                            r['__dm_target_cursor_after__'] = int(cursor_after)
                        if _i > 0 and '__dm_page__' in r:
                            r['__dm_page__'] = ''
                        yield r
                    continue
            reps = max(copies_decl, 0) if copies_explicit else (n_iter if has_iter else 1)
            if reps <= 0:
                continue
            for _i in range(reps):
                src = row_list[(_i % max(n_iter, 1))] if row_list else _row
                r = dict(src)
                # copy positional cells list (do not share between instances)
                if isinstance(r.get('cells'), list):
                    r['cells'] = list(r.get('cells') or [])
                r['_i'] = _i
                if holes_list:
                    r['__dm_holes_after__'] = int(holes_list.count(_i + 1))
                if _i > 0 and '__dm_page__' in r:
                    r['__dm_page__'] = ''  # copias no repiten page
                yield r
    def _coerce_holes(val):
        """Coerce __dm_holes__ into a list of 1-based integers.
        In the legacy v10 monolith, __dm_holes__ was stored as a Python list[int].
        Some dataset loaders (CSV/GS) may stringify internal meta fields; in that
        case __dm_holes__ can arrive as a string like "[2]" or "2".
        This helper normalizes all of those cases so hole-based slot skipping
        preserves v10 behavior.
        """
        if val is None:
            return []
        if isinstance(val, (list, tuple)):
            out = []
            for x in val:
                try:
                    out.append(int(x))
                except Exception:
                    pass
            return out
        if isinstance(val, str):
            nums = re.findall(r"\d+", val)
            try:
                return [int(n) for n in nums]
            except Exception:
                return []
        try:
            return [int(val)]
        except Exception:
            return []
    # --- Dedup: shared helpers for @page/@back field fill and deferred FA (nested for safe closure) ---
    def _fill_instance_fields(inst_node, row_inst, row_map, use_jobs, fa_jobs, path_jobs, transform_jobs, *, clone_first: bool = False):
        """Apply dataset fields into a template instance, collecting deferred jobs."""
        passes = (True, False) if clone_first else (False, True)
        for want_clone in passes:
            for k, raw in _iter_row_fields(headers, row_inst):
                if not k or k.startswith('__dm_') or k.startswith('_'):
                    continue
                if want_clone:
                    if not k.startswith('clone_'):
                        continue
                else:
                    if k.startswith('clone_'):
                        continue
                apply_field_in_clone(
                    inst_node, k, raw, row_map,
                    root_doc=root, use_jobs=use_jobs, fa_jobs=fa_jobs, path_jobs=path_jobs,
                    use_seq=use_seq, layout_obj=planner.current.layout, sm=SM, ss_registry=ss_registry,
                    transform_jobs=transform_jobs
                )

    def _exec_use_fa_paths(inst_node, use_jobs, fa_jobs, path_jobs, transform_jobs, *, warn_tag: str, owner_group=None):
        """Center <use> elements, execute deferred Fit/Anchor jobs, then Paths jobs."""
        def _apply_fa(scope_node, base_id, r_id, ops_full, place_mode, rect_elem):
            try:
                return FA.apply_to_by_ids(scope_node, base_id, r_id, ops_full, place=place_mode, rect_elem=rect_elem)
            except Exception as ex:
                msg = str(ex or "")
                # Back/template passes can run on detached clones where defs are not reachable
                # through the local scope. Retry against document root.
                if "base id=" in msg and "not found" in msg:
                    return FA.apply_to_by_ids(root, base_id, r_id, ops_full, place=place_mode, rect_elem=rect_elem)
                raise

        for placeholder, u, tr_spec in (use_jobs or []):
            try:
                _center_use_over_placeholder(u, placeholder, dbg_fa_rect_ids=_DBG_FA_RECT_IDS)
            except Exception:
                pass
            try:
                if tr_spec is not None:
                    TFX.apply_transform_spec(root, u, tr_spec)
            except Exception as ex:
                _l.w(f"{warn_tag} deferred transform failed target='{(u.get('id') if u is not None else '')}': {ex}")
        _fa_remove_later = []
        for (base_id, r_id, ops_full, place_mode, placeholder_to_remove, rect_elem, tr_spec) in (fa_jobs or []):
            try:
                _placed = _apply_fa(inst_node, base_id, r_id, ops_full, place_mode, rect_elem)
                if tr_spec is not None and _placed is not None:
                    TFX.apply_transform_spec(root, _placed, tr_spec)
                if placeholder_to_remove is not None:
                    _fa_remove_later.append(placeholder_to_remove)
            except Exception as ex:
                _l.w(f"{warn_tag} deferred fit-anchor failed base='{base_id}' rect='{r_id}': {ex}")
        _queue_paths(int(planner.page_index), path_jobs, owner_group=owner_group)
        return _fa_remove_later

    # Template roots are prepared once in engine.py. Per-card instances must not
    # repeat full-subtree image/link normalization.
    # --- end dedup helpers ---

    def _queue_paths(page_index: int, path_jobs, *, owner_group=None):
        for (target_el, paths_spec_raw, orient_hint) in (path_jobs or []):
            deferred_path_jobs.append({
                'page_index': int(page_index),
                'target_el': target_el,
                'paths_spec_raw': paths_spec_raw,
                'orient_hint': orient_hint,
                'owner_group': owner_group,
            })

    def _flush_deferred_paths(*, warn_tag: str):
        if not deferred_path_jobs:
            return
        for job in list(deferred_path_jobs):
            try:
                _pi = int(job.get('page_index', 0) or 0)
                PATHS.render_paths_for_target(
                    geom_registry,
                    job.get('target_el'),
                    job.get('paths_spec_raw') or '',
                    page_index=_pi,
                    orient_hint=job.get('orient_hint'),
                    style_scope_node=root,
                    insert_parent=_page_group_for(_pi),
                    insert_after_elem=job.get('owner_group'),
                )
            except Exception as ex:
                _l.w(f"{warn_tag} deferred paths failed target='{(job.get('target_el').get('id') if job.get('target_el') is not None else '')}': {ex}")
        deferred_path_jobs.clear()

    marks_current = getattr(ctx, 'header_marks_current', None)
    if marks_current is None:
        marks_current = None
    placed = 0
    split_boards_used = False
    # Slot bookkeeping for extra passes (@back, @page)
    slot_records = []   # list of dicts: {slot_no, page_index, slot_in_page}
    page_states = {}    # page_index -> deepcopy(planner.current) (first seen)
    pending_page_req = {}       # slot_no -> list[dict]  (@page)
    pending_page_back_req = {}  # slot_no -> list[dict]  (@page @back)
    skipped_back_instances = 0
    geom_registry = GREG.GridGeometryRegistry()
    deferred_path_jobs = []

    def _parse_slot_selector(sel_raw: str):
        """Parse a @page selector cell.

        Two accepted forms:
          1) "~N..."  (1-based global slot selector)
             - The suffix (if any) is interpreted as Fit/Anchor ops relative to the page frame.
             - If the suffix is empty, default ops is "~5".
             - If the suffix does not start with '.' or '~', it's appended to "~5".

          2) "ops-only" (no "~N")
             - The whole cell is treated as Fit/Anchor ops.
             - The request is bound to the *current* row/slot page.

        This keeps a single Fit/Anchor code path: ops is always forwarded to fit_anchor.py
        (which already supports both short and long syntaxes).
        """
        s = (sel_raw or '').strip()
        if not s or s in ('0', '-'):
            return None, None

        # Form 1: "~N..."
        m = re.match(r"^~\s*(\d+)\s*(.*)$", s)
        if m:
            slot_no = int(m.group(1))
            tail = (m.group(2) or '').strip()
            if not tail:
                ops = "~5"
            else:
                if tail.startswith(('.', '~')):
                    ops = tail
                else:
                    ops = "~5" + tail
            return slot_no, ops

        # Form 2: ops-only â†’ bind to current slot/page.
        # Accept both long and short Fit/Anchor syntaxes.
        if s.startswith(('.', '~', '{')) or re.match(r"^[A-Za-z][\w\-.]*\s*\.Fit\s*\{", s):
            return 0, s

        return None, None

    def _queue_page_requests(row_inst: dict, row_map: dict):
        """Collect @page placement requests from this dataset row.

        Requests are keyed by the referenced global slot_no so they can be executed
        when that slot is actually reached (so we know the target page_index).
        """
        for te in (page_templates or []):
            # engine.py provides 'control_col' (not 'control_key')
            ckey = (te or {}).get('control_col') or (te or {}).get('control_key')
            raw = (row_map.get(ckey) or '').strip() if ckey else ''
            if not raw or raw in ('0', '-'):
                continue
            slot_no, ops = _parse_slot_selector(raw)
            if slot_no is None:
                _l.w(f"[@page] invalid selector '{raw}' in col '{ckey or ''}' â†’ skipped")
                continue
            if int(slot_no) == 0:
                # ops-only: bind to current row/slot page (resolved later, once slot_no is known)
                row_inst.setdefault('__dm_page_now__', []).append({
                    'te': te,
                    'row': row_inst,
                    'ops': ops,
                    'raw': raw,
                })
                _l.d("[@page] queued ops-only (bind to current slot)", {"bbox": (te or {}).get('bbox_id'), "ops": ops})
            else:
                pending_page_req.setdefault(int(slot_no), []).append({
                    'te': te,
                    'row': row_inst,
                    'ops': ops,
                    'raw': raw,
                })
        for te in (page_back_templates or []):
            ckey = (te or {}).get('control_col') or (te or {}).get('control_key')
            raw = (row_map.get(ckey) or '').strip() if ckey else ''
            if not raw or raw in ('0', '-'):
                continue
            slot_no, ops = _parse_slot_selector(raw)
            if slot_no is None:
                _l.w(f"[@page @back] invalid selector '{raw}' in col '{ckey or ''}' â†’ skipped")
                continue
            if int(slot_no) == 0:
                # ops-only @page @back: bind to this row's current front slot (resolved later)
                row_inst.setdefault('__dm_page_back_now__', []).append({
                    'te': te,
                    'row': row_inst,
                    'ops': ops,
                    'raw': raw,
                })
                _l.d("[@page @back] queued ops-only (bind to current slot)", {"bbox": (te or {}).get('bbox_id'), "ops": ops})
            else:
                pending_page_back_req.setdefault(int(slot_no), []).append({
                    'te': te,
                    'row': row_inst,
                    'ops': ops,
                    'raw': raw,
                })

    def _page_inner_rect_elem_for(page_index: int):
        """Build an ephemeral <rect> representing the page inner frame (after Page.border_mm)."""
        try:
            pinfo = planner.pages[int(page_index)]
        except Exception:
            return None
        try:
            cur_state = page_states.get(int(page_index)) or deepcopy(planner.current)
        except Exception:
            cur_state = deepcopy(planner.current)
        # Page.margins_mm() returns (left, top, right, bottom) in mm (with sign).
        try:
            l, t, r, b = (cur_state.page.margins_mm() if hasattr(cur_state, 'page') and cur_state.page else (0,0,0,0))
        except Exception:
            l, t, r, b = (0, 0, 0, 0)
        # IMPORTANT: @page builds an ephemeral rect (not attached to the doc). Do NOT rely on any
        # free variables here; always use the planner's px_per_mm.
        ppm = float(getattr(planner, 'px_per_mm', 1.0) or 1.0)
        x = float(pinfo.get('x', 0.0)) + float(l) * ppm
        y = float(pinfo.get('y', 0.0)) + float(t) * ppm
        w = float(pinfo.get('w', 0.0)) - (float(l) + float(r)) * ppm
        h = float(pinfo.get('h', 0.0)) - (float(t) + float(b)) * ppm
        if w <= 0 or h <= 0:
            return None
        rect = SVG.etree.Element(inkex.addNS('rect', 'svg'))
        rect.set('x', str(x)); rect.set('y', str(y))
        rect.set('width', str(w)); rect.set('height', str(h))
        return rect

    def _compute_split_grid(template_w: float, template_h: float, page_w: float, page_h: float, layout_obj):
        cols = int(getattr(layout_obj, 'cols', 0) or 0)
        rows = int(getattr(layout_obj, 'rows', 0) or 0)
        if cols <= 0 and rows <= 0:
            cols = max(1, int(math.ceil(float(template_w) / max(float(page_w), 1e-9))))
            rows = max(1, int(math.ceil(float(template_h) / max(float(page_h), 1e-9))))
        elif cols <= 0:
            rows = max(1, rows)
            cols = max(1, int(math.ceil(float(template_w) / max(float(page_w), 1e-9))))
        elif rows <= 0:
            cols = max(1, cols)
            rows = max(1, int(math.ceil(float(template_h) / max(float(page_h), 1e-9))))
        return cols, rows

    # Ensure @page is truly "once per page" **per dataset**.
    placed_page_once = set()  # (ds_idx, bbox_id, pass_tag, page_index)

    def _place_page_template_now(te: dict, row_inst: dict, ops: str, page_index: int, pass_tag: str, insert_after_elem=None):
        """Place a filled template once on a given page, anchored to the page inner rect."""
        nonlocal next_n
        bid = (te or {}).get('bbox_id') or ''
        key = (int(ds_idx), bid, pass_tag, int(page_index))
        if key in placed_page_once:
            _l.w(f"[@page] '{bid}' already placed on page {int(page_index)+1} ({pass_tag}); first wins")
            return 0
        tmpl_root = (te or {}).get('template_root')
        if tmpl_root is None:
            return 0
        # Build a temporary base node in a temp group so fit_anchor can deep-copy into out_layer.
        tmp_group = getattr(ctx, '_pnpink_tmp_group', None)
        if tmp_group is None:
            tmp_group = inkex.Group(); tmp_group.set('id', f"dm_tmp_{ds_idx}")
            # IMPORTANT: do NOT use display:none here.
            # inkex.bounding_box() can return None for nodes not rendered.
            # We keep it invisible but renderable so Fit/Anchor can measure.
            tmp_group.set('style', 'opacity:0;fill:none;stroke:none')
            _append_output(tmp_group)
        ctx._pnpink_tmp_group = tmp_group
        suffix = f"_MD{next_n}"; next_n += 1
        inst, _target_index_page = _instantiate_template(tmpl_root, suffix, f"@page:{bid}")
        page_wrap = inkex.Group(); page_wrap.set('id', root.get_unique_id(f"_MDpw{ds_idx}_{int(page_index)+1}"))
        page_wrap.append(inst)
        tmp_group.append(page_wrap)
        # Fill fields (same rules as normal templates)
        use_jobs = []; fa_jobs = []; path_jobs = []; transform_jobs = []
        row_map = _build_row_map(headers, row_inst)

        # Phase-1: reset per-instance keep-visible set (populated by parse_header_key on headers with '+')
        global _P1_KEEP_SET
        _P1_KEEP_SET = set()
        RAP._P1_KEEP_SET = _P1_KEEP_SET

        _fill_instance_fields(inst, row_inst, row_map, use_jobs, fa_jobs, path_jobs, transform_jobs, clone_first=False)
        # Execute <use> centering and deferred FA on the base instance (still in temp group)
        # DEBUG: collect rect/placeholder ids that FA will touch in this instance so we can detect early placeholder removals
        global _DBG_FA_RECT_IDS
        try:
            _DBG_FA_RECT_IDS = set()
            for (_b, _r, _ops, _pm, _ph_rm, _rect_elem, _tr_spec) in (fa_jobs or []):
                if _ph_rm is not None:
                    _pid = _ph_rm.get('id') or ''
                    if _pid: _DBG_FA_RECT_IDS.add(_pid)
                if _rect_elem is not None:
                    _rid = _rect_elem.get('id') or ''
                    if _rid: _DBG_FA_RECT_IDS.add(_rid)
        except Exception:
            _DBG_FA_RECT_IDS = None
        _fa_remove_later = _exec_use_fa_paths(inst, use_jobs, fa_jobs, path_jobs, transform_jobs, warn_tag='[@page]')
        # Phase-1: never delete rect anchors. At the end of this instance, hide rect anchors unless any header uses '+'.
        try:
            _keep = _P1_KEEP_SET if isinstance(_P1_KEEP_SET, set) else set()
        except Exception:
            _keep = set()

        # Collect candidate rect anchors from headers and from deferred FA placeholders.
        _rect_ids = set()
        try:
            for _k, _raw in _iter_row_fields(headers, row_inst):
                if not _k or _k.startswith('__dm_') or _k.startswith('_'):
                    continue
                _hk = parse_header_key_full(_k)
                _h_targets = (_hk.get('target_ids') or [(_hk.get('target_id') or '')])
                for _tid in _resolve_header_target_ids(inst, _h_targets):
                    if _tid:
                        _rect_ids.add(_tid)
        except Exception:
            pass
        try:
            for _ph in list(dict.fromkeys(_fa_remove_later)):
                _pid = (_ph.get('id') or '').strip()
                if _pid:
                    _rect_ids.add(_pid)
        except Exception:
            pass

        for _rid in sorted(_rect_ids):
            try:
                _e = SVG.find_target_exact_in(inst, _rid)
                _rid_eff = _rid
                if _e is None:
                    # Phase1: ids inside instances may be suffixed (_pnpNNNN). If header uses the base id,
                    # try to resolve a unique suffixed element in this instance.
                    try:
                        pref = _rid + "_pnp"
                        for _cand in inst.iter():
                            _cid = (_cand.get('id') or '')
                            if _cid.startswith(pref):
                                _e = _cand
                                _rid_eff = _cid
                                break
                    except Exception:
                        pass
                if _e is None:
                    continue
                if SVG.is_text_like(_e) or (_e.tag in TEXT_LIKE):
                    continue
                base_rid = SVG.strip_pnp_suffix(_rid_eff)
                if (_rid_eff in _keep) or (base_rid in _keep):
                    # keep visible: also remove any existing display:none set in the template or by previous passes
                    st = _e.get('style') or ''
                    # remove display:none occurrences
                    st2 = re.sub(r'(^|;)\s*display\s*:\s*none\s*(?=;|$)', r'\1', st)
                    # normalize stray double semicolons
                    st2 = re.sub(r';{2,}', ';', st2).strip()
                    if st2.endswith(';'):
                        st2 = st2[:-1]
                    if st2 != st:
                        _e.set('style', st2)
                    continue  # keep visible
                # apply display:none (but do it only now, after all measurements/placements)
                st = _e.get('style') or ''
                if 'display:' in st:
                    # replace existing display
                    st2 = re.sub(r"(?:(^|;)\s*display\s*:\s*[^;]+)", lambda m: (m.group(1) + "display:none"), st)
                    _e.set('style', st2)
                else:
                    if st and not st.strip().endswith(';'):
                        st = st.strip() + ';'
                    _e.set('style', (st or '') + "display:none")
            except Exception:
                pass

        # DEBUG: clear per-instance rect tracking
        try: _DBG_FA_RECT_IDS = None
        except Exception: pass
        # Phase-1: clear per-instance keep-visible set
        try:
            _P1_KEEP_SET = None
            RAP._P1_KEEP_SET = _P1_KEEP_SET
        except Exception: pass
        # Anchor to page frame
        rect = _page_inner_rect_elem_for(int(page_index))
        if rect is None:
            try:
                tmp_group.remove(page_wrap)
            except Exception:
                pass
            return 0

        # Resolve reliable bbox element inside the clone: prefer data-origid == bbox id.
        bbox_elem = None
        for n in page_wrap.iter():
            if (n.get('data-origid') or '') == bid:
                bbox_elem = n
                break
        if bbox_elem is None:
            for n in page_wrap.iter():
                if (n.get('id') or '') == bid:
                    bbox_elem = n
                    break
        if bbox_elem is None:
            _l.w(f"[@page] bbox anchor '{bid}' not found inside cloned template")

        try:
            base_id = page_wrap.get('id') or ''
            placed_node = FA.apply_to_by_ids(
                tmp_group,
                base_id,
                rect_id="",
                ops_full=ops,
                place="copy",
                rect_elem=rect,
                parent_elem=_page_group_for(int(page_index)),
                insert_after_elem=(insert_after_elem if insert_after_elem is not None else None),
                bbox_elem=bbox_elem,
            )
        except Exception as ex:
            _l.w(f"[@page] fit_anchor failed for '{bid}' page={int(page_index)+1} ops='{ops}': {ex}")
            placed_node = None
        _mark_generated(placed_node)

        # For duplex alignment, the back pass is mirrored horizontally at the PAGE level.
        # This mirrors both placement AND artwork (so the physical paper flip cancels it).
        if placed_node is not None and pass_tag == 'back':
            try:
                p = pages[int(page_index)]
                px = float(p.get('x', 0.0)); pw = float(p.get('w', 0.0))
                cx = px + pw * 0.5
                M = inkex.Transform(f"translate({2.0*cx},0) scale(-1,1)")
                curT = inkex.Transform(placed_node.get('transform') or "")
                placed_node.set('transform', str(M @ curT))
            except Exception as ex:
                _l.w(f"[@page @back] mirror failed page={int(page_index)+1}: {ex}")
        # Cleanup temp base
        try:
            tmp_group.remove(page_wrap)
        except Exception:
            pass
        if placed_node is not None:
            placed_page_once.add(key)
            return 1
        return 0
    
    # Iconify A' preload (parallel SVG downloads)
    #
    # Goal: resolve all icon:// references referenced in dataset into <defs>
    # before any fit-anchor (~i) runs.
    #
    # This keeps the rest of the pipeline unchanged: later occurrences of
    # icon:// will normalize to existing symbols via SourceManager.
    # ------------------------------------------------------------------
    if not getattr(ctx, '_iconify_preloaded', False):
        try:
            import iconify as ICON  # local module
        except Exception:
            ICON = None

        def _scan_icon_tokens(rows) -> list:
            out = set()
            if not rows:
                return []
            # conservative tokenization: find "icon://" and stop on common DSL delimiters
            stop_chars = set(['}', ' ', '\t', '\n', '\r', ')', '(', '"', "'", ']', '[', ',', ';'])
            for r in rows:
                if not isinstance(r, dict):
                    continue
                for v in list(_row_cells(r)):
                    if not isinstance(v, str):
                        continue
                    s = v
                    i = 0
                    while True:
                        j = s.find('icon://', i)
                        if j < 0:
                            break
                        k = j + len('icon://')
                        # allow optional leading '@' before icon://
                        if j > 0 and s[j-1] == '@':
                            # ignore the '@' (not part of URI)
                            pass
                        # read until stop or '~' (fit ops)
                        token = ''
                        while k < len(s):
                            ch = s[k]
                            if ch == '~' or ch in stop_chars:
                                break
                            # stop at '.Fit' module separator etc.
                            if ch == '.':
                                break
                            token += ch
                            k += 1
                        i = k
                        token = token.strip().lstrip('/')
                        if not token:
                            continue
                        if ':' in token:
                            # PnPInk rule: ':' separator not supported
                            continue
                        if '/' in token:
                            prefix, name = token.split('/', 1)
                            prefix = (prefix or '').strip().lower()
                            name = (name or '').strip()
                        else:
                            # icon://name  â†’ default set
                            prefix = 'noto'
                            name = token.strip()
                        if prefix and name:
                            out.add((prefix, name))
            return sorted(out)

        if ICON is not None and hasattr(ICON, 'ensure_icon_symbols_parallel'):
            try:
                icons = _scan_icon_tokens(rows_data)
                if icons:
                    # respect preferences for logging noisiness; workers is bounded.
                    try:
                        mw = int(prefs.get('iconify_max_workers', 12) or 12)
                    except Exception:
                        mw = 12
                    mw = max(1, min(mw, 32))
                    ICON.ensure_icon_symbols_parallel(root, icons, max_workers=mw)
            except Exception as ex:
                _l.w(f"[iconify] preload skipped/failed: {ex}")
        ctx._iconify_preloaded = True
    compiled_field_specs = _compile_field_specs(headers)
    compiled_clone_specs = [s for s in compiled_field_specs if s.get("is_clone")]
    compiled_apply_specs = [s for s in compiled_field_specs if (not s.get("is_clone")) and (not s.get("is_internal"))]
    def _header_may_touch_anchor(hk):
        if not hk:
            return False
        if str(hk.get("prop") or "text") != "text":
            return True
        for _tid in (hk.get("target_ids") or [hk.get("target_id") or ""]):
            if not _tid:
                continue
            if _is_id_wildcard_token(_tid):
                return True
            try:
                _t = SVG.find_target_exact_in(proto_root, _tid)
                if _t is None:
                    return True
                if not (SVG.is_text_like(_t) or (_t.tag in TEXT_LIKE)):
                    return True
            except Exception:
                return True
        return False

    compiled_rect_hks = [s.get("hk") for s in compiled_apply_specs if _header_may_touch_anchor(s.get("hk"))]

    def _prepare_origids_once(template_root):
        if template_root is None:
            return 0
        count = 0
        for el in template_root.iter():
            if not hasattr(el, 'tag') or not isinstance(el.tag, str):
                continue
            cur = el.get('id')
            if not cur or el.get('data-origid'):
                continue
            el.set('data-origid', SVG.strip_pnp_suffix(cur))
            count += 1
        return count

    prepared_origids = _prepare_origids_once(proto_root)
    for _tpl_entry in list(overlay_templates or []) + list(back_templates or []) + list(page_templates or []) + list(page_back_templates or []):
        prepared_origids += _prepare_origids_once((_tpl_entry or {}).get('template_root'))
    if prepared_origids:
        _l.i(f"[templates_ids] prepared data-origid for {prepared_origids} template id(s)")

    template_engine = prefs.get_template_engine("legacy") if hasattr(prefs, "get_template_engine") else "legacy"
    composed_plan_cache = {}
    composed_dynamic_ids = []
    for _spec in compiled_apply_specs:
        _hk = _spec.get("hk") or {}
        for _tid in (_hk.get("target_ids") or [(_hk.get("target_id") or "")]):
            _tid = str(_tid or "").strip()
            if _tid and not _is_id_wildcard_token(_tid):
                composed_dynamic_ids.append(_tid)
    for _bbox_id in [declared_bbox_id] + [
        (_tpl_entry or {}).get('bbox_id') or ''
        for _tpl_entry in list(overlay_templates or []) + list(back_templates or []) + list(page_templates or []) + list(page_back_templates or [])
    ]:
        _bbox_id = str(_bbox_id or '').strip()
        if _bbox_id and _bbox_id not in composed_dynamic_ids:
            composed_dynamic_ids.append(_bbox_id)

    def _template_plan_for(template_root, label: str):
        if template_engine != "composed" or template_root is None:
            return None
        key = id(template_root)
        if key in composed_plan_cache:
            return composed_plan_cache[key]
        try:
            prefix = root.get_unique_id(f"pnp_tpl_{ds_idx}_{len(composed_plan_cache) + 1}") if hasattr(root, "get_unique_id") else f"pnp_tpl_{ds_idx}_{len(composed_plan_cache) + 1}"
            plan = TCOMP.build_plan(
                root=root,
                proto_root=template_root,
                dynamic_ids=composed_dynamic_ids,
                block_id_prefix=prefix,
                has_overlays=False,
                has_back_templates=False,
                has_page_templates=False,
                has_clone_fields=bool(compiled_clone_specs),
                has_anchor_visibility=bool(compiled_rect_hks),
            )
            composed_plan_cache[key] = plan
            _l.i(
                f"[templates.compose] enabled template={label} static_blocks={plan.static_blocks} "
                f"dynamic_roots={plan.dynamic_roots} dynamic_ids={len(plan.dynamic_ids)}"
            )
            return plan
        except TCOMP.UnsupportedComposedTemplate as ex:
            composed_plan_cache[key] = None
            _l.i(f"[templates.compose] legacy template={label}: {ex}")
            return None

    def _instantiate_template(template_root, suffix: str, label: str):
        plan = _template_plan_for(template_root, label)
        if plan is not None:
            return TCOMP.instantiate_plan(plan, suffix, root_doc=root)
        inst = deepcopy(template_root)
        target_index = SVG.uniquify_ids_and_build_target_index(inst, suffix, root.get_unique_id)
        return inst, target_index

    if template_engine == "composed":
        _template_plan_for(proto_root, "main")
        _l.i(f"[templates.compose] mode=composed plans={sum(1 for v in composed_plan_cache.values() if v is not None)}")
    else:
        _l.i("[templates.compose] disabled template_engine=legacy")

    template_declared_bbox = None
    if declared_bbox_node is not None and declared_bbox_id:
        rid = SVG.resolve_local_id(proto_root, declared_bbox_id)
        bbox_elem = proto_root.find(f".//*[@id='{rid}']") if rid else None
        if bbox_elem is not None:
            try:
                bb = bbox_elem.bounding_box()
                template_declared_bbox = (float(bb.left), float(bb.top), float(bb.width), float(bb.height))
                _l.i(
                    f"[templates_bbox] cached declared bbox id='{declared_bbox_id}' "
                    f"x={template_declared_bbox[0]:.3f} y={template_declared_bbox[1]:.3f} "
                    f"w={template_declared_bbox[2]:.3f} h={template_declared_bbox[3]:.3f}"
                )
            except Exception as ex:
                _l.w(f"[templates_bbox] cannot cache declared bbox id='{declared_bbox_id}': {ex}")
        else:
            _l.w(f"[templates_bbox] declared bbox id='{declared_bbox_id}' not found in prepared template.")

    _profile = {
        "instances_ms": 0.0,
        "pre_ms": 0.0,
        "clone_ms": 0.0,
        "clone_deepcopy_ms": 0.0,
        "clone_flatten_ms": 0.0,
        "clone_absolutize_ms": 0.0,
        "clone_uniquify_ms": 0.0,
        "clone_index_ms": 0.0,
        "fields_ms": 0.0,
        "fields_fast_ms": 0.0,
        "fields_generic_ms": 0.0,
        "fields_fast_count": 0,
        "fields_fast_plain_count": 0,
        "fields_generic_count": 0,
        "bbox_ms": 0.0,
        "fit_ms": 0.0,
        "fit_slot_ms": 0.0,
        "fit_pagegroup_ms": 0.0,
        "fit_node_append_ms": 0.0,
        "fit_append_ms": 0.0,
        "fit_transform_ms": 0.0,
        "fit_geom_ms": 0.0,
        "fit_marks_ms": 0.0,
        "fa_ms": 0.0,
        "post_ms": 0.0,
        "row_total_ms": 0.0,
        "rows": 0,
    }
    _profile_dataset_t0 = time.perf_counter()
    _t_instances = time.perf_counter()
    instances = list(_iter_instances(rows_data))
    _profile["instances_ms"] = (time.perf_counter() - _t_instances) * 1000.0
    progress_total = int(len(instances))
    progress_step = max(1, progress_total // 200) if progress_total > 0 else 1
    if progress_total > 0:
        PROGRESS.emit(
            "render_rows_total",
            dataset_index=int(ds_idx),
            dataset_count=int(dataset_count),
            total=int(progress_total),
        )
    row_log_step = max(1, progress_total // 20) if progress_total > 0 else 1

    def _log_row_stage(row_idx: int, stage: str) -> None:
        if row_idx == 1 or row_idx == progress_total or (row_idx % row_log_step) == 0:
            _l.i(f"ROW {row_idx}: {stage}")

    for idx, row in enumerate(instances, start=1):
        _profile_row_t0 = time.perf_counter()
        _profile_phase_t0 = _profile_row_t0
        _log_row_stage(idx, "begin")
        if idx == 1 or idx == progress_total or (idx % progress_step) == 0:
            PROGRESS.emit(
                "render_row",
                current=int(idx),
                total=int(progress_total),
                dataset_index=int(ds_idx),
                dataset_count=int(dataset_count),
                dataset_current=int(idx),
                dataset_total=int(progress_total),
            )
        row_map = _build_row_map(headers, row)
        row_page   = (row.get("__dm_page__")   or "").strip()
        row_layout = (row.get("__dm_layout__") or "").strip()
        row_marks  = (row.get("__dm_marks__")  or "").strip()
        if (not row_page) and (not row_layout) and (not row_marks):
            is_placeholder = True
            for v in _row_cells(row):
                if str(v or "").strip() != "":
                    is_placeholder = False
                    break
            if is_placeholder:
                _log_row_stage(idx, "placeholder empty row, skip slot")
                planner.commit_slot()
                continue
        if row_page:
            _log_row_stage(idx, "apply PAGE preset")
            if re.fullmatch(r"\{\s*\}", row_page):
                _jump_page_with_marks(planner)
            else:
                m = re.match(r"^\{\s*(?:(?P<n>\d+)\s*(?:\*\s*(?P<body>[^\}]+))?)?\s*\}$", row_page)
                if m:
                    n = int(m.group("n") or 1)
                    body_txt = (m.group("body") or "").strip()
                    if body_txt == "":
                        for _ in range(n):
                            _jump_page_with_marks(planner)
                    else:
                        if planner.slot_index > 0:
                            _jump_page_with_marks(planner)
                        body = "{" + body_txt + "}"
                        ps_for_cursor = None
                        try:
                            ps_for_cursor = DSL.parse_page_block(body)
                        except Exception:
                            ps_for_cursor = None
                        page = LYT.parse_and_resolve_page(body, page, doc_page_mm)
                        current = _resolve_with_base(ctx, page, card, layout, gaps, doc_page_mm)
                        _old_page_idx = int(planner.page_index)
                        planner.apply_preset(current)
                        if int(planner.page_index) != _old_page_idx:
                            _flush_marks_for_page(_old_page_idx)
                        if ps_for_cursor is not None:
                            _apply_page_cursor_from_page(planner, ps_for_cursor)
                        for _ in range(n-1):
                            _jump_page_with_marks(planner)
                else:
                    if planner.slot_index > 0:
                        _jump_page_with_marks(planner)
                    ps_for_cursor = None
                    try:
                        ps_for_cursor = DSL.parse_page_block(row_page)
                    except Exception:
                        ps_for_cursor = None
                    page = LYT.parse_and_resolve_page(row_page, page, doc_page_mm)
                    current = _resolve_with_base(ctx, page, card, layout, gaps, doc_page_mm)
                    _old_page_idx = int(planner.page_index)
                    planner.apply_preset(current)
                    if int(planner.page_index) != _old_page_idx:
                        _flush_marks_for_page(_old_page_idx)
                    if ps_for_cursor is not None:
                        _apply_page_cursor_from_page(planner, ps_for_cursor)
            _l.i(f"Grid {planner.plan.cols}x{planner.plan.rows}, gaps {planner.current.gaps.h}Ã—{planner.current.gaps.v} mm; slots/page {planner.slots_per_page()}")
        if row_layout:
            _log_row_stage(idx, "apply LAYOUT tail")
            try:
                ls = DSL.parse_layout_block(row_layout)
            except Exception as ex:
                _l.w(f"layout tail invÃ¡lido '{row_layout}': {ex}")
                ls = None
            if ls is not None:
                page, card, layout, gaps = LYT.apply_layout_spec((page, card, layout, gaps), ls)
                current = _resolve_with_base(ctx, page, card, layout, gaps, doc_page_mm)
                _old_page_idx = int(planner.page_index)
                planner.apply_preset(current)
                if int(planner.page_index) != _old_page_idx:
                    _flush_marks_for_page(_old_page_idx)
                _shape = (getattr(layout, 'smart_shape', None) or card.name or '').strip()
                _orient = (getattr(layout, 'smart_hex_orient', None) or '').strip()
                _shape_dbg = _shape if not _orient else f"{_shape}/{_orient}"
                _l.i(f"Tail applied: g={layout.cols}x{layout.rows} inv=({layout.invert_cols},{layout.invert_rows}) rowMajor={layout.sweep_rows_first} k={gaps.h}Ã—{gaps.v} s='{_shape_dbg}'")
        if row_marks:
            _log_row_stage(idx, "apply MARKS tail")
            if row_marks in ("0", "-"):
                marks_current = None
            else:
                try:
                    marks_current = DSL.parse_marks_block(row_marks)
                except Exception as ex:
                    _l.w(f"marks tail invÃ¡lido '{row_marks}': {ex}")
        # Control-only row: apply Page/Layout/Marks but do not create an instance
        # when all payload cells (columns B+) are empty.
        if (row_page or row_layout or row_marks):
            has_payload = False
            for v in _row_cells(row):
                if str(v or "").strip() != "":
                    has_payload = True
                    break
            if not has_payload:
                _log_row_stage(idx, "control-only row, no instance")
                continue
        # Collect any @page requests from this row (they'll be executed when their referenced slot is reached)
        _queue_page_requests(row, row_map)

        target_within = None
        if str(row.get('__dm_target_slot__', '') or '').strip():
            try:
                target_within = int(row.get('__dm_target_slot__')) - 1
            except Exception:
                target_within = None
        if target_within is not None:
            sg = _slot_geom_for_within(planner, target_within)
            if sg is None:
                _l.w(f"[slots] target slot out of range: {int(target_within)+1} (per_page={len(getattr(planner,'local_slots',[]) or [])})")
                continue
            slot_x, slot_y, _slot_w_target, _slot_h_target = sg
            slot_within_for_record = int(target_within)
        else:
            slot_x, slot_y = _get_or_advance_slot(planner)
            _slot_w_target = _slot_h_target = None
            slot_within_for_record = int(planner.slot_index)

        # Register slot mapping (1-based global slot number)
        slot_no = len(slot_records) + 1
        if int(planner.page_index) not in page_states:
            try:
                page_states[int(planner.page_index)] = deepcopy(planner.current)
            except Exception:
                page_states[int(planner.page_index)] = None
        slot_records.append({
            'slot_no': int(slot_no),
            'page_index': int(planner.page_index),
            'slot_in_page': int(slot_within_for_record),
            'row': row,
        })

        # Execute any ops-only @page requests bound to this row's current slot/page.
        # This supports long/short Fit/Anchor syntaxes without forcing a "~N" selector.
        if '__dm_page_now__' in row:
            reqs_now = row.pop('__dm_page_now__', []) or []
            for rq in reqs_now:
                try:
                    _l.d("[@page] place ops-only on current page", {"slot": int(slot_no), "page": int(planner.page_index)+1, "bbox": (rq.get('te') or {}).get('bbox_id'), "ops": rq.get('ops')})
                    _place_page_template_now(
                        rq.get('te'),
                        rq.get('row') or {},
                        rq.get('ops') or '~5',
                        int(planner.page_index),
                        pass_tag='front',
                        insert_after_elem=(out_layer[-1] if len(out_layer) > 0 else None),
                    )
                except Exception as ex:
                    _l.w(f"[@page] placement failed (current slot {slot_no}): {ex}")

        # Ops-only selectors for @page @back are bound to the current front slot.
        # We enqueue them under this slot_no so they can be placed during the back pass.
        if '__dm_page_back_now__' in row:
            reqs_now = row.pop('__dm_page_back_now__', []) or []
            if reqs_now:
                pending_page_back_req.setdefault(int(slot_no), []).extend(reqs_now)
                _l.d("[@page @back] queued ops-only to slot", {"slot": int(slot_no), "count": len(reqs_now)})

        # Execute any pending @page requests whose selector points to this slot_no.
        # This makes page membership deterministic and keeps Z-order tied to dataset order.
        if int(slot_no) in pending_page_req:
            reqs = pending_page_req.pop(int(slot_no), [])
            for rq in (reqs or []):
                try:
                    _place_page_template_now(
                        rq.get('te'),
                        rq.get('row') or {},
                        rq.get('ops') or '~5',
                        int(planner.page_index),
                        pass_tag='front',
                        insert_after_elem=(out_layer[-1] if len(out_layer) > 0 else None),
                    )
                except Exception as ex:
                    _l.w(f"[@page] placement failed at slot {slot_no}: {ex}")
        _profile["pre_ms"] += (time.perf_counter() - _profile_phase_t0) * 1000.0
        _profile_phase_t0 = time.perf_counter()
        card_group = inkex.Group()
        card_item_no = int(next_n)
        suffix_main = f"_MD{next_n}"; next_n += 1
        _clone_t = time.perf_counter()
        inst_main, target_index_main = _instantiate_template(proto_root, suffix_main, "main")
        _profile["clone_deepcopy_ms"] += (time.perf_counter() - _clone_t) * 1000.0
        inst_jobs = []  # list of dicts: {node, use_jobs, fa_jobs, path_jobs, suffix, bbox_id}
        inst_jobs.append({
            'node': inst_main,
            'target_index': target_index_main,
            'use_jobs': [],
            'fa_jobs': [],
            'path_jobs': [],
            'transform_jobs': [],
            'suffix': suffix_main,
            'bbox_id': (declared_bbox_id or ''),
            'overlay_ops': '',
        })
        for ot_i, ot in enumerate(overlay_templates or [], start=1):
            # engine.py provides 'control_col' for template-columns
            ctrl_key = (ot or {}).get('control_col') or (ot or {}).get('control_key')
            ctrl_val = (row_map.get(ctrl_key) or '').strip() if ctrl_key else ''
            if ctrl_val in ('0', '-'):
                continue
            tmpl_ov = (ot or {}).get('template_root')
            if tmpl_ov is None:
                continue
            suffix_ov = f"_MD{next_n}"; next_n += 1
            _clone_t = time.perf_counter()
            inst_ov, target_index_ov = _instantiate_template(tmpl_ov, suffix_ov, "overlay")
            _profile["clone_deepcopy_ms"] += (time.perf_counter() - _clone_t) * 1000.0
            inst_jobs.append({
                'node': inst_ov,
                'target_index': target_index_ov,
                'use_jobs': [],
                'fa_jobs': [],
                'path_jobs': [],
                'transform_jobs': [],
                'suffix': suffix_ov,
                'bbox_id': ((ot or {}).get('bbox_id') or ''),
                'overlay_ops': ctrl_val,
            })
        _profile["clone_ms"] += (time.perf_counter() - _profile_phase_t0) * 1000.0
        def _apply_field_any(spec, raw):
            key = spec.get("key") if isinstance(spec, dict) else str(spec or "")
            hk = spec.get("hk") if isinstance(spec, dict) else None
            fast_text_target = spec.get("fast_text_target") if isinstance(spec, dict) else ""
            fast_text_plain = bool(spec.get("fast_text_plain")) if isinstance(spec, dict) else False
            if fast_text_target:
                _field_t0 = time.perf_counter()
                value = raw
                if ("\\" in value) or ("${" in value):
                    value = expand_value(value, row_map)
                for j in inst_jobs:
                    tgt = (j.get("target_index") or {}).get(fast_text_target)
                    if tgt is None:
                        continue
                    if SVG.is_text_like(tgt) or (tgt.tag in TEXT_LIKE):
                        if fast_text_plain:
                            SVG.clear_children(tgt)
                            tgt.text = "" if value is None else str(value)
                            _profile["fields_fast_plain_count"] += 1
                        elif not SVG.replace_text_fast(tgt, value):
                            SVG.replace_text(tgt, value)
                        _profile["fields_fast_ms"] += (time.perf_counter() - _field_t0) * 1000.0
                        _profile["fields_fast_count"] += 1
                        return 1, "text"
                # If the indexed fast path cannot find the target, fall through
                # to the generic handler so missing/renamed ids keep producing
                # the same diagnostics and behavior as complex fields.
            _field_t0 = time.perf_counter()
            for j in inst_jobs:
                cnt, st = apply_field_in_clone(
                    j['node'], key, raw, row_map,
                    root_doc=root, use_jobs=j['use_jobs'], fa_jobs=j['fa_jobs'], path_jobs=j['path_jobs'],
                    use_seq=use_seq, layout_obj=planner.current.layout, sm=SM, ss_registry=ss_registry,
                    transform_jobs=j['transform_jobs'],
                    target_index=j.get('target_index'),
                    header_info=hk,
                )
                if st != 'miss':
                    _profile["fields_generic_ms"] += (time.perf_counter() - _field_t0) * 1000.0
                    _profile["fields_generic_count"] += 1
                    return cnt, st
            _profile["fields_generic_ms"] += (time.perf_counter() - _field_t0) * 1000.0
            _profile["fields_generic_count"] += 1
            return 0, 'miss'

        # Phase-1: per-row keep-visible set for rect anchors (populated by parse_header_key on headers with '+').
        # We reset it once per placed card so visibility decisions are deterministic and do not leak across rows.
        global _P1_KEEP_SET
        _P1_KEEP_SET = set()
        RAP._P1_KEEP_SET = _P1_KEEP_SET
        for _spec in compiled_apply_specs:
            _hk = _spec.get("hk") or {}
            if not (bool(_hk.get("header_plus") or False) or str(_hk.get("prop") or "text") != "text"):
                continue
            for _tid in (_hk.get("target_ids") or [(_hk.get("target_id") or "")]):
                if _tid and not RAP._is_id_wildcard_token(_tid):
                    _P1_KEEP_SET.add(_tid)

        _profile_phase_t0 = time.perf_counter()
        _cells_for_fields = _row_cells(row)
        for _spec in compiled_clone_specs:
            _i = int(_spec.get("index") or 0)
            _raw = _cells_for_fields[_i] if _i < len(_cells_for_fields) else ""
            _apply_field_any(_spec, "" if _raw is None else str(_raw))
        for _spec in compiled_apply_specs:
            _i = int(_spec.get("index") or 0)
            _raw = _cells_for_fields[_i] if _i < len(_cells_for_fields) else ""
            _apply_field_any(_spec, "" if _raw is None else str(_raw))
        _profile["fields_ms"] += (time.perf_counter() - _profile_phase_t0) * 1000.0
        if len(inst_jobs) > 1 and declared_bbox_id:
            rid_main = SVG.resolve_local_id(inst_main, declared_bbox_id)
            rect_elem_main = inst_main.find(f".//*[@id='{rid_main}']") if rid_main else None
            if rect_elem_main is None:
                _l.w(f"[overlay] main bbox id '{declared_bbox_id}' not found in main instance; overlays will keep their native positions.")
            else:
                if inst_main.getparent() is None:
                    card_group.append(inst_main)
                for _j in inst_jobs[1:]:
                    _n = _j.get('node')
                    if _n is not None and _n.getparent() is None:
                        card_group.append(_n)
                insert_after = inst_main  # keep declared order above the main
                for j in inst_jobs[1:]:
                    ov_node = j.get('node')
                    if ov_node is None:
                        continue
                    ops_raw = (j.get('overlay_ops') or '').strip()
                    if ops_raw in ('0', '-'):
                        continue
                    ops = ops_raw if ops_raw else "~5"
                    if ops.startswith(".F"):
                        ops = ".Fit" + ops[2:]
                    ops = ops.replace(".F{", ".Fit{").replace(".F {", ".Fit {")
                    try:
                        base_id = ov_node.get('id') or ''
                        if not base_id:
                            continue
                        placed_ov = FA.apply_to_by_ids(
                            card_group,
                            base_id,
                            rect_id="",
                            ops_full=ops,
                            place="copy",
                            rect_elem=rect_elem_main,
                            parent_elem=card_group,
                            insert_after_elem=insert_after,
                        )
                        try:
                            ov_node.getparent().remove(ov_node)
                        except Exception:
                            pass
                        if placed_ov is not None:
                            j['node'] = placed_ov
                            insert_after = placed_ov
                    except Exception as ex:
                        _l.w(f"[overlay] fit_anchor failed for overlay id='{(ov_node.get('id') if ov_node is not None else '')}' ops='{ops}': {ex}")
        if inst_main.getparent() is None:
            card_group.append(inst_main)
        for j in inst_jobs[1:]:
            n = j.get('node')
            if n is not None and n.getparent() is None:
                card_group.append(n)
        _profile_phase_t0 = time.perf_counter()
        _log_row_stage(idx, "place card")
        placed_node = None
        if template_declared_bbox is not None:
            bx, by, bw, bh = template_declared_bbox
        elif declared_bbox_node is not None and declared_bbox_id:
            rid = SVG.resolve_local_id(inst_main, declared_bbox_id)
            bbox_elem = inst_main.find(f".//*[@id='{rid}']") if rid else None
            if bbox_elem is None:
                _l.w(
                    f"[templates_bbox] bbox id '{declared_bbox_id}' not found in main instance as '{rid}'. "
                    f"Falling back to pick_anchor_in()."
                )
                an = SVG.pick_anchor_in(inst_main)
                bb = an.bounding_box()
                bx, by, bw, bh = float(bb.left), float(bb.top), float(bb.width), float(bb.height)
            else:
                bb = bbox_elem.bounding_box()
                bx, by, bw, bh = float(bb.left), float(bb.top), float(bb.width), float(bb.height)
        else:
            an = SVG.pick_anchor_in(inst_main)
            bb = an.bounding_box()
            bx, by, bw, bh = float(bb.left), float(bb.top), float(bb.width), float(bb.height)
        _profile["bbox_ms"] += (time.perf_counter() - _profile_phase_t0) * 1000.0
        # Split boards when template bbox exceeds page inner frame.
        inner_rect_now = _page_inner_rect_elem_for(int(planner.page_index))
        split_boards = False
        use_swap = False
        page_w_px, page_h_px = planner.page_size_px()
        ppm = float(getattr(planner, 'px_per_mm', 1.0) or 1.0)
        try:
            mg = SVG.coerce_margins_mm(planner.current.page.margins_mm())
        except Exception:
            mg = SVG.coerce_margins_mm((0, 0, 0, 0))

        def _inner_dims_for(pw, ph):
            cw = float(pw) - (float(mg.left) + float(mg.right)) * ppm
            ch = float(ph) - (float(mg.top) + float(mg.bottom)) * ppm
            return cw, ch

        piw, pih = _inner_dims_for(page_w_px, page_h_px)
        if inner_rect_now is not None:
            try:
                split_boards = (bw > piw + 1e-6) or (bh > pih + 1e-6)
            except Exception:
                split_boards = False

        if split_boards and inner_rect_now is not None:
            split_boards_used = True
            # Auto orientation: pick the page orientation that minimizes page count.
            auto_layout = (int(getattr(planner.current.layout, 'cols', 0) or 0) <= 0 and int(getattr(planner.current.layout, 'rows', 0) or 0) <= 0)
            cols, rows = _compute_split_grid(bw, bh, piw, pih, planner.current.layout)
            if auto_layout:
                piw2, pih2 = _inner_dims_for(page_h_px, page_w_px)
                cols2, rows2 = _compute_split_grid(bw, bh, piw2, pih2, planner.current.layout)
                if (cols2 * rows2) < (cols * rows):
                    use_swap = True
                    cols, rows = cols2, rows2
                    piw, pih = piw2, pih2
                    page_w_px, page_h_px = page_h_px, page_w_px
            _l.i(f"[split_boards] row={idx} bbox=({bw:.2f}x{bh:.2f}) page_inner=({piw:.2f}x{pih:.2f}) grid={cols}x{rows} swap={use_swap}")
            tile_w = float(bw) / float(max(cols, 1))
            tile_h = float(bh) / float(max(rows, 1))
            if int(planner.slot_index) != 0:
                _jump_page_with_marks(planner)

            # Arrange split pages in a grid matching the crop layout.
            base_page_idx = int(planner.page_index)
            try:
                base_page = planner.pages[base_page_idx]
                base_x = float(base_page.get('x', 0.0))
                base_y = float(base_page.get('y', 0.0))
            except Exception:
                base_x = 0.0; base_y = 0.0
            gap_px = float(getattr(planner, 'page_gap_px', 0.0) or 0.0)

            part_idx = 0
            for rr in range(rows):
                for cc in range(cols):
                    if part_idx > 0:
                        _jump_page_with_marks(planner)
                    part_idx += 1

                    # Update page size/orientation if swap was selected.
                    try:
                        pinfo = planner.pages[int(planner.page_index)]
                        if pinfo is not None:
                            pinfo['w'] = float(page_w_px)
                            pinfo['h'] = float(page_h_px)
                            pel = pinfo.get('el')
                            if pel is not None:
                                pel.set('width', str(float(page_w_px)))
                                pel.set('height', str(float(page_h_px)))
                    except Exception:
                        pass

                    # Place page grid position (matching tile order).
                    try:
                        pinfo = planner.pages[int(planner.page_index)]
                        if pinfo is not None:
                            px = base_x + cc * (float(page_w_px) + gap_px)
                            py = base_y + rr * (float(page_h_px) + gap_px)
                            pinfo['x'] = px; pinfo['y'] = py
                            pel = pinfo.get('el')
                            if pel is not None:
                                pel.set('x', str(px))
                                pel.set('y', str(py))
                    except Exception:
                        pass

                    inner_rect = _page_inner_rect_elem_for(int(planner.page_index))
                    if inner_rect is None:
                        continue
                    dx = float(inner_rect.get('x') or 0.0)
                    dy = float(inner_rect.get('y') or 0.0)
                    dw = float(inner_rect.get('width') or 0.0)
                    dh = float(inner_rect.get('height') or 0.0)
                    src_x = float(bx) + float(cc) * tile_w
                    src_y = float(by) + float(rr) * tile_h
                    part = deepcopy(card_group)
                    suffix_part = f"_MD{next_n}"; next_n += 1
                    SVG.uniquify_all_ids_in_scope(part, suffix_part, root.get_unique_id)
                    _append_output(part, page_index=int(planner.page_index))
                    SVG.apply_clip_from_rect(
                        root,
                        part,
                        (src_x, src_y, tile_w, tile_h),
                        stage='split_board',
                        clip_id=f"_MDcl{next_n - 1}",
                    )
                    # No scaling: just translate so tile is centered in the page inner rect.
                    tx = dx + (dw - tile_w) * 0.5 - src_x
                    ty = dy + (dh - tile_h) * 0.5 - src_y
                    try:
                        curT = inkex.Transform(part.get('transform') or "")
                    except Exception:
                        curT = inkex.Transform()
                    T_split = inkex.Transform(f"translate({tx},{ty})")
                    part.set('transform', str(T_split @ curT))

                    if marks_current is not None:
                        try:
                            mx = dx + (dw - tile_w) * 0.5
                            my = dy + (dh - tile_h) * 0.5
                            jobs = _marks_pending_by_page.setdefault(int(planner.page_index), [])
                            jobs.append({
                                'ms': marks_current,
                                'parent': _page_group_for(int(planner.page_index)),
                                'bbox': (float(mx), float(my), float(tile_w), float(tile_h)),
                                'within': 0,
                                'r': 0,
                                'c': 0,
                                'rows': 1,
                                'cols': 1,
                                'gaps_has_offsets': _gaps_has_offsets(planner.current.layout),
                                'smart_shape': (getattr(planner.current.layout, 'smart_shape', None) or '').strip().lower(),
                                'smart_hex_orient': (getattr(planner.current.layout, 'smart_hex_orient', None) or '').strip().lower(),
                            })
                        except Exception as ex:
                            _l.w(f"[marks] render failed (split): {ex}")

                    page1 = int(planner.page_index) + 1
                    part_name = f"_MDgc{next_n - 1}_{cc+1}_{rr+1}"
                    part.set('id', part_name)
                    part.set(inkex.addNS('label', 'inkscape'), part_name)
            placed += 1
            _profile["row_total_ms"] += (time.perf_counter() - _profile_row_t0) * 1000.0
            _profile["rows"] += 1
            planner.commit_slot()
            continue

        _profile_phase_t0 = time.perf_counter()
        if (_slot_w_target is not None) and (_slot_h_target is not None):
            slot_w, slot_h = _slot_w_target, _slot_h_target
        else:
            try:
                _, _, slot_w, slot_h = planner.local_slots[planner.slot_index]
            except Exception:
                slot_w, slot_h = bw, bh
        _sub_t = time.perf_counter()
        _profile["fit_slot_ms"] += (_sub_t - _profile_phase_t0) * 1000.0
        _pg_t = time.perf_counter()
        _mark_generated(card_group)
        parent = _page_group_for(int(planner.page_index))
        _pg_t2 = time.perf_counter()
        parent.append(card_group)
        _pg_t3 = time.perf_counter()
        _profile["fit_pagegroup_ms"] += (_pg_t2 - _pg_t) * 1000.0
        _profile["fit_node_append_ms"] += (_pg_t3 - _pg_t2) * 1000.0
        _sub_t2 = time.perf_counter()
        _profile["fit_append_ms"] += (_sub_t2 - _sub_t) * 1000.0
        _sub_t = _sub_t2
        placed_node = card_group
        sx = (float(slot_w) / float(bw)) if bw else 1.0
        sy = (float(slot_h) / float(bh)) if bh else 1.0
        tx = float(slot_x) - (float(bx) * sx)
        ty = float(slot_y) - (float(by) * sy)
        card_group.set('transform', f"matrix({sx:.12g} 0 0 {sy:.12g} {tx:.12g} {ty:.12g})")
        _sub_t2 = time.perf_counter()
        _profile["fit_transform_ms"] += (_sub_t2 - _sub_t) * 1000.0
        _sub_t = _sub_t2
        geom_registry.add_group(int(planner.page_index), card_group)
        _sub_t2 = time.perf_counter()
        _profile["fit_geom_ms"] += (_sub_t2 - _sub_t) * 1000.0
        _sub_t = _sub_t2
        if marks_current is not None:
            try:
                ms = marks_current
                within = int(slot_within_for_record)
                r0, c0 = _slot_index_to_rc(within, planner.plan, planner.current.layout)
                jobs = _marks_pending_by_page.setdefault(int(planner.page_index), [])
                jobs.append({
                    'ms': ms,
                    'parent': parent,
                    'bbox': (float(slot_x), float(slot_y), float(slot_w), float(slot_h)),
                    'within': within,
                    'r': int(r0),
                    'c': int(c0),
                    'rows': int(getattr(planner.plan, 'rows', 0) or 0),
                    'cols': int(getattr(planner.plan, 'cols', 0) or 0),
                    'gaps_has_offsets': _gaps_has_offsets(planner.current.layout),
                    'smart_shape': (getattr(planner.current.layout, 'smart_shape', None) or '').strip().lower(),
                    'smart_hex_orient': (getattr(planner.current.layout, 'smart_hex_orient', None) or '').strip().lower(),
                })
            except Exception as ex:
                _l.w(f"[marks] render failed: {ex}")
        _profile["fit_marks_ms"] += (time.perf_counter() - _sub_t) * 1000.0
        _profile["fit_ms"] += (time.perf_counter() - _profile_phase_t0) * 1000.0
        # DEBUG: collect rect/placeholder ids that FA will touch in this ROW so we can detect early placeholder removals
        global _DBG_FA_RECT_IDS
        try:
            _DBG_FA_RECT_IDS = set()
            for _jdbg in (inst_jobs or []):
                for (_b, _r, _ops, _pm, _ph_rm, _rect_elem, _tr_spec) in (_jdbg.get('fa_jobs') or []):
                    if _ph_rm is not None:
                        _pid = _ph_rm.get('id') or ''
                        if _pid: _DBG_FA_RECT_IDS.add(_pid)
                    if _rect_elem is not None:
                        _rid = _rect_elem.get('id') or ''
                        if _rid: _DBG_FA_RECT_IDS.add(_rid)
        except Exception:
            _DBG_FA_RECT_IDS = None
        _profile_phase_t0 = time.perf_counter()
        _log_row_stage(idx, "fit-anchor")
        _fa_remove_later = []
        for j in inst_jobs:
            _fa_remove_later.extend(_exec_use_fa_paths(
                j.get('node'),
                j.get('use_jobs') or [],
                j.get('fa_jobs') or [],
                j.get('path_jobs') or [],
                j.get('transform_jobs') or [],
                warn_tag='[deckmaker]',
                owner_group=card_group,
            ))
        # Remove placeholders at the end so the same rect can be reused (multivalue/dup headers).
        for _ph in list(dict.fromkeys(_fa_remove_later)):
            try:
                par = _ph.getparent()
                if par is not None:
                    if not _is_rect_elem(_ph):

                        par.remove(_ph)
            except Exception:
                pass
        _profile["fa_ms"] += (time.perf_counter() - _profile_phase_t0) * 1000.0

        # Phase-1: hide non-text anchors at the end of the row, unless any duplicate header for that base id used '+'.
        # This must happen AFTER all bbox measurements / placements.
        try:
            _keep = _P1_KEEP_SET if isinstance(_P1_KEEP_SET, set) else set()
        except Exception:
            _keep = set()
        _profile_phase_t0 = time.perf_counter()

        # Collect header specs; wildcard targets are resolved per-scope.
        _rect_hks = compiled_rect_hks

        def _apply_anchor_visibility(_scope, _target_index=None):
            _rect_ids = set()
            for _hk in (_rect_hks or []):
                _h_targets = (_hk.get('target_ids') or [(_hk.get('target_id') or '')])
                for _tid in _resolve_header_target_ids(_scope, _h_targets):
                    if _tid:
                        _rect_ids.add(_tid)
            for _rid in _rect_ids:
                try:
                    _e = SVG.find_target_exact_in(_scope, _rid, target_index=_target_index)
                    _rid_eff = _rid
                    if _e is None:
                        # ids inside instances are suffixed (_pnpNNNN). Resolve a unique suffixed element in this scope.
                        pref = _rid + "_pnp"
                        for _cand in _scope.iter():
                            _cid = (_cand.get('id') or '')
                            if _cid.startswith(pref):
                                _e = _cand
                                _rid_eff = _cid
                                break
                    if _e is None:
                        continue
                    if SVG.is_text_like(_e) or (_e.tag in TEXT_LIKE):
                        continue
                    base_rid = SVG.strip_pnp_suffix(_rid_eff)
                    if (_rid in _keep) or (base_rid in _keep):
                        # keep visible: remove display:none if present
                        st = _e.get('style') or ''
                        st2 = re.sub(r'(^|;)\s*display\s*:\s*none\s*(?=;|$)', r'\1', st)
                        st2 = re.sub(r';{2,}', ';', st2).strip()
                        if st2.endswith(';'):
                            st2 = st2[:-1]
                        if st2 != st:
                            _e.set('style', st2)
                        continue
                    # default: hide
                    st = _e.get('style') or ''
                    if 'display:' in st:
                        st2 = re.sub(r'(?:(^|;)\s*display\s*:\s*[^;]+)', lambda m: (m.group(1) + 'display:none'), st)
                        _e.set('style', st2)
                    else:
                        if st and not st.strip().endswith(';'):
                            st = st.strip() + ';'
                        _e.set('style', (st or '') + 'display:none')
                except Exception:
                    pass

        # Apply to every instantiated template scope (main + overlays) so anchors behave consistently.
        if _rect_hks:
            try:
                for _j in (inst_jobs or []):
                    _scope = _j.get('node')
                    if _scope is not None:
                        _apply_anchor_visibility(_scope, _j.get('target_index'))
            except Exception:
                pass

        _flatten_card_group(card_group, inst_main)

        # Clear per-row keep-visible set to avoid leaking across rows.
        try:
            _P1_KEEP_SET = None
            RAP._P1_KEEP_SET = _P1_KEEP_SET
        except Exception:
            pass
        # DEBUG: clear per-instance rect tracking
        try: _DBG_FA_RECT_IDS = None
        except Exception: pass
        within = planner.slot_index
        if getattr(planner.current.layout, "sweep_rows_first", False):
            row1 = (within // planner.plan.cols)+1; col1 = (within % planner.plan.cols)+1
        else:
            col1 = (within // planner.plan.rows)+1; row1 = (within % planner.plan.rows)+1
        page1 = planner.page_index+1
        new_name = _set_card_group_identity(
            card_group,
            str(proto_root.get('id') or 'card'),
            int(planner.page_index),
            int(idx),
            "front",
            1,
            item_no=int(card_item_no),
            col_index=int(col1),
            row_slot_index=int(row1),
        )
        try:
            unique_name = root.get_unique_id(new_name)
        except Exception:
            unique_name = new_name
        placed_node.set('id', unique_name)
        placed_node.set(inkex.addNS('label','inkscape'), new_name)
        placed += 1
        if target_within is not None:
            try:
                cur_after = row.get('__dm_target_cursor_after__', None)
                if str(cur_after or '').strip():
                    planner.slot_index = max(0, int(cur_after) - 1)
            except Exception:
                pass
        else:
            planner.commit_slot()
        _profile["post_ms"] += (time.perf_counter() - _profile_phase_t0) * 1000.0
        _profile["row_total_ms"] += (time.perf_counter() - _profile_row_t0) * 1000.0
        _profile["rows"] += 1
        try:
            holes = _coerce_holes(row.get('__dm_holes__', []) or [])
            cur_copy_idx = int(row.get('_i', 0) or 0) + 1
            n_extra = int(row.get('__dm_holes_after__', 0) or 0)
            if n_extra <= 0:
                n_extra = holes.count(cur_copy_idx) if holes else 0
            if n_extra > 0:
                for _ in range(n_extra):
                    planner.commit_slot()
                _l.d(f"[holes] extra slots={n_extra} after copy={cur_copy_idx} row={idx}")
        except Exception:
            pass
    _flush_deferred_paths(warn_tag='[deckmaker.paths]')
    if split_boards_used:
        _l.i("[split_boards] mode active on this dataset pass")
    try:
        for _pid in sorted(list(_marks_pending_by_page.keys())):
            _flush_marks_for_page(int(_pid))
    except Exception:
        pass

    # ---------------------
    
    # ---------------------
    # Back pass (@back) + page-anchored backs (@page @back)
    # ---------------------
    placed_back = 0
    if (back_templates and len(back_templates) > 0) or (pending_page_back_req and len(pending_page_back_req) > 0):
        front_pages = sorted(list(page_states.keys()))
        if front_pages:
            # Interleave pages: 1,1',2,2',... (front/back). This preserves page order for duplex.
            # Duplex alignment: slot positions are mirrored horizontally within each page (column order reversed),
            # but artwork is NOT flipped (we only remap slots).
            page_to_back: Dict[int, int] = {}
            nv = getattr(planner, 'nv', None)
            gap_px = float(getattr(planner, 'page_gap_px', 0.0) or 0.0)

            # Interleave mode: we insert each back page right after its front page.
            # Duplex alignment requires mirroring the slot within page (reverse column order).
            # IMPORTANT: cols/rows may vary by dataset/preset, so we compute them per page
            # from planner.plan (not by inferring from an arbitrary cached local_slots list).
            _l.i("[@back] interleave mode: 1,1',2,2'...; mirror slots within page (duplex)")

            # Create one back page per front page, INSERT it after the corresponding front page
            # both in namedview order and in planner.pages list (so indices stay consistent).
            inserted = 0
            for fp in front_pages:
                try:
                    fp = int(fp)
                    front_idx = fp + inserted
                    if front_idx < 0 or front_idx >= len(pages):
                        continue
                    p_front = pages[front_idx]
                    st = page_states.get(fp)

                    px = float(p_front.get('x', 0.0)); py = float(p_front.get('y', 0.0))
                    pw = float(p_front.get('w', 0.0)); ph = float(p_front.get('h', 0.0))

                    # Place the back page geometrically "below" the front page so existing artwork doesn't shift.
                    bp_x = px
                    bp_y = py + ph + gap_px
                    after_el = p_front.get('el')
                    attrs = _page_attrs_from_resolved(st) if st is not None else None
                    pid = SVG.next_dm_page_id(nv, "dm_page_") if nv is not None else None
                    if pid is None or nv is None:
                        continue

                    if after_el is not None:
                        el = SVG.add_inkscape_page_mm_after(nv, after_el, bp_x, bp_y, pw, ph, pid, attrs)
                    else:
                        el = SVG.add_inkscape_page_mm(nv, bp_x, bp_y, pw, ph, pid, attrs)

                    rec = {"id": pid, "x": bp_x, "y": bp_y, "w": pw, "h": ph, "el": el}

                    pages.insert(front_idx + 1, rec)
                    bp_idx = front_idx + 1
                    page_to_back[fp] = int(bp_idx)
                    inserted += 1

                    try:
                        _l.i(f"[@back] created back page={bp_idx+1} for front={fp+1} (inserted after page={front_idx+1}) id='{pid}'")
                    except Exception:
                        pass
                except Exception as ex:
                    _l.w(f"[@back] failed to create back page for front={fp+1}: {ex}")

            # After inserting, advance cursor to the last page so next dataset doesn't overwrite.
            try:
                planner.page_index = max(0, len(pages) - 1)
                planner.slot_index = 0
            except Exception:
                pass

            try:
                _l.d("[@back] page_to_back", page_to_back)
            except Exception:
                pass

            # Execute back slots in the original slot order; for each slot we remap the slot index within page.
            for rec in (slot_records or []):
                slot_no = int(rec.get('slot_no', 0) or 0)
                fp = int(rec.get('page_index', 0) or 0)
                sp = int(rec.get('slot_in_page', 0) or 0)
                row = rec.get('row') or {}
                bp = page_to_back.get(fp)
                if bp is None:
                    continue

                st = page_states.get(fp)
                try:
                    planner.page_index = int(bp)
                    if st is not None:
                        # Reset then re-apply the exact same preset used on the front page.
                        # We initially set slot_index to the *front* slot-in-page (sp) so that
                        # any preset logic that depends on the cursor is stable.
                        planner.slot_index = int(sp)
                        planner.apply_preset(st)
                    # Duplex alignment: keep the *front* slot index (sp) to compute the exact geometry
                    # (including gaps offsets / stagger), then mirror the X coordinate around the page.
                    # This guarantees that back(i) lands exactly behind front(i) in duplex printing.
                    planner.slot_index = int(sp)
                except Exception:
                    continue

                # Place any pending @page @back requests that target this front slot
                if slot_no in pending_page_back_req:
                    reqs = pending_page_back_req.pop(slot_no, [])
                    for rq in (reqs or []):
                        try:
                            _place_page_template_now(
                                rq.get('te'),
                                rq.get('row') or {},
                                rq.get('ops') or '~5',
                                int(bp),
                                pass_tag='back',
                                insert_after_elem=(out_layer[-1] if len(out_layer) > 0 else None),
                            )
                        except Exception as ex:
                            _l.w(f"[@page @back] placement failed at slot {slot_no}: {ex}")

                # Slot position: compute from the original front slot (sp), then mirror X around the page.
                try:
                    slot_x, slot_y = planner.begin_slot()
                    _, _, slot_w, slot_h = planner.local_slots[int(sp)]
                    ppg = pages[int(bp)]
                    page_x0 = float(ppg.get('x', 0.0))
                    page_w  = float(ppg.get('w', 0.0))
                    # Mirror around the page vertical axis.
                    slot_x = page_x0 + page_w - (float(slot_x) - page_x0) - float(slot_w)
                except Exception:
                    continue
                try:
                    brow0, bcol0 = _slot_index_to_rc(int(sp), planner.plan, planner.current.layout)
                    back_col1 = int(bcol0) + 1
                    back_row1 = int(brow0) + 1
                except Exception:
                    back_col1 = 1
                    back_row1 = 1

                row_map = _build_row_map(headers, row)
                # Place back templates (one or many columns)
                for bt_i, bt in enumerate(back_templates or [], start=1):
                    ctrl_key = (bt or {}).get('control_col') or (bt or {}).get('control_key')
                    ctrl_val = (row_map.get(ctrl_key) or '').strip() if ctrl_key else ''
                    if ctrl_val in ('0', '-'):
                        skipped_back_instances += 1
                        continue

                    tmpl_root = (bt or {}).get('template_root')
                    if tmpl_root is None:
                        continue

                    card_group = inkex.Group()
                    back_item_no = int(next_n)
                    suffix = f"_MD{next_n}"; next_n += 1
                    inst, _target_index_back = _instantiate_template(tmpl_root, suffix, "back")

                    use_jobs = []
                    fa_jobs = []
                    path_jobs = []
                    transform_jobs = []
                    _fill_instance_fields(inst, row, row_map, use_jobs, fa_jobs, path_jobs, transform_jobs, clone_first=False)

                    _fa_remove_later = _exec_use_fa_paths(inst, use_jobs, fa_jobs, path_jobs, transform_jobs, warn_tag='[@back]')
                    for _ph in list(dict.fromkeys(_fa_remove_later)):
                        try:
                            par = _ph.getparent()
                            if par is not None:
                                if not _is_rect_elem(_ph):

                                    par.remove(_ph)
                        except Exception:
                            pass

                    card_group.append(inst)
                    _append_output(card_group, page_index=int(planner.page_index))

                    # Fit to slot rect using the declared bbox of that back template (same as front).
                    bbid = (bt or {}).get('bbox_id') or ''
                    try:
                        rid = SVG.resolve_local_id(inst, bbid) if bbid else None
                        bbox_elem = inst.find(f".//*[@id='{rid}']") if rid else None
                        if bbox_elem is None:
                            an = SVG.pick_anchor_in(inst)
                            bb = an.bounding_box(); bx, by, bw, bh = float(bb.left), float(bb.top), float(bb.width), float(bb.height)
                        else:
                            bb = bbox_elem.bounding_box(); bx, by, bw, bh = float(bb.left), float(bb.top), float(bb.width), float(bb.height)
                        T_fit = SVG.transform_bbox_to_rect(
                            bx=bx, by=by, bw=bw, bh=bh,
                            dst_x=slot_x, dst_y=slot_y, dst_w=slot_w, dst_h=slot_h,
                            fit='a', anchor=(0.0, 0.0), shift=(0.0, 0.0),
                            rot_deg=0.0, mir_h=False, mir_v=False
                        )
                        curT = inkex.Transform(card_group.get('transform') or "")
                    except Exception:
                        T_fit = inkex.Transform(); curT = inkex.Transform()

                    # Note: we do NOT mirror artwork; we only mirrored the slot selection (sp_m).
                    _flatten_card_group(card_group, inst)
                    back_name = _set_card_group_identity(
                        card_group,
                        str(tmpl_root.get('id') or 'card'),
                        int(planner.page_index),
                        int(idx),
                        "back",
                        int(bt_i),
                        item_no=int(back_item_no),
                        col_index=int(back_col1),
                        row_slot_index=int(back_row1),
                    )
                    try:
                        back_unique_name = root.get_unique_id(back_name)
                    except Exception:
                        back_unique_name = back_name
                    card_group.set('id', back_unique_name)
                    card_group.set(inkex.addNS('label', 'inkscape'), back_name)
                    card_group.set('transform', str(T_fit @ curT))
                    geom_registry.add_group(int(planner.page_index), card_group)
                    placed_back += 1
# Any remaining pending @page requests that referenced slots beyond the placed range are ignored.
    _flush_deferred_paths(warn_tag='[@back]')
    if pending_page_req:
        _l.w(f"[@page] ignored {sum(len(v) for v in pending_page_req.values())} pending requests: slot ref out of range")
        pending_page_req.clear()
    if pending_page_back_req:
        _l.w(f"[@page @back] ignored {sum(len(v) for v in pending_page_back_req.values())} pending requests: slot ref out of range")
        pending_page_back_req.clear()

    try:
        tmp_group = getattr(ctx, '_pnpink_tmp_group', None)
        if tmp_group is not None and tmp_group.getparent() is not None:
            tmp_group.getparent().remove(tmp_group)
            ctx._pnpink_tmp_group = None
    except Exception:
        pass

    if placed_back > 0:
        try:
            n_back_pages = len([p for p in planner.pages if (p.get('id') or '').startswith('dm_page_')])
        except Exception:
            n_back_pages = 0
        _l.i(f"[@back] placed={placed_back} instances; skipped={skipped_back_instances}; back_pages_created={n_back_pages}")
    try:
        _profile_rows = int(_profile.get("rows") or 0)
        if _profile_rows > 0:
            _profile_dataset_ms = (time.perf_counter() - _profile_dataset_t0) * 1000.0
            _profile_avg = float(_profile.get("row_total_ms") or 0.0) / float(_profile_rows)
            _l.i(
                f"[render.profile] dataset={ds_idx} rows={_profile_rows} placed={placed} "
                f"dataset_ms={_profile_dataset_ms:.1f} row_sum_ms={float(_profile.get('row_total_ms') or 0.0):.1f} "
                f"avg_row_ms={_profile_avg:.2f} instances_ms={float(_profile.get('instances_ms') or 0.0):.1f}"
            )
            _l.i(
                f"[render.profile] dataset={ds_idx} avg_ms "
                f"pre={float(_profile.get('pre_ms') or 0.0) / _profile_rows:.2f} "
                f"clone={float(_profile.get('clone_ms') or 0.0) / _profile_rows:.2f} "
                f"fields={float(_profile.get('fields_ms') or 0.0) / _profile_rows:.2f} "
                f"bbox={float(_profile.get('bbox_ms') or 0.0) / _profile_rows:.2f} "
                f"fit={float(_profile.get('fit_ms') or 0.0) / _profile_rows:.2f} "
                f"fa={float(_profile.get('fa_ms') or 0.0) / _profile_rows:.2f} "
                f"post={float(_profile.get('post_ms') or 0.0) / _profile_rows:.2f}"
            )
            _l.i(
                f"[render.profile.clone] dataset={ds_idx} avg_ms "
                f"deepcopy={float(_profile.get('clone_deepcopy_ms') or 0.0) / _profile_rows:.2f} "
                f"flatten={float(_profile.get('clone_flatten_ms') or 0.0) / _profile_rows:.2f} "
                f"absolutize={float(_profile.get('clone_absolutize_ms') or 0.0) / _profile_rows:.2f} "
                f"uniquify={float(_profile.get('clone_uniquify_ms') or 0.0) / _profile_rows:.2f} "
                f"index={float(_profile.get('clone_index_ms') or 0.0) / _profile_rows:.2f}"
            )
            _l.i(
                f"[render.profile.fields] dataset={ds_idx} "
                f"fast_count={int(_profile.get('fields_fast_count') or 0)} "
                f"fast_plain_count={int(_profile.get('fields_fast_plain_count') or 0)} "
                f"generic_count={int(_profile.get('fields_generic_count') or 0)} "
                f"avg_fast_ms={float(_profile.get('fields_fast_ms') or 0.0) / max(1, int(_profile.get('fields_fast_count') or 0)):.4f} "
                f"avg_generic_ms={float(_profile.get('fields_generic_ms') or 0.0) / max(1, int(_profile.get('fields_generic_count') or 0)):.4f}"
            )
            _l.i(
                f"[render.profile.fit] dataset={ds_idx} avg_ms "
                f"slot={float(_profile.get('fit_slot_ms') or 0.0) / _profile_rows:.2f} "
                f"append={float(_profile.get('fit_append_ms') or 0.0) / _profile_rows:.2f} "
                f"pagegroup={float(_profile.get('fit_pagegroup_ms') or 0.0) / _profile_rows:.2f} "
                f"node_append={float(_profile.get('fit_node_append_ms') or 0.0) / _profile_rows:.2f} "
                f"transform={float(_profile.get('fit_transform_ms') or 0.0) / _profile_rows:.2f} "
                f"geom={float(_profile.get('fit_geom_ms') or 0.0) / _profile_rows:.2f} "
                f"marks={float(_profile.get('fit_marks_ms') or 0.0) / _profile_rows:.2f}"
            )
    except Exception as ex:
        _l.w(f"[render.profile] failed: {ex}")
    _l.i(f"[datasets] #{ds_idx}: placed={placed} cards; end_page={planner.page_index+1}")
    placed_total += placed
    start_page_index = planner.page_index + 1
    ctx.next_n = next_n
    ctx.placed_total = placed_total
    ctx.start_page_index = start_page_index
