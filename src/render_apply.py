# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import re
from typing import Dict, Optional, Tuple

import inkex

import log as LOG
import svg as SVG
import layouts as LYT
import dsl as DSL
import fit_anchor as FA
import render_helpers as RHP
import render_tokens as RTK

_l = LOG
_P1_KEEP_SET = None
TEXT_LIKE = set(getattr(SVG, "TEXT_LIKE", ()))

_parse_object_token = RTK.parse_object_token
_parse_sprite_alias_token = RTK.parse_sprite_alias_token
_split_paths_suffix = RTK.split_paths_suffix
_split_transform_suffixes = RTK.split_transform_suffixes
_fit_suffix_to_ops = RTK.fit_suffix_to_ops
_merge_fit_ops = RTK.merge_fit_ops
_normalize_ops_chain = RTK.normalize_ops_chain
_parse_source_token_with_selector = RTK.parse_source_token_with_selector
_virtual_warn_tag = RTK.virtual_warn_tag
_resolve_virtual_source_urls = RTK.resolve_virtual_source_urls

_is_rect_elem = RHP.is_rect_elem
_ensure_wrap_symbol_for_src = RHP.ensure_wrap_symbol_for_src
_make_use_for_wrap = RHP.make_use_for_wrap

def _parse_array_token(token: str):
    s = (token or '').strip()
    if not s.startswith('['):
        return None
    # find matching closing bracket for the leading group
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
        raise ValueError(f"Unclosed array group: '{token}'")
    body = s[1:end].strip()
    tail = s[end+1:].strip()

    items = []

    def _append_array_item(raw_item: str):
        tt2 = (raw_item or '').strip()
        if not tt2:
            return
        if tt2 == '-' or re.fullmatch(r"-+", tt2):
            items.append(None)
            return
        m_gap = re.match(r"^(\d+)-$", tt2)
        if m_gap:
            items.extend([None] * int(m_gap.group(1)))
            return
        try:
            base_txt, _place, ops_txt = _parse_object_token(tt2)
            base_txt = (base_txt or "").strip()
            ops_txt = (ops_txt or "").strip()
            if not base_txt:
                return
            items.append({"id": base_txt, "ops": ops_txt})
            return
        except Exception:
            pass
        base_txt, _sep, ops_txt = tt2.partition("~")
        base_txt = base_txt.strip()
        ops_txt = ops_txt.strip()
        if not base_txt:
            return
        items.append({"id": base_txt, "ops": ops_txt})

    def _expand_array_body_expr(expr: str):
        out_exprs = []
        tt = (expr or '').strip()
        if not tt:
            return out_exprs
        # Group repetition: K*(...)
        m_rep_grp = re.match(r"^(\d+)\*\((.*)\)$", tt)
        if m_rep_grp:
            k = int(m_rep_grp.group(1))
            inner = (m_rep_grp.group(2) or '').strip()
            inner_toks = _split_multivalue(inner) if inner else []
            expanded_inner = []
            for it in inner_toks:
                expanded_inner.extend(_expand_array_body_expr(it))
            for _ in range(max(0, k)):
                out_exprs.extend(expanded_inner)
            return out_exprs
        # Single grouped block: (...)
        if tt.startswith("(") and tt.endswith(")"):
            inner = tt[1:-1].strip()
            inner_toks = _split_multivalue(inner) if inner else []
            for it in inner_toks:
                out_exprs.extend(_expand_array_body_expr(it))
            return out_exprs
        # Repetition shorthand inside arrays: K*X
        # Example: [5*:Ic(potato)] -> five items ":Ic(potato)".
        m_rep = re.match(r"^(\d+)\*(.+)$", tt)
        if m_rep:
            k = int(m_rep.group(1))
            rhs = (m_rep.group(2) or '').strip()
            rhs_expanded = _expand_array_body_expr(rhs)
            for _ in range(max(0, k)):
                out_exprs.extend(rhs_expanded)
            return out_exprs
        out_exprs.append(tt)
        return out_exprs

    for t in _split_multivalue(body):
        for expr in _expand_array_body_expr(t):
            _append_array_item(expr)

    layout_spec = None
    m = re.search(r"\.(?:Layout|L)\s*(\{.*\})", tail)
    if m:
        try:
            layout_spec = DSL.parse_layout_block('L' + m.group(1))
        except Exception as ex:
            _l.w(f"[array] invalid layout in '{token}': {ex}")
            layout_spec = None
        tail = (tail[:m.start()] + tail[m.end():]).strip()

    ops = ''
    if '~' in tail:
        pre, _sep, post = tail.partition('~')
        if pre.strip():
            _l.w(f"[array] unexpected tail '{pre.strip()}' in '{token}'")
        ops = DSL.normalize_ops_suffix(post)
    elif tail:
        _l.w(f"[array] unexpected tail '{tail}' in '{token}'")

    return {'items': items, 'layout': layout_spec, 'ops': ops}


def _resolve_array_item(root_doc, inst_node, item_id: str, sm=None, ss_registry=None):
    s = (item_id or '').strip()
    if not s:
        return None
    s, _normalized, _symbol_id = _normalize_source_token(s, sm=sm, ss_registry=ss_registry)
    if s.startswith('['):
        try:
            arr = _parse_array_token(s)
            items = (arr or {}).get('items') or []
            first = next((it for it in items if isinstance(it, dict) and (it.get('id') or '').strip()), None)
            if first is not None:
                s = (first.get('id') or '').strip()
        except Exception:
            pass
    try:
        s, _place, _ops = _parse_object_token(s)
    except Exception:
        pass
    base = SVG.find_target_exact_in(inst_node, s)
    if base is None:
        base = SVG.find_target_exact_in(root_doc, s)
    return base


def _build_array_group(inst_node, root_doc, items, layout_spec, *, sm=None, ss_registry=None, group_id_prefix='dm_array'):
    if not items:
        return None, None
    g = inkex.Group()
    try:
        gid = root_doc.get_unique_id(group_id_prefix)
    except Exception:
        gid = f"{group_id_prefix}_{id(g)}"
    g.set('id', gid)
    inst_node.append(g)

    resolved = []
    ref_w = ref_h = 0.0
    for it in items:
        if it is None:
            resolved.append(None)
            continue
        item_id = (it.get("id") if isinstance(it, dict) else None) or ""
        item_ops = (it.get("ops") if isinstance(it, dict) else "") or ""
        base = _resolve_array_item(root_doc, inst_node, item_id, sm=sm, ss_registry=ss_registry)
        if base is None:
            _l.w(f"[array] target not found: '{item_id}'")
            resolved.append(None)
            continue
        try:
            bx, by, bw, bh = SVG.visual_bbox(base)
        except Exception:
            bx = by = 0.0; bw = bh = 0.0
        resolved.append((base, bx, by, bw, bh, item_ops))
        if ref_w <= 0.0 and bw > 0.0:
            ref_w = float(bw)
            ref_h = float(bh)

    if ref_w <= 0.0 or ref_h <= 0.0:
        ref_w = ref_h = 1.0

    layout_obj = LYT.LayoutSpec()
    gaps_obj = LYT.GapsMM()
    page_obj = LYT.PageSpec()
    card_obj = LYT.CardSpec()
    try:
        page_obj, card_obj, layout_obj, gaps_obj = LYT.apply_layout_spec(
            (page_obj, card_obj, layout_obj, gaps_obj), layout_spec
        )
    except Exception:
        pass

    cols = int(getattr(layout_obj, "cols", 0) or 0)
    rows = int(getattr(layout_obj, "rows", 0) or 0)
    if cols <= 0 and rows <= 0:
        cols = max(1, len(items))
        rows = 1
    elif cols <= 0:
        cols = int((len(items) + rows - 1) // rows)
    elif rows <= 0:
        rows = int((len(items) + cols - 1) // cols)
    layout_obj.cols = cols
    layout_obj.rows = rows

    try:
        px_per_mm = float(root_doc.unittouu("1mm"))
    except Exception:
        px_per_mm = 1.0
    gaps_px6 = None
    gaps_px = (0.0, 0.0)
    if getattr(layout_obj, "gaps", None):
        gx, gy, w1, h1, w2, h2 = LYT.gaps6_to_px(LYT.layout_gaps_tokens(layout_obj), ref_w, ref_h, px_per_mm)
        gaps_px = (gx, gy)
        gaps_px6 = (gx, gy, w1, h1, w2, h2)

    gh0 = 0.0 if (isinstance(gaps_px[0], float) and math.isnan(gaps_px[0])) else float(gaps_px[0])
    gv0 = 0.0 if (isinstance(gaps_px[1], float) and math.isnan(gaps_px[1])) else float(gaps_px[1])
    cw = (float(cols) * ref_w) + (float(max(0, cols - 1)) * gh0)
    ch = (float(rows) * ref_h) + (float(max(0, rows - 1)) * gv0)
    plan = LYT.plan_grid(
        cw, ch, ref_w, ref_h,
        gaps_px=gaps_px,
        gaps_px6=gaps_px6,
        layout=layout_obj,
        content_origin_px=(0.0, 0.0),
        content_wh_px=(cw, ch),
    )
    slots = list(getattr(plan, "slots", []) or [])
    # Provide an explicit bbox for the array group so fit_anchor can scale reliably.
    if slots:
        minx = min(s[0] for s in slots)
        miny = min(s[1] for s in slots)
        maxx = max(s[0] + s[2] for s in slots)
        maxy = max(s[1] + s[3] for s in slots)
        bb_w = maxx - minx
        bb_h = maxy - miny
    else:
        minx = miny = 0.0
        bb_w = cw
        bb_h = ch
    try:
        g.set('data-bbox', f"{minx} {miny} {bb_w} {bb_h}")
    except Exception:
        pass

    for idx, entry in enumerate(resolved):
        if entry is None:
            continue
        if idx >= len(slots):
            break
        base, _bx, _by, _bw, _bh, item_ops = entry
        sx, sy, sw, sh = slots[idx]
        try:
            rect = SVG.etree.Element(inkex.addNS('rect', 'svg'))
            rect.set('x', f"{sx}"); rect.set('y', f"{sy}")
            rect.set('width', f"{sw}"); rect.set('height', f"{sh}")
            ops_body = (item_ops or "").strip()
            ops_full = f"~{ops_body}" if ops_body else "~i"
            FA.apply_to_by_ids(
                root_doc,
                base.get('id') or '',
                rect_id="",
                ops_full=ops_full,
                place="clone",
                rect_elem=rect,
                parent_elem=g,
            )
        except Exception as ex:
            _l.w(f"[array] failed to place '{(base.get('id') if base is not None else '')}': {ex}")

    return g, gid

def _split_multivalue(s: str) -> list:
    """Split a cell into whitespace-separated tokens, without breaking inside {...}, (...), [...], or quotes."""
    if not s:
        return []
    out = []
    cur = []
    depth_brace = depth_paren = depth_brack = 0
    quote = None
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch == '{':
            depth_brace += 1
        elif ch == '}':
            depth_brace = max(0, depth_brace - 1)
        elif ch == '(':
            depth_paren += 1
        elif ch == ')':
            depth_paren = max(0, depth_paren - 1)
        elif ch == '[':
            depth_brack += 1
        elif ch == ']':
            depth_brack = max(0, depth_brack - 1)

        if ch.isspace() and depth_brace == 0 and depth_paren == 0 and depth_brack == 0:
            tok = "".join(cur).strip()
            if tok:
                out.append(tok)
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    tok = "".join(cur).strip()
    if tok:
        out.append(tok)
    return out

def expand_value(raw: Optional[str], row: Dict[str, str]) -> str:
    s = "" if raw is None else str(raw)
    s = s.replace("\\n","\n").replace("\\t","\t")
    s = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: str(row.get(m.group(1), "")), s)
    return s


def _orient_hint(layout_obj):
    return getattr(layout_obj, 'smart_hex_orient', None) if layout_obj is not None else None


def _queue_paths_for_target(tgt, raw_paths_spec: str, path_jobs, layout_obj) -> int:
    if not raw_paths_spec:
        return 0
    try:
        tgt.set('data-dm-keep-paths', '1')
    except Exception:
        pass
    path_jobs.append((tgt, raw_paths_spec, _orient_hint(layout_obj)))
    return 1


def _has_fa_signature(raw_token: str, *, compact_ops_present: bool) -> bool:
    s = (raw_token or "").strip()
    return bool(
        compact_ops_present
        or ("~" in s)
        or s.endswith("=")
        or s.endswith("+")
        or ("=~" in s)
        or ("+~" in s)
        or s.lstrip().startswith('[')
    )


def _append_inserted_use(parent, tgt, u, transform_spec, use_jobs, raw_paths_spec, path_jobs, layout_obj) -> bool:
    if parent is None:
        return False
    parent.insert(parent.index(tgt) + 1, u)
    use_jobs.append((tgt, u, transform_spec))
    _queue_paths_for_target(tgt, raw_paths_spec, path_jobs, layout_obj)
    return True


def _insert_symbol_use(symbol_id: str, *, tgt, target_id: str, use_seq, transform_spec, use_jobs, raw_paths_spec, path_jobs, layout_obj):
    use_seq[0] += 1
    use_id = f"dm_srcuse_{use_seq[0]}"
    parent = tgt.getparent()
    if parent is None:
        _l.w(f"Target '{target_id}' has no parent; cannot insert source <use> '{use_id}'.")
        return 0, "miss"
    u = SVG.etree.Element(inkex.addNS('use', 'svg'))
    u.set(inkex.addNS('href', 'xlink'), f"#{symbol_id}")
    _append_inserted_use(parent, tgt, u, transform_spec, use_jobs, raw_paths_spec, path_jobs, layout_obj)
    return use_id, u


def _insert_wrapped_source_use(root_doc, src_id: str, *, tgt, target_id: str, use_seq, transform_spec, use_jobs, raw_paths_spec, path_jobs, layout_obj):
    src = root_doc.find(".//*[@id='%s']" % src_id)
    if src is None:
        _l.w(f"Clone source '{src_id}' not found for non-text target '{target_id}'.")
        return 0, "miss"
    wrap_id, bw, bh = _ensure_wrap_symbol_for_src(root_doc, src)
    if bw <= 0 or bh <= 0:
        _l.w(f"source '{src_id}' invalid bbox (w={bw} h={bh}); skip.")
        return 0, "miss"
    use_seq[0] += 1
    use_id = f"dm_use_{src_id}_{use_seq[0]}"
    u = _make_use_for_wrap(wrap_id, bw, bh, use_id=use_id)
    parent = tgt.getparent()
    if parent is None:
        _l.w(f"Target '{target_id}' has no parent; cannot insert <use> '{use_id}'.")
        return 0, "miss"
    _append_inserted_use(parent, tgt, u, transform_spec, use_jobs, raw_paths_spec, path_jobs, layout_obj)
    return use_id, wrap_id, bw, bh


def _normalize_source_token(raw_token: str, sm=None, ss_registry=None):
    token = (raw_token or "").strip()
    normalized = False
    symbol_id = None

    if token.startswith('@') and (not token.startswith('@{')) and sm is not None and ss_registry is not None:
        parsed = _parse_sprite_alias_token(token)
        if parsed:
            a_name, dims, ops_tail = parsed
            if a_name in ss_registry:
                frame = None
                page = 1
                col = None
                row_i = None
                _l.i(f"[spritesheet] token seen in render: '{token}'")
                try:
                    if len(dims) == 1:
                        frame = next((x for x in dims[0] if isinstance(x, int)), None)
                        if frame is None:
                            frame = 1
                        if len([x for x in dims[0] if isinstance(x, int)]) > 1:
                            _l.w(f"[spritesheets] token '{token}': multiple frame indices not supported yet; using first={frame}")
                    elif len(dims) == 2:
                        col = next((x for x in dims[0] if isinstance(x, int)), None)
                        row_i = next((x for x in dims[1] if isinstance(x, int)), None)
                    elif len(dims) == 3:
                        page = next((x for x in dims[0] if isinstance(x, int)), None)
                        col = next((x for x in dims[1] if isinstance(x, int)), None)
                        row_i = next((x for x in dims[2] if isinstance(x, int)), None)
                    else:
                        _l.w(f"[spritesheets] token '{token}': invalid selector dims={len(dims)}")
                except Exception as ex:
                    _l.w(f"[spritesheets] token '{token}': parse error: {ex}")

                _l.i(f"[spritesheet] parsed indices: page={page} col={col} row={row_i} idx={frame}")
                if frame is not None or (col is not None and row_i is not None):
                    try:
                        _l.i(f"[spritesheet] frame symbol requested id=sp_{a_name}_? selector p={page} c={col} r={row_i} idx={frame}")
                        sref = sm.register_spritesheet_frame(a_name, frame=frame, page=page, col=col, row=row_i)
                        if sref is not None:
                            _l.i(f"[spritesheet] frame symbol created id={sref.symbol_id}")
                            symbol_id = sref.symbol_id
                            token = f"{sref.symbol_id}" + (f"~{ops_tail}" if ops_tail else "")
                            normalized = True
                            _l.d(f"[spritesheets] normalized '@{a_name}[...]' ? '{token}'")
                    except Exception as ex:
                        _l.w(f"[spritesheets] frame resolve failed '{token}': {ex}")
            else:
                _l.d(f"[spritesheets] token '{token}': alias @{a_name} not registered; ignoring")

    if sm is not None:
        src_val, sel_src, ops_from_token, src_tag = _parse_source_token_with_selector(token)
        if src_val:
            try:
                v_urls = _resolve_virtual_source_urls(sm, src_val, sel_src, warn_tag=_virtual_warn_tag(src_val, "wkmc"))
                if v_urls is not None:
                    if not v_urls:
                        _l.w(f"[deckmaker.src] virtual source '{src_val}' produced no selected urls")
                    else:
                        ids = []
                        for _u in v_urls:
                            _src0, _sel0, _ops0, _tag0 = _parse_source_token_with_selector(_u)
                            if not _src0:
                                continue
                            sref0 = sm.register(_src0)
                            ids.append(sref0.symbol_id)
                        if len(ids) == 1:
                            symbol_id = ids[0]
                            token = f"{ids[0]}{ops_from_token}"
                        else:
                            token = "[" + " ".join(ids) + "]" + (ops_from_token or "")
                        normalized = True
                        _l.d(f"[deckmaker.src] normalized virtual '{src_tag}' ? '{token}'")
                else:
                    sref = sm.register(src_val)
                    symbol_id = sref.symbol_id
                    token = f"{sref.symbol_id}{ops_from_token}"
                    normalized = True
                    _l.d(f"[deckmaker.src] normalized '{src_tag}' ? '{token}' (symbol in <defs>)")
            except Exception as ex:
                _l.w(f"[deckmaker.src] normalize failed '{token}': {ex}")

    if (not normalized) and sm is not None:
        low = token.lower().lstrip()
        if low.startswith("@icon://") or low.startswith("icon://"):
            main, sep, ops_tail = token.partition("~")
            src_val = main.strip()
            if src_val.lower().startswith("@icon://"):
                src_val = src_val[1:]
            try:
                sref = sm.register(src_val)
                token = f"{sref.symbol_id}{sep}{ops_tail}" if sep else sref.symbol_id
                symbol_id = sref.symbol_id
                normalized = True
                _l.d(f"[deckmaker.src] normalized 'icon://' → '{token}' (symbol in <defs>)")
            except Exception as ex:
                _l.w(f"[deckmaker.src] icon:// normalize failed '{token}': {ex}")

    return token, normalized, symbol_id


def _parse_header_default_spec(spec: str, target_id: str) -> Tuple[Optional[str], str, str, str]:
    """Parse the RHS of a header default declaration.

    Examples (spec):
      'id1'                -> (default_id='id1', default_ops='',    global_ops='', default_expr='')
      'id1~m7^'             -> (default_id='id1', default_ops='~m7^', global_ops='', default_expr='')
      'id1.Fit{m7^}'        -> (default_id='id1', default_ops='~m7^', global_ops='', default_expr='')
      '~[-50%]'             -> (default_id=None,  default_ops='',    global_ops='~[-50%]', default_expr='')
      '.Fit{m7^15}'         -> (default_id=None,  default_ops='',    global_ops='~m7^15', default_expr='')
      '@{...}~i5'           -> (default_id=None,  default_ops='',    global_ops='', default_expr='@{...}~i5')
      'ph_id'               -> (default_id='ph_id', default_ops='',  global_ops='', default_expr='')
    """
    s = (spec or "").strip()
    if not s:
        return None, "", "", ""
    # Global-fit-only forms.
    if s.startswith("~"):
        return None, "", s, ""
    if s.startswith(".Fit"):
        return None, "", (_fit_suffix_to_ops(s) or ""), ""
    # Default-id forms, optionally with ops.
    default_id = None
    default_ops = ""
    global_ops = ""
    default_expr = ""
    # Split on first '~' or '.Fit{...}'
    m = re.match(r"^(?P<id>[A-Za-z_][-A-Za-z0-9_:.]*\*?)(?P<rest>.*)$", s)
    if not m:
        # Not an id-form default => keep as full default expression.
        return None, "", "", s
    default_id = m.group("id")
    rest = (m.group("rest") or "").strip()
    if rest:
        if rest.startswith("~"):
            default_ops = rest
        elif rest.startswith("^") or rest.startswith("!") or rest.startswith("|"):
            default_ops = "~" + rest
        elif rest.startswith(".Fit"):
            default_ops = _fit_suffix_to_ops(rest)
        else:
            # Not a valid id+ops tail (e.g. 'url(#g1)'): treat full RHS as literal expression.
            return None, "", "", s
    return default_id, (default_ops or ""), (global_ops or ""), (default_expr or "")

def _is_id_wildcard_token(token: str) -> bool:
    t = (token or "").strip()
    return bool(re.match(r"^[A-Za-z_][-A-Za-z0-9_:.]*\*$", t))


def _expand_id_wildcard_in_scope(scope, token: str) -> list:
    """Expand a wildcard token like 'main_icon-*' against ids in `scope`.

    Matches by prefix over `strip_pnp_suffix(@id)`, preserving document order.
    """
    t = (token or "").strip()
    if not t:
        return []
    if not _is_id_wildcard_token(t):
        return [t]
    pref = t[:-1]
    out = []
    seen = set()
    if scope is None:
        return out
    try:
        for el in scope.iter():
            cid = (el.get("id") or "").strip()
            if not cid:
                continue
            bid = (SVG.strip_pnp_suffix(cid) or cid).strip()
            if (not bid) or (not bid.startswith(pref)) or (bid in seen):
                continue
            seen.add(bid)
            out.append(bid)
    except Exception:
        return []
    return out


def _resolve_header_target_ids(scope, header_targets) -> list:
    out = []
    seen = set()
    toks = list(header_targets or [])
    for tok in toks:
        t = (tok or "").strip()
        if not t:
            continue
        expanded = _expand_id_wildcard_in_scope(scope, t) if _is_id_wildcard_token(t) else [t]
        for x in expanded:
            xx = (x or "").strip()
            if not xx or xx in seen:
                continue
            seen.add(xx)
            out.append(xx)
    return out


def _split_leading_bracket_group(s: str):
    txt = (s or "").strip()
    if not txt.startswith('['):
        return None, None
    depth = 0
    end = -1
    for i, ch in enumerate(txt):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None, None
    return txt[:end + 1], txt[end + 1:].strip()


def _expand_wildcard_object_token(tok: str, id_scope) -> list:
    t = (tok or "").strip()
    if not t:
        return []
    try:
        base_id, place, ops_tok = _parse_object_token(t)
    except Exception:
        return [t]
    if not _is_id_wildcard_token(base_id):
        return [t]
    ids = _expand_id_wildcard_in_scope(id_scope, base_id)
    mod = "" if place == "clone" else ("=" if place == "copy" else "+")
    ops = f"~{ops_tok}" if (ops_tok or "").strip() else ""
    return [f"{i}{mod}{ops}" for i in ids]


def _expand_wildcard_ids_in_value(raw_value: str, id_scope) -> str:
    """Expand id wildcards in non-text value tokens.

    - `id-*` expands to whitespace-separated multivalue tokens.
    - `[id-*]` expands inside list bodies preserving optional trailing tail.
    """
    s = (raw_value or "").strip()
    if not s or ("*" not in s):
        return s
    toks = _split_multivalue(s) if any(ch.isspace() for ch in s) else [s]
    out = []
    for tok in toks:
        t = (tok or "").strip()
        if not t:
            continue
        bcore, btail = _split_leading_bracket_group(t)
        if bcore is not None:
            body = bcore[1:-1].strip()
            body_toks = _split_multivalue(body) if body else []
            body_out = []
            for bt in body_toks:
                body_out.extend(_expand_wildcard_object_token(bt, id_scope))
            if body_out:
                arr = "[" + " ".join(body_out) + "]"
                if btail:
                    arr += btail
                out.append(arr)
            continue
        out.extend(_expand_wildcard_object_token(t, id_scope))
    return " ".join([x for x in out if x]).strip()


def _build_single_target_header_key(hk: Dict[str, object], target_id: str) -> str:
    left = (target_id or "").strip()
    prop = (hk.get("prop") or "text").strip().lower()
    if prop and prop != "text":
        left = f"{left}[{prop}]"
    if bool(hk.get("header_plus") or False):
        left = left + "+"
    default_raw = (hk.get("default_raw") or "").strip()
    if default_raw:
        return f"{left}={default_raw}"
    return left


def parse_header_key_full(key: str) -> Dict[str, object]:
    """Parse a dataset header key including modifiers.

    Supported modifiers:
      - '+' suffix on id: keep rect anchor visible (Phase-1 behavior).
      - '=' default declaration: 'ph_id=...' (defaults and/or global fit).

    Returns dict:
      {target_id, target_ids, prop, header_plus, default_id, default_ops, global_ops, default_expr, default_raw}
    """
    raw = (key or "").strip()
    if not raw:
        return {'target_id': '', 'target_ids': [], 'prop': 'text', 'header_plus': False,
                'default_id': None, 'default_ops': '', 'global_ops': '', 'default_expr': '', 'default_raw': ''}

    # Split default declaration first so '=~[12x12]' is never confused with header '[prop]'.
    left, has_eq, right = raw.partition("=")
    left = (left or "").strip()
    right = (right or "").strip()

    # Optional property suffix only on LEFT side.
    prop = "text"
    m_prop = re.match(r"^(?P<id>.+?)\[(?P<prop>[A-Za-z_][A-Za-z0-9_-]*)\]\s*$", left)
    if m_prop:
        p = (m_prop.group("prop") or "").strip().lower()
        if p:
            prop = p
            left = (m_prop.group("id") or "").strip()

    header_plus = False
    if left.endswith("+"):
        header_plus = True
        left = left[:-1].strip()

    target_ids = _split_multivalue(left) if any(ch.isspace() for ch in left) else [left]
    target_ids = [(x or "").strip() for x in (target_ids or []) if (x or "").strip()]
    target_id = target_ids[0] if target_ids else ""

    # Phase-1 keep-visible set:
    # - '+' keeps explicit anchors visible.
    # - property columns (id[fill], id[stroke], etc.) are also explicit visual
    #   edits; an empty cell means "leave template value", not "hide target".
    global _P1_KEEP_SET
    if (header_plus or prop != "text") and target_ids:
        try:
            if isinstance(_P1_KEEP_SET, set):
                for _tid in target_ids:
                    if not _is_id_wildcard_token(_tid):
                        _P1_KEEP_SET.add(_tid)
        except Exception:
            pass

    default_id = None
    default_ops = ""
    global_ops = ""
    default_expr = ""
    if has_eq:
        default_id, default_ops, global_ops, default_expr = _parse_header_default_spec(right, target_id)

    return {'target_id': target_id, 'target_ids': target_ids, 'prop': prop, 'header_plus': header_plus,
            'default_id': default_id, 'default_ops': (default_ops or ''), 'global_ops': (global_ops or ''),
            'default_expr': (default_expr or ''), 'default_raw': (right or '')}

def parse_header_key(key: str) -> Tuple[str, str, bool]:
    info = parse_header_key_full(key)
    return str(info.get('target_id') or ''), str(info.get('prop') or 'text'), bool(info.get('header_plus') or False)


def apply_field_in_clone(inst, key, raw_val, row, *, root_doc, use_jobs, fa_jobs, path_jobs, use_seq, layout_obj=None, sm=None, ss_registry=None, transform_jobs=None):
    global _P1_KEEP_SET
    hk = parse_header_key_full(key)
    target_tokens = hk.get('target_ids') if isinstance(hk, dict) else None
    if not target_tokens:
        target_tokens = [hk.get('target_id') or '']
    target_ids = _resolve_header_target_ids(inst, target_tokens)
    if not target_ids:
        target_ids = [hk.get('target_id') or '']
    # Multiheader / wildcard headers: apply the same value to each resolved target.
    # IMPORTANT: every placed element is inserted relative to its placeholder, so
    # processing targets left->right would invert the visual stacking order.
    # Iterate in reverse so the rightmost/rearmost declared header ends up on top.
    if len(target_ids) > 1:
        total = 0
        status = "miss"
        for _tid in reversed(target_ids):
            sub_key = _build_single_target_header_key(hk, _tid)
            c, st = apply_field_in_clone(
                inst, sub_key, raw_val, row,
                root_doc=root_doc, use_jobs=use_jobs, fa_jobs=fa_jobs, path_jobs=path_jobs, use_seq=use_seq, layout_obj=layout_obj,
                transform_jobs=transform_jobs,
                sm=sm, ss_registry=ss_registry
            )
            total += int(c or 0)
            if st != "miss":
                status = "multiheader"
        return total, status

    target_id = target_ids[0] if target_ids else (hk.get('target_id') or '')
    prop = hk.get('prop') or 'text'
    header_plus = bool(hk.get('header_plus') or False)
    try:
        _default_id = hk.get('default_id') if isinstance(hk, dict) else None
        _default_ops = (hk.get('default_ops') or '') if isinstance(hk, dict) else ''
        _global_ops = (hk.get('global_ops') or '') if isinstance(hk, dict) else ''
        _default_expr = (hk.get('default_expr') or '') if isinstance(hk, dict) else ''
        _default_raw = (hk.get('default_raw') or '') if isinstance(hk, dict) else ''
    except Exception:
        _default_id = None
        _default_ops = ''
        _global_ops = ''
        _default_expr = ''
        _default_raw = ''
    value = expand_value(raw_val, row)
    def _normalize_style_value(_prop: str, _raw):
        s = str(_raw or "").strip()
        if _prop not in ("fill", "stroke"):
            return s, None, None
        h = s.lstrip("#")
        if re.fullmatch(r"[0-9A-Fa-f]{3,8}", h):
            s = f"#{h}"
        # SVG compatibility: prefer #RRGGBB + *-opacity over rgba(...)
        m4 = re.fullmatch(r"#([0-9A-Fa-f]{4})", s)
        if m4:
            hx = m4.group(1)
            rgb = f"#{hx[0]}{hx[0]}{hx[1]}{hx[1]}{hx[2]}{hx[2]}"
            a = int(hx[3] + hx[3], 16) / 255.0
            op_key = "fill-opacity" if _prop == "fill" else "stroke-opacity"
            return rgb, op_key, f"{a:.4f}".rstrip("0").rstrip(".")
        m8 = re.fullmatch(r"#([0-9A-Fa-f]{8})", s)
        if m8:
            hx = m8.group(1)
            a = int(hx[6:8], 16) / 255.0
            rgb = f"#{hx[0:6]}"
            op_key = "fill-opacity" if _prop == "fill" else "stroke-opacity"
            return rgb, op_key, f"{a:.4f}".rstrip("0").rstrip(".")
        return s, None, None
    tgt = SVG.find_target_exact_in(inst, target_id)
    if tgt is None:
        _l.d(f"field '{key}': target id='{target_id}' NOT FOUND in clone")
        return 0, "miss"
    if SVG.is_text_like(tgt) or (tgt.tag in TEXT_LIKE):
        if (not str(value or "").strip()):
            if _default_expr:
                value = expand_value(str(_default_expr), row)
            elif _default_id and (prop == "text"):
                value = str(_default_id)
        if prop == "xml":
            SVG.replace_xml(tgt, value)
            _l.d(f"field '{key}': XML -> id='{target_id}'")
            return 1, "xml"
        if prop != "text":
            try:
                v, op_key, op_val = _normalize_style_value(prop, value)
                if v == "":
                    _l.d(f"field '{key}': STYLE[{prop}] empty -> keep current style id='{target_id}'")
                    return 0, "skip"
                smap = SVG.style_map(tgt)
                smap[prop] = v
                if op_key:
                    smap[op_key] = op_val
                SVG.style_set(tgt, smap)
                # Text styles are often set on tspans; propagate style there too.
                try:
                    for _ch in tgt.iter():
                        if _ch is tgt:
                            continue
                        _tag = str(getattr(_ch, "tag", "") or "")
                        if not _tag.endswith("tspan"):
                            continue
                        _sm = SVG.style_map(_ch)
                        _sm[prop] = v
                        if op_key:
                            _sm[op_key] = op_val
                        SVG.style_set(_ch, _sm)
                except Exception:
                    pass
                _l.d(f"field '{key}': STYLE[{prop}] -> id='{target_id}'")
                return 1, "style"
            except Exception as ex:
                _l.w(f"field '{key}': STYLE[{prop}] failed on id='{target_id}': {ex}")
                return 0, "miss"
        SVG.replace_text(tgt, value)
        _l.d(f"field '{key}': TEXT -> id='{target_id}'")
        return 1, "text"
    if prop == "xml":
        _l.w(f"field '{key}': [xml] is only supported on text-like targets (id='{target_id}')")
        return 0, "miss"
    if prop != "text":
        try:
            if (not str(value or "").strip()):
                if _default_expr:
                    value = expand_value(str(_default_expr), row)
                elif _default_id:
                    value = str(_default_id)
                elif _default_raw:
                    value = expand_value(str(_default_raw), row)
            v, op_key, op_val = _normalize_style_value(prop, value)
            if v == "":
                try:
                    if isinstance(_P1_KEEP_SET, set):
                        _P1_KEEP_SET.add(target_id)
                except Exception:
                    pass
                _l.d(f"field '{key}': STYLE[{prop}] empty -> keep current style id='{target_id}'")
                return 0, "skip"
            smap = SVG.style_map(tgt)
            smap[prop] = v
            if op_key:
                smap[op_key] = op_val
            SVG.style_set(tgt, smap)
            # Explicit style columns are meant to affect visible artwork, not act as placeholders.
            try:
                if isinstance(_P1_KEEP_SET, set):
                    _P1_KEEP_SET.add(target_id)
            except Exception:
                pass
            _l.d(f"field '{key}': STYLE[{prop}] -> id='{target_id}'")
            return 1, "style"
        except Exception as ex:
            _l.w(f"field '{key}': STYLE[{prop}] failed on id='{target_id}': {ex}")
            return 0, "miss"
    raw_token = (value or "").strip()
    # Phase-1: multivalue cells â€” split into top-level whitespace-separated tokens
    # and process each token independently against the SAME header/target.
    # IMPORTANT: each clone/use/FA job is inserted relative to the same placeholder,
    # so iterating A B C in natural order would leave C on top of B on top of A.
    # Reverse the processing order so the first token remains at the back.
    if raw_token and any(ch.isspace() for ch in raw_token):
        toks = _split_multivalue(raw_token)
        if len(toks) > 1:
            total = 0
            for _tok in reversed(toks):
                c, _ = apply_field_in_clone(inst, key, _tok, row, root_doc=root_doc, use_jobs=use_jobs, fa_jobs=fa_jobs, path_jobs=path_jobs, use_seq=use_seq, layout_obj=layout_obj, sm=sm, ss_registry=ss_registry, transform_jobs=transform_jobs)
                total += int(c or 0)
            return total, 'multi'

    # Header defaults ('ph_id=...') and global fit ('ph_id=~.../.Fit{...}') apply only to non-text targets.
    if not raw_token:
        if _default_expr:
            raw_token = expand_value(str(_default_expr), row).strip()
        elif _default_id:
            raw_token = str(_default_id).strip()
            if raw_token and _default_ops:
                # default_ops already includes leading '~' when present
                raw_token = f"{raw_token}{_default_ops}"
        elif _global_ops and (not SVG.is_text_like(tgt)) and (tgt.tag not in TEXT_LIKE):
            # Header-global fit only (e.g. id=~i5) must NOT create implicit content
            # for empty cells. It only augments explicit per-cell/object values.
            raw_token = ""

    raw_token = _expand_wildcard_ids_in_value(raw_token, root_doc)
    raw_token, raw_paths_spec = _split_paths_suffix(raw_token)
    raw_token, transform_spec = _split_transform_suffixes(raw_token)

    # Header-global ops ('id=~...') are merged later per token in FA queue stage.
    # Doing it there gives deterministic precedence with iterator/item ops.
    if not raw_token:
        if raw_paths_spec:
            try:
                tgt.set('data-dm-keep-paths', '1')
            except Exception:
                pass
            path_jobs.append((tgt, raw_paths_spec, _orient_hint(layout_obj)))
            _l.d(f"field '{key}': PATHS only -> id='{target_id}'")
            return 1, "paths"
        # Phase-1: NEVER delete rect anchors (duplicate headers / multivalue need a stable, unique anchor element).
        if _is_rect_elem(tgt):
            _l.d(f"field '{key}': empty rect anchor kept id='{target_id}'")
            return 0, "skip"
        # Phase-1: Do not delete non-text placeholders during render; they may act as anchors and
        # duplicates/multivalue rely on stability. Visibility is handled in the finalize step.
        _l.d(f"field '{key}': empty non-text kept id='{target_id}'")
        return 0, "skip"
    raw_token, source_was_normalized, symbol_id_for_fallback = _normalize_source_token(
        raw_token, sm=sm, ss_registry=ss_registry
    )

    # For non-text placeholders, normalized source tokens without explicit fit ops
    # should still go through FitAnchor with default behavior (inside+center).
    force_fa_default = False
    compact_ops_present = False
    try:
        _bidc, _placec, _opsc = _parse_object_token(raw_token)
        compact_ops_present = bool((_opsc or "").strip()) and ("~" not in raw_token)
    except Exception:
        compact_ops_present = False
    if source_was_normalized and symbol_id_for_fallback:
        has_fa_sig = _has_fa_signature(raw_token, compact_ops_present=compact_ops_present)
        if (not header_plus) and (not has_fa_sig):
            force_fa_default = True
    else:
        # Local object ids should behave like other sources: implicit Fit/Anchor when
        # no explicit FA signature is provided (default ~i5).
        has_fa_sig = _has_fa_signature(raw_token, compact_ops_present=compact_ops_present)
        if (not header_plus) and (not has_fa_sig):
            try:
                _base_id, _place, _ops_tok = _parse_object_token(raw_token)
                _src_local = root_doc.find(".//*[@id='%s']" % _base_id)
                if _src_local is not None:
                    force_fa_default = True
            except Exception:
                pass

    is_fa_token = force_fa_default or header_plus or compact_ops_present or ("~" in raw_token) or raw_token.endswith("=") or raw_token.endswith("+") or ("=~" in raw_token) or ("+~" in raw_token) or raw_token.lstrip().startswith('[')
    if header_plus and ("~" not in raw_token):
        raw_token = raw_token + "~i"

    def _ensure_ops_full(ops_body: str) -> str:
        s = (ops_body or "").strip()
        if not s:
            return "~"
        if s.startswith(".Fit"):
            s = _fit_suffix_to_ops(s)
        elif not s.startswith("~"):
            s = "~" + s
        s = _normalize_ops_chain(s)
        return s or "~"

    def _merge_header_global_ops(ops_body: str) -> str:
        gops = _normalize_ops_chain(_global_ops or "")
        raw = (ops_body or "").strip()
        # Default implicit fit only when neither local nor header-global ops are present.
        if not raw:
            if gops:
                return gops
            return "~i5"
        base_full = _ensure_ops_full(raw)
        gops = _normalize_ops_chain(_global_ops or "")
        if not gops:
            return base_full
        merged = _merge_fit_ops(gops, base_full)
        return merged or "~i5"

    if is_fa_token:
        # Multivalue support: allow several FA tokens separated by whitespace in the same cell.
        tokens = _split_multivalue(raw_token) if any(ch.isspace() for ch in raw_token) else [raw_token]

        rect_header_key = next((k for k in row.keys() if isinstance(k, str) and k.startswith("rect_ID")), None)
        rect_id_val = ""
        default_ops = ""
        if rect_header_key:
            m = re.match(r"^(rect_ID)(?:~(.+))?\s*$", rect_header_key)
            rect_col = m.group(1) if m else "rect_ID"
            default_ops = (m.group(2) or "")
            rect_id_val = (row.get(rect_header_key) or row.get(rect_col) or "").strip()

        used_placeholder_as_rect = False
        rect_elem_for_fa = None
        if not rect_id_val:
            # No explicit rect_ID provided: use the target element itself as the rect.
            rect_id_val = tgt.get("id") or ""
            if rect_id_val:
                used_placeholder_as_rect = True
                rect_elem_for_fa = tgt

        if not rect_id_val:
            _l.w(f"[deckmaker.fa] placeholder '{key}': no rect target (rect_ID empty and target has no id)")
            return 0, "skip"

        _resolved = SVG.resolve_local_id(inst, rect_id_val)
        if _resolved:
            rect_id_val = _resolved

        queued = 0
        queued_paths = 0
        for tok in (tokens or []):
            tok = (tok or "").strip()
            if not tok:
                continue
            tok_core, tok_paths_spec = _split_paths_suffix(tok)
            if tok_core.lstrip().startswith('['):
                try:
                    arr = _parse_array_token(tok_core)
                except Exception:
                    _l.w(f"[deckmaker.fa] placeholder '{key}': array token invalido '{tok_core}'")
                    continue
                if not arr or not arr.get('items'):
                    continue
                ops_body = (arr.get('ops') or "") or default_ops
                ops_full = _merge_header_global_ops(ops_body)
                g_node, g_id = _build_array_group(
                    inst, root_doc, arr.get('items'), arr.get('layout'),
                    sm=sm, ss_registry=ss_registry
                )
                if g_id:
                    # Use deep-copy for arrays so the temp group can be removed safely.
                    fa_jobs.append((g_id, rect_id_val, ops_full, 'copy', g_node, rect_elem_for_fa, transform_spec))
                    queued += 1
                    _l.d(f"[deckmaker.fa] queued '{key}' -> base='{g_id}' rect='{rect_id_val}' place=copy ops='{ops_full or '~'}'")
                    queued_paths += _queue_paths_for_target(tgt, tok_paths_spec, path_jobs, layout_obj)
                continue
            try:
                base_id, place, ops_tok = _parse_object_token(tok_core)
            except Exception:
                _l.w(f"[deckmaker.fa] placeholder '{key}': token invalido '{tok_core}'")
                continue
            ops_body = (ops_tok or "") or default_ops
            ops_full = _merge_header_global_ops(ops_body)
            fa_jobs.append((base_id, rect_id_val, ops_full, place, None, rect_elem_for_fa, transform_spec))
            queued += 1
            _l.d(f"[deckmaker.fa] queued '{key}' -> base='{base_id}' rect='{rect_id_val}' place={place} ops='{ops_full or '~'}'")
            queued_paths += _queue_paths_for_target(tgt, tok_paths_spec, path_jobs, layout_obj)
        # Remove placeholder immediately only when it is NOT serving as the rect itself.
        if (queued > 0) and (not used_placeholder_as_rect) and (queued_paths <= 0):
            par = tgt.getparent()
            if par is not None:
                try:
                    par.remove(tgt)
                except Exception as ex:
                    _l.w(f"field '{key}': removing placeholder after enqueue fa failed: {ex}")

        return queued, "fa"
    if source_was_normalized and symbol_id_for_fallback and not is_fa_token:
        try:
            use_id, _u = _insert_symbol_use(
                symbol_id_for_fallback,
                tgt=tgt, target_id=target_id, use_seq=use_seq,
                transform_spec=transform_spec, use_jobs=use_jobs,
                raw_paths_spec=raw_paths_spec, path_jobs=path_jobs, layout_obj=layout_obj,
            )
            if not use_id:
                return 0, "miss"
            _l.d(f"field '{key}': SOURCE(use) id='{use_id}' symbol='{symbol_id_for_fallback}' [fallback center]")
            return 1, 'source'
        except Exception as ex:
            _l.w(f"field '{key}': SOURCE fallback <use> failed: {ex}")
    src_id = raw_token
    res = _insert_wrapped_source_use(
        root_doc, src_id,
        tgt=tgt, target_id=target_id, use_seq=use_seq,
        transform_spec=transform_spec, use_jobs=use_jobs,
        raw_paths_spec=raw_paths_spec, path_jobs=path_jobs, layout_obj=layout_obj,
    )
    if not res or not res[0]:
        return 0, "miss"
    use_id, wrap_id, bw, bh = res
    _l.d(f"field '{key}': INSERT use id='{use_id}' wrap='{wrap_id}' (src_bbox w={bw:.2f} h={bh:.2f})")
    return 1, "clone"

