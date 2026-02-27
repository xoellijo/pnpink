# [2026-02-18] Change: remove %...% variable expansion in dataset values.
# Changelog: add target array grouping with explicit group bbox for fit_anchor.
# [2026-02-19] Add: split layout gaps into gaps + offset properties.
# [2026-02-20] Add: split-board rendering for oversized templates using fit+clip pipeline.
# [2026-02-20] Debug: log fallback slot failure for split boards.
# -*- coding: utf-8 -*-
import log as LOG
_l = LOG
_DBG_FA_RECT_IDS = None  # debug: set of rect/placeholder ids used by FA in current instance
import re
import math


# ---------------- spritesheet alias token parsing ----------------

_ALIAS_TOKEN_RE = re.compile(r"^@(?P<name>[A-Za-z][\w\-\.]*)((?:\[[^\]]+\])+)(?:~(?P<ops>.*))?$")

def _expand_index_expr(expr: str):
    """Expand a single bracket expression into a list.

    Supports:
      - N
      - A-B (range, inclusive)
      - '*' (star)
      - '-' gap placeholder
      - K- (K gaps)
      - K*X (repeat X K times)

    Returns a list of ints and/or None (for gaps), or [StarIdx] sentinel via string '*'.
    """
    s = (expr or '').strip()
    if not s:
        return []
    if ',' in s:
        raise ValueError("No se permiten comas en índices")
    toks = s.split()
    out = []
    for t in toks:
        if t == '*':
            out.append('*'); continue
        if t == '-':
            out.append(None); continue
        m = re.match(r"^(\d+)-$", t)
        if m:
            out.extend([None] * int(m.group(1))); continue
        m = re.match(r"^(\d+)\*(\d+)$", t)
        if m:
            k = int(m.group(1)); v = int(m.group(2))
            out.extend([v] * k); continue
        m = re.match(r"^(\d+)-(\d+)$", t)
        if m:
            a = int(m.group(1)); b = int(m.group(2))
            step = 1 if b >= a else -1
            out.extend(list(range(a, b + step, step))); continue
        if re.match(r"^\d+$", t):
            out.append(int(t)); continue
        raise ValueError(f"Índice no reconocido: '{t}'")
    return out

def _parse_sprite_alias_token(raw_token: str):
    """Parse '@alias[...]' token.

    Returns (alias, dims_lists, ops) where dims_lists is a list of lists corresponding to each [].
    """
    m = _ALIAS_TOKEN_RE.match((raw_token or '').strip())
    if not m:
        return None
    name = m.group('name')
    idxs_raw = m.group(2) or ''
    ops = (m.group('ops') or '').strip()
    inner = idxs_raw[1:-1]
    chunks = inner.split('][') if inner else []
    dims = []
    for ch in chunks:
        try:
            dims.append(_expand_index_expr(ch))
        except Exception as ex:
            _l.w(f"[spritesheets] token '{raw_token}': invalid index expression '[{ch}]' ({ex})")
            return None
    return name, dims, ops
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
from typing import Dict, Optional, Tuple
_HEADER_RE = re.compile(r"^(?P<id>[^\[\]]+)(?:\[(?P<prop>[^\]]+)\])?$")

# Phase-1: per-instance set of rect ids to keep visible (from header '+' modifier)
_P1_KEEP_SET = None

def _slot_index_to_rc(within: int, plan_obj, layout_obj):
    """Map slot_index within page to (r,c) in the logical grid.
    NOTE: kept identical to legacy implementation that lived inside deckmaker/engine.
    """
    cols = int(getattr(plan_obj, 'cols', 0) or 0)
    rows = int(getattr(plan_obj, 'rows', 0) or 0)
    if cols <= 0 or rows <= 0:
        return 0, 0
    sweep_rows_first = bool(getattr(layout_obj, 'sweep_rows_first', True))
    if sweep_rows_first:
        r0 = within // cols
        c0 = within % cols
    else:
        c0 = within // rows
        r0 = within % rows
    if bool(getattr(layout_obj, 'invert_rows', False)):
        r0 = (rows - 1) - r0
    if bool(getattr(layout_obj, 'invert_cols', False)):
        c0 = (cols - 1) - c0
    return int(r0), int(c0)

def _gaps_has_offsets(layout_obj) -> bool:
    """True only if gaps params 3..6 are non-zero."""
    try:
        k = getattr(layout_obj, 'gaps', None)
        if isinstance(k, (list, tuple)) and len(k) >= 6:
            for t in list(k)[2:6]:
                if t is None:
                    continue
                v = float(SVG.measure_to_mm(t, base_mm=None))
                if abs(v) > 1e-9:
                    return True
    except Exception:
        pass
    return False

def _parse_object_token(token: str) -> Tuple[str, str, str]:
    m = re.match(r"""
        ^(?P<id>[A-Za-z_][-A-Za-z0-9_:.]*)
        (?P<mode>[=+])?
        (?:~(?P<ops>.+))?
        \s*$
    """, token or "", re.VERBOSE)
    if not m:
        raise ValueError(f"Invalid object token: '{token}'")
    base_id = m.group("id")
    mod     = m.group("mode")
    ops     = m.group("ops") or ""
    place   = "clone" if mod is None else ("copy" if mod=="=" else "clone+unlink")
    return base_id, place, ops

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
    for t in _split_multivalue(body):
        tt = (t or '').strip()
        if not tt:
            continue
        if tt == '-' or re.fullmatch(r"-+", tt):
            items.append(None)
            continue
        m = re.match(r"^(\d+)-$", tt)
        if m:
            items.extend([None] * int(m.group(1)))
            continue
        base_txt, _sep, ops_txt = tt.partition("~")
        base_txt = base_txt.strip()
        ops_txt = ops_txt.strip()
        if not base_txt:
            continue
        items.append({"id": base_txt, "ops": ops_txt})

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


def _resolve_array_item(root_doc, inst_node, item_id: str, sm=None):
    s = (item_id or '').strip()
    if not s:
        return None
    if sm is not None:
        src_val, sel_src, _ops, _tag = _parse_source_token_with_selector(s)
        if src_val:
            try:
                v_urls = _resolve_virtual_source_urls(sm, src_val, sel_src, warn_tag=_virtual_warn_tag(src_val, "wkmc"))
                if v_urls is not None and len(v_urls) > 0:
                    src0, _s0, _o0, _t0 = _parse_source_token_with_selector(v_urls[0])
                    sref = sm.register(src0 or "")
                else:
                    sref = sm.register(src_val)
                s = sref.symbol_id
            except Exception:
                pass
    if sm is not None:
        low = s.lower()
        if low.startswith('@icon://') or low.startswith('icon://'):
            try:
                src_val = s[1:] if low.startswith('@icon://') else s
                sref = sm.register(src_val)
                s = sref.symbol_id
            except Exception:
                pass
    base = SVG.find_target_exact_in(inst_node, s)
    if base is None:
        base = SVG.find_target_exact_in(root_doc, s)
    return base


def _build_array_group(inst_node, root_doc, items, layout_spec, *, sm=None, group_id_prefix='dm_array'):
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
        base = _resolve_array_item(root_doc, inst_node, item_id, sm=sm)
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

    cw = (float(cols) * ref_w) + (float(max(0, cols - 1)) * gaps_px[0])
    ch = (float(rows) * ref_h) + (float(max(0, rows - 1)) * gaps_px[1])
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

def _is_rect_elem(e):
    try:
        return (e is not None) and ((e.tag == 'rect') or str(e.tag).endswith('}rect'))
    except Exception:
        return False


def _flatten_group_transform(g):
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


# --------------------- dataset row helpers (positional rows) ---------------------

def _row_cells(row) -> list:
    if isinstance(row, dict):
        c = row.get("cells")
        if isinstance(c, list):
            return c
    return []

def _build_row_map(headers: list, row: dict) -> Dict[str, str]:
    """Build a dict-like view of the row for variable expansion and legacy lookups.

    - For duplicate headers, **last column wins**, matching the old dict-overwrite behavior.
    - Meta fields (__dm_*) are included as-is.
    """
    out: Dict[str, str] = {}
    cells = _row_cells(row)
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
            # keep non-string meta values out unless they are string-like
            if isinstance(v, (str, int, float)):
                out[k] = str(v)
    return out

def _iter_row_fields(headers: list, row: dict):
    """Yield (header_key, raw_cell) for each column, preserving order and duplicates."""
    cells = _row_cells(row)
    for i, h in enumerate(headers or []):
        raw = cells[i] if i < len(cells) else ""
        yield h, ("" if raw is None else str(raw))

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


def _fit_suffix_to_ops(fit_suffix: str) -> str:
    """Convert a '.Fit{...}' suffix into '~ops' (short FA ops string).

    Returns '' on parse failure.
    """
    s = (fit_suffix or '').strip()
    if not s:
        return ""
    if not s.startswith('.Fit'):
        return ""
    try:
        # Example: fit_suffix='.Fit{m7^15}'
        cmd = DSL.parse(f"X{s}")
        fs = getattr(cmd, 'fit', None)
        if fs:
            body = DSL.ops_from_fit_spec(fs) or ""
            return f"~{body}" if body else "~"
    except Exception:
        return ""
    return ""

def _merge_fit_ops(prefix_ops: str, suffix_ops: str) -> str:
    """Merge two FA ops strings preserving order: prefix then suffix.

    Semantics: the resulting ops must behave like applying prefix, then suffix.
    We concatenate their bodies and then canonicalize through dsl.fit_spec_from_ops
    so mutually-exclusive directives collapse (last wins), matching user expectations.
    """
    def _body(x: str) -> str:
        x = (x or "").strip()
        if not x:
            return ""
        if x.startswith("~"):
            return x[1:]
        return x

    b1 = _body(prefix_ops)
    b2 = _body(suffix_ops)
    if not b1 and not b2:
        return ""

    merged_raw = "~" + (b1 + b2)
    try:
        fs = DSL.fit_spec_from_ops(merged_raw)
        body = DSL.ops_from_fit_spec(fs) or ""
        return "~" + body if body else "~"
    except Exception:
        # Fallback: keep raw concatenation if DSL rejects for any reason.
        return merged_raw

def _normalize_ops_chain(ops: str) -> str:
    """Normalize chained ops like '~[30%]~m!' into a single canonical ops string."""
    s = (ops or "").strip()
    if not s:
        return ""
    # Drop leading '~' and split on subsequent '~'
    had_leading = s.startswith("~")
    if had_leading:
        s = s[1:]
    parts = [p for p in s.split("~") if p.strip()]
    if not parts:
        return "~" if had_leading else ""
    # Prefer semantic merge through FitSpec to avoid invalid concatenations.
    def _merge_specs(a: Optional["DSL.FitSpec"], b: Optional["DSL.FitSpec"]) -> Optional["DSL.FitSpec"]:
        if a is None:
            return b
        if b is None:
            return a
        out = DSL.FitSpec()
        out.mode = a.mode
        out.anchor = a.anchor
        out.border = a.border[:] if a.border else None
        out.shift = a.shift[:] if a.shift else None
        out.rotate = a.rotate
        out.mirror = a.mirror
        out.clip = a.clip
        out.clip_stage = a.clip_stage
        if b.mode is not None:
            out.mode = b.mode
        if b.anchor is not None:
            out.anchor = b.anchor
        if b.border is not None:
            out.border = b.border[:] if b.border else None
        if b.shift is not None:
            out.shift = b.shift[:] if b.shift else None
        if b.rotate is not None:
            out.rotate = (out.rotate or 0.0) + b.rotate
        if b.mirror is not None:
            out.mirror = b.mirror
        if b.clip is not None:
            out.clip = b.clip
            out.clip_stage = b.clip_stage
        return out

    fs = None
    for p in parts:
        try:
            fs_p = DSL.fit_spec_from_ops("~" + p)
        except Exception:
            fs = None
            break
        fs = _merge_specs(fs, fs_p)
    if fs is not None:
        return DSL.ops_from_fit_spec(fs) or ("~" if had_leading else "")

    # Fallback: raw merge
    merged = ""
    for p in parts:
        merged = _merge_fit_ops(merged, "~" + p)
    return merged or ("~" if had_leading else "")

def _parse_header_default_spec(spec: str, target_id: str) -> Tuple[Optional[str], str, str]:
    """Parse the RHS of a header default declaration.

    Examples (spec):
      'id1'                -> (default_id='id1', default_ops='',    global_ops='')
      'id1~m7^'             -> (default_id='id1', default_ops='~m7^', global_ops='')
      'id1.Fit{m7^}'        -> (default_id='id1', default_ops='~m7^', global_ops='')
      '~[-50%]'             -> (default_id=None,  default_ops='',    global_ops='~[-50%]')
      '.Fit{m7^15}'         -> (default_id=None,  default_ops='',    global_ops='~m7^15')
      'ph_id'               -> (default_id='ph_id', default_ops='',  global_ops='')
    """
    s = (spec or "").strip()
    if not s:
        return None, "", ""
    # Global-fit-only forms.
    if s.startswith("~"):
        return None, "", s
    if s.startswith(".Fit"):
        return None, "", (_fit_suffix_to_ops(s) or "")
    # Default-id forms, optionally with ops.
    default_id = None
    default_ops = ""
    global_ops = ""
    # Split on first '~' or '.Fit{...}'
    m = re.match(r"^(?P<id>[A-Za-z_][-A-Za-z0-9_:.]*)(?P<rest>.*)$", s)
    if not m:
        return None, "", ""
    default_id = m.group("id")
    rest = (m.group("rest") or "").strip()
    if rest:
        if rest.startswith("~"):
            default_ops = rest
        elif rest.startswith(".Fit"):
            default_ops = _fit_suffix_to_ops(rest)
        else:
            # Unknown tail; ignore to stay forward-compatible.
            default_ops = ""
    return default_id, (default_ops or ""), (global_ops or "")

def parse_header_key_full(key: str) -> Dict[str, object]:
    """Parse a dataset header key including modifiers.

    Supported modifiers:
      - '+' suffix on id: keep rect anchor visible (Phase-1 behavior).
      - '=' default declaration: 'ph_id=...' (defaults and/or global fit).

    Returns dict:
      {target_id, prop, header_plus, default_id, default_ops, global_ops}
    """
    m = _HEADER_RE.match((key or "").strip())
    if not m:
        return {'target_id': (key or '').strip(), 'prop': 'text', 'header_plus': False,
                'default_id': None, 'default_ops': '', 'global_ops': ''}

    raw_id = (m.group("id") or "").strip()
    prop = ((m.group("prop") or "text").strip().lower()) or "text"
    if prop not in ("text", "xml"):
        prop = "text"

    # Split default declaration if present.
    left, has_eq, right = raw_id.partition("=")
    left = (left or "").strip()
    right = (right or "").strip()

    header_plus = False
    if left.endswith("+"):
        header_plus = True
        left = left[:-1].strip()

    target_id = left

    # Phase-1 keep-visible set from '+'
    global _P1_KEEP_SET
    if header_plus and target_id:
        try:
            if isinstance(_P1_KEEP_SET, set):
                _P1_KEEP_SET.add(target_id)
        except Exception:
            pass

    default_id = None
    default_ops = ""
    global_ops = ""
    if has_eq:
        default_id, default_ops, global_ops = _parse_header_default_spec(right, target_id)

    return {'target_id': target_id, 'prop': prop, 'header_plus': header_plus,
            'default_id': default_id, 'default_ops': (default_ops or ''), 'global_ops': (global_ops or '')}

def parse_header_key(key: str) -> Tuple[str, str, bool]:
    info = parse_header_key_full(key)
    return str(info.get('target_id') or ''), str(info.get('prop') or 'text'), bool(info.get('header_plus') or False)

def _parse_source_like_token(raw_token: str):
    """Parse source-like tokens:
      - @{...}[~ops|.Fit{...}]
      - Source{...}[~ops|.Fit{...}]
      - S{...}[~ops|.Fit{...}]
      - http(s)://... [~ops]
    Returns (src_val, ops_suffix, tag) or (None, '', '').
    """
    s = (raw_token or "").strip()
    if not s:
        return None, "", ""

    m_all = re.match(
        r'^\s*@\{\s*(?P<body>[^}]*)\s*\}\s*(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*)))?\s*$',
        s,
        re.IGNORECASE,
    )
    tag = "@{...}"
    if not m_all:
        m_all = re.match(
            r'^\s*(?:Source|S)\s*\{\s*(?P<body>[^}]*)\s*\}\s*(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*)))?\s*$',
            s,
            re.IGNORECASE,
        )
        tag = "Source{...}"
    if m_all:
        body_for_dsl = (m_all.group("body") or "").strip()
        src_val = None
        try:
            cmd = DSL.maybe_parse(f"@{{{body_for_dsl}}}")
        except Exception:
            cmd = None
        if cmd and getattr(cmd, "name", None) == "Source" and getattr(cmd, "target", None) is not None:
            try:
                src_val = cmd.target.args.get("src") if hasattr(cmd.target, "args") else None
                if not src_val:
                    src_val = getattr(cmd.target, "src", None)
            except Exception:
                src_val = None
        if not src_val:
            src_val = body_for_dsl

        fit_text = m_all.group("fit")
        legacy_ops = m_all.group("ops")
        if fit_text:
            try:
                fit_cmd = DSL.parse(f"X.{fit_text}")
                fs = getattr(fit_cmd, "fit", None)
                ops = DSL.ops_from_fit_spec(fs) if fs else ""
            except Exception:
                ops = ""
        elif legacy_ops:
            ops = f"~{legacy_ops.strip()}"
        else:
            ops = ""
        ops = _normalize_ops_chain(ops)
        return src_val, ops, tag

    # Bare URL token (optionally with ~ops).
    m_url = re.match(r"^\s*(?P<url>https?://\S+?)(?:~(?P<ops>.*))?\s*$", s, re.IGNORECASE)
    if m_url:
        url = (m_url.group("url") or "").strip()
        ops = (m_url.group("ops") or "").strip()
        ops = (f"~{ops}" if ops else "")
        ops = _normalize_ops_chain(ops)
        return url, ops, "url"
    return None, "", ""

def _parse_index_selector_1based(sel: str) -> list:
    """Parse selector body like '2 4..12 15..26' into 1-based integer indices."""
    body = str(sel or "").strip()
    if body.startswith("[") and body.endswith("]"):
        body = body[1:-1].strip()
    if not body:
        return []
    out = []
    toks = [t for t in re.split(r"[\s,]+", body) if t]
    for t in toks:
        m = re.match(r"^(\d+)\s*\.\.\s*(\d+)$", t)
        if m:
            a = int(m.group(1)); b = int(m.group(2))
            step = 1 if b >= a else -1
            out.extend(list(range(a, b + step, step)))
            continue
        if re.match(r"^\d+$", t):
            out.append(int(t))
            continue
    return out

def _select_1based_with_warning(items: list, selector: str, warn_tag: str) -> list:
    arr = list(items or [])
    idxs = _parse_index_selector_1based(selector)
    if not idxs:
        return arr
    out = []
    n = len(arr)
    for i1 in idxs:
        if i1 <= 0 or i1 > n:
            _l.w(f"[{warn_tag}] selector index out of range: {i1} (size={n})")
            continue
        out.append(arr[i1 - 1])
    return out

def _parse_source_token_with_selector(raw_token: str):
    """Parse source-like token plus optional trailing selector [...]."""
    s = (raw_token or "").strip()
    m = re.match(r"^\s*(?P<core>(?:@\{[^}]*\}|(?:Source|S)\s*\{[^}]*\}|https?://\S+?))\s*(?P<sel>\[[^\]]*\])?\s*(?P<tail>(?:\.(?:Fit)\s*\{[^}]*\}|~.*)?)\s*$", s, re.IGNORECASE)
    if not m:
        return None, None, "", ""
    core = (m.group("core") or "").strip()
    sel = (m.group("sel") or "").strip() or None
    tail = (m.group("tail") or "").strip()
    src_val, ops, tag = _parse_source_like_token(core + tail)
    return src_val, sel, ops, tag

def _virtual_warn_tag(src_val: str, base_tag: str) -> str:
    s = (src_val or "").strip().lower()
    if s.startswith("pxby://"):
        return (base_tag or "pxby").replace("wkmc", "pxby")
    if s.startswith("oclp://"):
        return (base_tag or "oclp").replace("wkmc", "oclp")
    return base_tag

def _resolve_virtual_source_urls(sm, src_val: str, selector: Optional[str], *, warn_tag: str) -> Optional[list]:
    if sm is None:
        return None
    s = (src_val or "").strip()
    sl = s.lower()
    if sl.startswith("wkmc://"):
        urls = list(sm.resolve_wkmc_urls(s) or [])
    elif sl.startswith("pxby://"):
        urls = list(sm.resolve_pxby_urls(s) or [])
    elif sl.startswith("oclp://"):
        urls = list(sm.resolve_oclp_urls(s) or [])
    else:
        return None
    urls = _select_1based_with_warning(urls, selector or "", warn_tag)
    try:
        if urls:
            sm.prefetch_urls(urls)
    except Exception:
        pass
    return [f"@{{{u}}}" for u in urls]


def _resolve_with_base(ctx, page, card, layout, gaps, doc_page_mm):
    fn = getattr(ctx, 'resolve_with_base', None)
    if callable(fn):
        return fn(page, card, layout, gaps, doc_page_mm)
    return LYT.resolve(page, card, layout, gaps, doc_page_mm)

def _page_attrs_from_resolved(resolved) -> Dict[str,str]:
    attrs = {}
    mg = SVG.coerce_margins_mm(resolved.page.margins_mm())
    if any(abs(v)>1e-9 for v in (mg.top, mg.right, mg.bottom, mg.left)):
        attrs["margin"] = f"{mg.top} {mg.right} {mg.bottom} {mg.left}"
    return attrs


def ensure_page_for(page_index, pages, nv, current_resolved, doc_page_mm, page_gap_px, px_per_mm):
    pw_mm, ph_mm = current_resolved.page.resolved_size_mm(doc_page_mm)
    w_px, h_px = pw_mm * px_per_mm, ph_mm * px_per_mm
    attrs = _page_attrs_from_resolved(current_resolved)
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
        self.plan, self.local_slots = self._compute_plan_for(self.current,
                                                             self.pages[0]["w"],
                                                             self.pages[0]["h"])
        if self.plan.per_page <= 0:
            raise inkex.AbortExtension("No caben cartas en la página con el preset/layout actual.")
        _l.d("planner.init", {"slots_per_page": self.plan.per_page})
    def _ensure_fallback_plan_for_split(self):
        if getattr(self.plan, 'per_page', 0) > 0:
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
    def slots_per_page(self) -> int: return int(self.plan.per_page)
    def page_count(self) -> int: return len(self.pages)
    def page_size_px(self, idx: int = None) -> tuple[float, float]:
        i = self.page_index if idx is None else idx
        return self.pages[i]["w"], self.pages[i]["h"]
    def sync_page_attrs(self):
        self.ensure_page_for(self.page_index, self.pages, self.nv, self.current,
                             self.doc_page_mm, self.page_gap_px, self.px_per_mm)
    def jump_page(self):
        self.page_index += 1; self.slot_index = 0
        self.ensure_page_for(self.page_index, self.pages, self.nv, self.current,
                             self.doc_page_mm, self.page_gap_px, self.px_per_mm)
        pw, ph = self.page_size_px()
        self.plan, self.local_slots = self._compute_plan_for(self.current, pw, ph)
        if self.plan.per_page <= 0:
            self._ensure_fallback_plan_for_split()
        _l.d("planner.jump_page", {"page": self.page_index+1, "slots_per_page": self.plan.per_page})
    def apply_preset(self, new_resolved):
        def _sig(r):
            pg = r.page
            mg = SVG.coerce_margins_mm(pg.margins_mm())
            lay = r.layout
            card = r.card
            return (
                pg.name, pg.width_mm, pg.height_mm, pg.landscape,
                round(mg.top,3), round(mg.right,3), round(mg.bottom,3), round(mg.left,3),
                getattr(lay, 'cols', None), getattr(lay, 'rows', None),
                getattr(lay, 'sweep_rows_first', None),
                r.gaps.h, r.gaps.v,
                card.name, card.width_mm, card.height_mm, card.landscape
            )
        old_sig = _sig(self.current)
        new_sig = _sig(new_resolved)
        self.current = new_resolved
        self.sync_page_attrs()
        if self.slot_index != 0 and new_sig != old_sig:
            self.page_index += 1
            self.slot_index = 0
            self.ensure_page_for(self.page_index, self.pages, self.nv, self.current,
                                 self.doc_page_mm, self.page_gap_px, self.px_per_mm)
        pw, ph = self.page_size_px()
        self.plan, self.local_slots = self._compute_plan_for(self.current, pw, ph)
        if self.plan.per_page <= 0:
            self._ensure_fallback_plan_for_split()
        if self.plan.per_page <= 0:
            raise inkex.AbortExtension("No caben cartas con el nuevo preset/layout.")
        _l.d("planner.apply_preset", {"page": self.page_index+1, "slots_per_page": self.plan.per_page})
    def begin_slot(self):
        """
        Devuelve (slot_x_abs, slot_y_abs) del slot actual.
        - self.local_slots contiene (x,y) o (x,y,w,h) en coords LOCALES al content-box de la página.
        - Para hacerlas absolutas en el documento, sumamos:
            page_offset (p.x,p.y) + content_origin (márgenes) + centering (left,top) + local(x,y).
        """
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
        top  = float(getattr(self.plan, "top", 0.0))
        slot_x_abs = px + cx + left + local_x
        slot_y_abs = py + cy + top  + local_y
        return slot_x_abs, slot_y_abs
    def commit_slot(self):
        self.slot_index += 1

def _ensure_wrap_symbol_for_src(doc_root, src):
    SVG.ensure_xlink_ns(doc_root)
    src_id = src.get('id')
    if not src_id: raise inkex.AbortExtension("Source element for wrap has no id.")
    wrap_id = f"wrap_{src_id}"
    bb = src.bounding_box()
    bw, bh = float(bb.width), float(bb.height)
    bx, by = float(bb.left), float(bb.top)
    if bw <= 0 or bh <= 0: raise inkex.AbortExtension(f"Invalid bbox for '{src_id}'.")
    if doc_root.xpath(f".//*[@id='{wrap_id}']"):
        return wrap_id, bw, bh
    defs = SVG.ensure_defs(doc_root)
    sym = SVG.etree.SubElement(defs, inkex.addNS('symbol','svg')); sym.set('id', wrap_id); sym.set('viewBox', f"0 0 {bw} {bh}")
    inner = SVG.etree.SubElement(sym, inkex.addNS('use','svg')); inner.set(inkex.addNS('href','xlink'), f"#{src_id}")
    if bx or by: inner.set('transform', f"translate({-bx:.6f},{-by:.6f})")
    return wrap_id, bw, bh

def _make_use_for_wrap(wrap_id: str, w: float, h: float, use_id: Optional[str]=None) -> SVG.etree._Element:
    u = SVG.etree.Element(inkex.addNS('use','svg')); u.set(inkex.addNS('href','xlink'), f"#{wrap_id}")
    u.set('width', f"{w:.6f}"); u.set('height', f"{h:.6f}"); u.set('preserveAspectRatio','xMidYMid meet')
    if use_id: u.set('id', use_id)
    return u

def _center_use_over_placeholder(u, placeholder):
    bb_t = placeholder.bounding_box()
    cx_t = float(bb_t.left) + float(bb_t.width)*0.5
    cy_t = float(bb_t.top)  + float(bb_t.height)*0.5
    w = float(u.get('width') or "0"); h = float(u.get('height') or "0")
    x = cx_t - w/2; y = cy_t - h/2
    u.set('x', f"{x:.6f}"); u.set('y', f"{y:.6f}")
    par = placeholder.getparent()
    # DEBUG (Phase 1 headers-dup/multivalue): track placeholder removals that may orphan rects for later FA jobs
    try:
        pid = placeholder.get('id') or ''
        in_fa = bool(_DBG_FA_RECT_IDS) and pid in _DBG_FA_RECT_IDS
        par_id = (par.get('id') if par is not None else None)
        _l.d(f"[dbg.use_rm] placeholder id='{pid}' par='{par_id}' in_fa={in_fa}")
    except Exception:
        pass
    if par is not None:
        # Phase-1: do not delete rect anchors; they will be hidden at finalize unless any header uses '+'
        if _is_rect_elem(placeholder):
            return
        try:
            par.remove(placeholder)
        except Exception as ex:
            _l.w(f"removing placeholder '{placeholder.get('id')}' failed: {ex}")

def apply_field_in_clone(inst, key, raw_val, row, *, root_doc, use_jobs, fa_jobs, use_seq, sm=None, ss_registry=None):
    hk = parse_header_key_full(key)
    target_id = hk.get('target_id') or ''
    prop = hk.get('prop') or 'text'
    header_plus = bool(hk.get('header_plus') or False)
    value = expand_value(raw_val, row)
    tgt = SVG.find_target_exact_in(inst, target_id)
    if tgt is None:
        _l.d(f"field '{key}': target id='{target_id}' NOT FOUND in clone")
        return 0, "miss"
    if SVG.is_text_like(tgt) or (tgt.tag in TEXT_LIKE):
        if prop == "xml":
            SVG.replace_xml(tgt, value)
            _l.d(f"field '{key}': XML → id='{target_id}'")
            return 1, "xml"
        SVG.replace_text(tgt, value)
        _l.d(f"field '{key}': TEXT → id='{target_id}'")
        return 1, "text"
    raw_token = (value or "").strip()
    # Phase-1: multivalue cells — split into top-level whitespace-separated tokens (Z-order by token order)
    # and process each token independently against the SAME header/target.
    if raw_token and any(ch.isspace() for ch in raw_token):
        toks = _split_multivalue(raw_token)
        if len(toks) > 1:
            total = 0
            for _tok in toks:
                c, _ = apply_field_in_clone(inst, key, _tok, row, root_doc=root_doc, use_jobs=use_jobs, fa_jobs=fa_jobs, use_seq=use_seq, sm=sm, ss_registry=ss_registry)
                total += int(c or 0)
            return total, 'multi'

    # Header defaults ('ph_id=...') and global fit ('ph_id=~.../.Fit{...}') apply only to non-text targets.
    try:
        _default_id = hk.get('default_id') if isinstance(hk, dict) else None
        _default_ops = (hk.get('default_ops') or '') if isinstance(hk, dict) else ''
        _global_ops = (hk.get('global_ops') or '') if isinstance(hk, dict) else ''
    except Exception:
        _default_id = None
        _default_ops = ''
        _global_ops = ''

    if not raw_token and _default_id:
        raw_token = str(_default_id).strip()
        if raw_token and _default_ops:
            # default_ops already includes leading '~' when present
            raw_token = f"{raw_token}{_default_ops}"

    if raw_token and _global_ops:
        # Merge ops: header/global first, then token-local ops (if any) so the token-local ops wins.
        if "~" in raw_token:
            head, _, tail = raw_token.partition("~")
            suffix = ("~" + tail) if tail is not None else "~"
            merged = _merge_fit_ops(_global_ops, suffix)
            raw_token = f"{head}{merged}" if merged else head
        else:
            raw_token = f"{raw_token}{_global_ops}"
    if not raw_token:
        # Phase-1: NEVER delete rect anchors (duplicate headers / multivalue need a stable, unique anchor element).
        if _is_rect_elem(tgt):
            _l.d(f"field '{key}': empty rect anchor kept id='{target_id}'")
            return 0, "skip"
        # Phase-1: Do not delete non-text placeholders during render; they may act as anchors and
        # duplicates/multivalue rely on stability. Visibility is handled in the finalize step.
        _l.d(f"field '{key}': empty non-text kept id='{target_id}'")
        return 0, "skip"
    source_was_normalized = False
    symbol_id_for_fallback = None

    # Spritesheet alias token: @sp1[14]~ops or @sp1[2][1]~ops
    if raw_token.startswith('@') and (not raw_token.startswith('@{')) and sm is not None and ss_registry is not None:
        parsed = _parse_sprite_alias_token(raw_token)
        if parsed:
            a_name, dims, ops_tail = parsed
            if a_name in ss_registry:
                frame = None
                page = 1
                col = None
                row_i = None
                _l.i(f"[spritesheet] token seen in render: '{raw_token}'")
                try:
                    if len(dims) == 1:
                        frame = next((x for x in dims[0] if isinstance(x, int)), None)
                        if frame is None:
                            frame = 1
                        if len([x for x in dims[0] if isinstance(x, int)]) > 1:
                            _l.w(f"[spritesheets] token '{raw_token}': multiple frame indices not supported yet; using first={frame}")
                    elif len(dims) == 2:
                        col = next((x for x in dims[0] if isinstance(x, int)), None)
                        row_i = next((x for x in dims[1] if isinstance(x, int)), None)
                    elif len(dims) == 3:
                        page = next((x for x in dims[0] if isinstance(x, int)), None)
                        col = next((x for x in dims[1] if isinstance(x, int)), None)
                        row_i = next((x for x in dims[2] if isinstance(x, int)), None)
                    else:
                        _l.w(f"[spritesheets] token '{raw_token}': invalid selector dims={len(dims)}")
                except Exception as ex:
                    _l.w(f"[spritesheets] token '{raw_token}': parse error: {ex}")

                _l.i(f"[spritesheet] parsed indices: page={page} col={col} row={row_i} idx={frame}")
                if frame is not None or (col is not None and row_i is not None):
                    try:
                        _l.i(f"[spritesheet] frame symbol requested id=sp_{a_name}_? selector p={page} c={col} r={row_i} idx={frame}")
                        sref = sm.register_spritesheet_frame(a_name, frame=frame, page=page, col=col, row=row_i)
                        if sref is not None:
                            _l.i(f"[spritesheet] frame symbol created id={sref.symbol_id}")
                            symbol_id_for_fallback = sref.symbol_id
                            raw_token = f"{sref.symbol_id}" + (f"~{ops_tail}" if ops_tail else "")
                            source_was_normalized = True
                            _l.d(f"[spritesheets] normalized '@{a_name}[...]' → '{raw_token}'")
                    except Exception as ex:
                        _l.w(f"[spritesheets] frame resolve failed '{raw_token}': {ex}")
            else:
                _l.d(f"[spritesheets] token '{raw_token}': alias @{a_name} not registered; ignoring")
    if sm is not None:
        src_val, sel_src, ops_from_token, src_tag = _parse_source_token_with_selector(raw_token)
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
                            symbol_id_for_fallback = ids[0]
                            raw_token = f"{ids[0]}{ops_from_token}"
                        else:
                            raw_token = "[" + " ".join(ids) + "]" + (ops_from_token or "")
                        source_was_normalized = True
                        _l.d(f"[deckmaker.src] normalized virtual '{src_tag}' → '{raw_token}'")
                else:
                    sref = sm.register(src_val)
                    symbol_id_for_fallback = sref.symbol_id
                    raw_token = f"{sref.symbol_id}{ops_from_token}"
                    source_was_normalized = True
                    _l.d(f"[deckmaker.src] normalized '{src_tag}' → '{raw_token}' (symbol in <defs>)")
            except Exception as ex:
                _l.w(f"field '{key}': SOURCE normalize failed '{raw_token}': {ex}")


    # Iconify pseudo-scheme: @icon://set/name or icon://set/name (optionally with ~ops)
    # This is the lightweight path used directly in dataset cells.
    if (not source_was_normalized) and sm is not None:
        rt_low = raw_token.lower().lstrip()
        if rt_low.startswith("@icon://") or rt_low.startswith("icon://"):
            main, sep, ops_tail = raw_token.partition("~")
            src_val = main.strip()
            # SourceManager accepts both icon://... and @icon://..., but we normalize.
            if src_val.lower().startswith("@icon://"):
                src_val = src_val[1:]
            try:
                sref = sm.register(src_val)
                raw_token = f"{sref.symbol_id}{sep}{ops_tail}" if sep else sref.symbol_id
                source_was_normalized = True
                _l.d(f"[deckmaker.src] normalized 'icon://' → '{raw_token}' (symbol in <defs>)")
            except Exception as ex:
                _l.w(f"field '{key}': icon:// normalize failed '{raw_token}': {ex}")
    # For non-text placeholders, normalized source tokens without explicit fit ops
    # should still go through FitAnchor with default behavior (inside+center).
    force_fa_default = False
    if source_was_normalized and symbol_id_for_fallback:
        has_fa_sig = ("~" in raw_token) or raw_token.endswith("=") or raw_token.endswith("+") or ("=~" in raw_token) or ("+~" in raw_token) or raw_token.lstrip().startswith('[')
        if (not header_plus) and (not has_fa_sig):
            force_fa_default = True

    is_fa_token = force_fa_default or header_plus or ("~" in raw_token) or raw_token.endswith("=") or raw_token.endswith("+") or ("=~" in raw_token) or ("+~" in raw_token) or raw_token.lstrip().startswith('[')
    if header_plus and ("~" not in raw_token):
        raw_token = raw_token + "~i"
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
        for tok in (tokens or []):
            tok = (tok or "").strip()
            if not tok:
                continue
            if tok.lstrip().startswith('['):
                try:
                    arr = _parse_array_token(tok)
                except Exception:
                    _l.w(f"[deckmaker.fa] placeholder '{key}': array token invalido '{tok}'")
                    continue
                if not arr or not arr.get('items'):
                    continue
                ops_body = (arr.get('ops') or "") or default_ops
                ops_full = f"~{ops_body}" if ops_body else "~"
                g_node, g_id = _build_array_group(inst, root_doc, arr.get('items'), arr.get('layout'), sm=sm)
                if g_id:
                    # Use deep-copy for arrays so the temp group can be removed safely.
                    fa_jobs.append((g_id, rect_id_val, ops_full, 'copy', g_node, rect_elem_for_fa))
                    queued += 1
                    _l.d(f"[deckmaker.fa] queued '{key}' -> base='{g_id}' rect='{rect_id_val}' place=copy ops='{ops_full or '~'}'")
                continue
            try:
                base_id, place, ops_tok = _parse_object_token(tok)
            except Exception:
                _l.w(f"[deckmaker.fa] placeholder '{key}': token invalido '{tok}'")
                continue
            ops_body = (ops_tok or "") or default_ops
            ops_full = f"~{ops_body}" if ops_body else "~"
            fa_jobs.append((base_id, rect_id_val, ops_full, place, None, rect_elem_for_fa))
            queued += 1
            _l.d(f"[deckmaker.fa] queued '{key}' -> base='{base_id}' rect='{rect_id_val}' place={place} ops='{ops_full or '~'}'")
        # Remove placeholder immediately only when it is NOT serving as the rect itself.
        if (queued > 0) and (not used_placeholder_as_rect):
            par = tgt.getparent()
            if par is not None:
                try:
                    par.remove(tgt)
                except Exception as ex:
                    _l.w(f"field '{key}': removing placeholder after enqueue fa failed: {ex}")

        return queued, "fa"
    if source_was_normalized and symbol_id_for_fallback and not is_fa_token:
        try:
            use_seq[0] += 1
            use_id = f"dm_srcuse_{use_seq[0]}"
            par = tgt.getparent()
            if par is None:
                _l.w(f"Target '{target_id}' has no parent; cannot insert source <use> '{use_id}'.")
                return 0, 'miss'
            u = SVG.etree.Element(inkex.addNS('use','svg'))
            u.set(inkex.addNS('href','xlink'), f"#{symbol_id_for_fallback}")
            par.insert(par.index(tgt) + 1, u)
            use_jobs.append((tgt, u))
            _l.d(f"field '{key}': SOURCE(use) id='{use_id}' symbol='{symbol_id_for_fallback}' [fallback center]")
            return 1, 'source'
        except Exception as ex:
            _l.w(f"field '{key}': SOURCE fallback <use> failed: {ex}")
    src_id = raw_token
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
    else:
        parent.insert(parent.index(tgt) + 1, u)
        use_jobs.append((tgt, u))
        _l.d(f"field '{key}': INSERT use id='{use_id}' wrap='{wrap_id}' (src_bbox w={bw:.2f} h={bh:.2f})")
        return 1, "clone"
    return 0, "miss"

def render_phase(ctx):
    root = ctx.root
    SM = ctx.SM
    ss_registry = getattr(ctx, 'spritesheets', None)
    ds_idx = ctx.ds_idx
    headers = ctx.headers
    rows_data = ctx.rows_data
    use_seq = ctx.use_seq
    next_n = ctx.next_n
    placed_total = ctx.placed_total
    start_page_index = ctx.start_page_index
    planner = ctx.planner
    pages = planner.pages  # list[dict] with {id,x,y,w,h,el}
    proto_root = ctx.proto_root
    out_layer = ctx.out_layer
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
            raise inkex.AbortExtension(f"Cursor de páginas inválido at='{expr}': {ex}")
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
                f"No hay huecos disponibles (slots) para colocar más cartas. "
                f"per_page={getattr(planner_obj.plan,'per_page',-1)} "
                f"page={planner_obj.page_index+1} slot_index={planner_obj.slot_index}"
            )
        return sx, sy
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
            # split by comma OR whitespace
            toks = [t for t in re.split(r"[\s,]+", body) if t]
            out = []
            for t in toks:
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

        def _parse_iter_seq(expr: str):
            """Parse an iterator expression (without star prefix) into list[str]."""
            s = (expr or '').strip()
            if not s:
                return ['']
            src_v, sel_v, _ops_v, _tag_v = _parse_source_token_with_selector(s)
            if src_v:
                v_urls = _resolve_virtual_source_urls(SM, src_v, sel_v, warn_tag=_virtual_warn_tag(src_v, "wkmc.iter"))
                if v_urls is not None:
                    ops_norm = _normalize_ops_chain(_ops_v or "")
                    if ops_norm:
                        return [f"{u}{ops_norm}" for u in v_urls]
                    return v_urls
            if s.startswith('[') and s.endswith(']'):
                return _parse_range_or_list(s)
            if s.startswith('@{') and s.endswith('}'):
                return _expand_glob_from_at_brace(s)
            ss = _expand_spritesheet_wildcard(s)
            if ss is not None:
                return ss
            # fallback: treat as a scalar token
            return [s]

        def _expand_row_iterators(row0: dict):
            """Return (expanded_rows, has_iterators)."""
            cells0 = row0.get('cells')
            if not isinstance(cells0, list):
                return [row0], False
            # detect max level
            max_lv = 0
            for c in cells0:
                st = (str(c or '').strip())
                if st.startswith('*'):
                    max_lv = max(max_lv, _count_leading_stars(st))
            if max_lv <= 0:
                return [row0], False

            level_cols = {}   # k -> list[idx]
            level_seqs = {}   # (k, idx) -> seq
            level_len = {}    # k -> maxlen

            for idx, c in enumerate(cells0):
                st = (str(c or '').strip())
                if not st.startswith('*'):
                    continue
                k = _count_leading_stars(st)
                expr = st[k:].strip()
                seq = _parse_iter_seq(expr)
                level_cols.setdefault(k, []).append(idx)
                level_seqs[(k, idx)] = seq

            for k in range(1, max_lv + 1):
                cols = level_cols.get(k, [])
                if not cols:
                    level_len[k] = 1
                    continue
                mlen = 1
                for idx in cols:
                    seq = level_seqs.get((k, idx), [''])
                    try:
                        mlen = max(mlen, len(seq))
                    except Exception:
                        pass
                level_len[k] = mlen

            # build nested loops via recursion
            out_rows = []
            idx_stack = [0] * (max_lv + 1)  # 1-based per level

            def _recur(level: int):
                if level > max_lv:
                    # materialize one instance
                    rr = dict(row0)
                    rr_cells = list(cells0)
                    for k in range(1, max_lv + 1):
                        cols = level_cols.get(k, [])
                        if not cols:
                            continue
                        i_k = idx_stack[k]
                        for col_idx in cols:
                            seq = level_seqs.get((k, col_idx), [''])
                            val = ''
                            try:
                                if i_k < len(seq):
                                    val = str(seq[i_k])
                            except Exception:
                                val = ''
                            rr_cells[col_idx] = val
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
                    cols = level_cols.get(k, [])
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
            n_iter = len(row_list) if has_iter else 1
            try:
                copies_decl = int(_row.get('__dm_copies__', 1) or 1)
            except Exception:
                copies_decl = 1
            copies_explicit = _as_bool(_row.get('__dm_copies_explicit__', False))
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
    def _fill_instance_fields(inst_node, row_inst, row_map, use_jobs, fa_jobs, *, clone_first: bool = False):
        """Apply dataset fields into a template instance, collecting <use> jobs and deferred fit-anchor jobs."""
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
                    root_doc=root, use_jobs=use_jobs, fa_jobs=fa_jobs,
                    use_seq=use_seq, sm=SM, ss_registry=ss_registry
                )

    def _exec_use_and_fa(inst_node, use_jobs, fa_jobs, *, warn_tag: str):
        """Center <use> elements over placeholders, then execute deferred Fit/Anchor jobs."""
        for placeholder, u in (use_jobs or []):
            try:
                _center_use_over_placeholder(u, placeholder)
            except Exception:
                pass
        _fa_remove_later = []
        for (base_id, r_id, ops_full, place_mode, placeholder_to_remove, rect_elem) in (fa_jobs or []):
            try:
                FA.apply_to_by_ids(inst_node, base_id, r_id, ops_full, place=place_mode, rect_elem=rect_elem)
                if placeholder_to_remove is not None:
                    _fa_remove_later.append(placeholder_to_remove)
            except Exception as ex:
                _l.w(f"{warn_tag} deferred fit-anchor failed base='{base_id}' rect='{r_id}': {ex}")
        return _fa_remove_later
    # --- end dedup helpers ---

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

        # Form 2: ops-only → bind to current slot/page.
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
                _l.w(f"[@page] invalid selector '{raw}' in col '{ckey or ''}' → skipped")
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
                _l.w(f"[@page @back] invalid selector '{raw}' in col '{ckey or ''}' → skipped")
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
            out_layer.append(tmp_group)
            ctx._pnpink_tmp_group = tmp_group
        inst = tmpl_root.copy()
        _flatten_group_transform(inst)
        suffix = f"_pnp{next_n}_pg"; next_n += 1
        SVG.uniquify_all_ids_in_scope(inst, suffix, root.get_unique_id)
        page_wrap = inkex.Group(); page_wrap.set('id', root.get_unique_id(f"dm_pagewrap_{ds_idx}"))
        page_wrap.append(inst)
        tmp_group.append(page_wrap)
        # Fill fields (same rules as normal templates)
        use_jobs = []; fa_jobs = []
        row_map = _build_row_map(headers, row_inst)

        # Phase-1: reset per-instance keep-visible set (populated by parse_header_key on headers with '+')
        global _P1_KEEP_SET
        _P1_KEEP_SET = set()

        _fill_instance_fields(inst, row_inst, row_map, use_jobs, fa_jobs, clone_first=False)
        # Execute <use> centering and deferred FA on the base instance (still in temp group)
        # DEBUG: collect rect/placeholder ids that FA will touch in this instance so we can detect early placeholder removals
        global _DBG_FA_RECT_IDS
        try:
            _DBG_FA_RECT_IDS = set()
            for (_b, _r, _ops, _pm, _ph_rm, _rect_elem) in (fa_jobs or []):
                if _ph_rm is not None:
                    _pid = _ph_rm.get('id') or ''
                    if _pid: _DBG_FA_RECT_IDS.add(_pid)
                if _rect_elem is not None:
                    _rid = _rect_elem.get('id') or ''
                    if _rid: _DBG_FA_RECT_IDS.add(_rid)
        except Exception:
            _DBG_FA_RECT_IDS = None
        _fa_remove_later = _exec_use_and_fa(inst, use_jobs, fa_jobs, warn_tag='[@page]')
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
                # parse_header_key also strips '+' and populates _P1_KEEP_SET
                _tid = (parse_header_key_full(_k).get('target_id') or '').strip()
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
                if _e is None or (not _is_rect_elem(_e)):
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
        try: _P1_KEEP_SET = None
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
                parent_elem=out_layer,
                insert_after_elem=(insert_after_elem if insert_after_elem is not None else None),
                bbox_elem=bbox_elem,
            )
        except Exception as ex:
            _l.w(f"[@page] fit_anchor failed for '{bid}' page={int(page_index)+1} ops='{ops}': {ex}")
            placed_node = None

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
                            # icon://name  → default set
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
    for idx, row in enumerate(_iter_instances(rows_data), start=1):
        _l.s(f"ROW {idx}: begin")
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
                _l.s(f"ROW {idx}: placeholder (empty row) → skip slot")
                planner.commit_slot()
                continue
        if row_page:
            _l.s(f"ROW {idx}: apply PAGE preset")
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
            _l.i(f"Grid {planner.plan.cols}x{planner.plan.rows}, gaps {planner.current.gaps.h}×{planner.current.gaps.v} mm; slots/page {planner.slots_per_page()}")
        if row_layout:
            _l.s(f"ROW {idx}: apply LAYOUT tail")
            try:
                ls = DSL.parse_layout_block(row_layout)
            except Exception as ex:
                _l.w(f"layout tail inválido '{row_layout}': {ex}")
                ls = None
            if ls is not None:
                page, card, layout, gaps = LYT.apply_layout_spec((page, card, layout, gaps), ls)
                current = _resolve_with_base(ctx, page, card, layout, gaps, doc_page_mm)
                _old_page_idx = int(planner.page_index)
                planner.apply_preset(current)
                if int(planner.page_index) != _old_page_idx:
                    _flush_marks_for_page(_old_page_idx)
                _l.i(f"Tail applied: g={layout.cols}x{layout.rows} inv=({layout.invert_cols},{layout.invert_rows}) rowMajor={layout.sweep_rows_first} k={gaps.h}×{gaps.v} s='{card.name or ''}'")
        if row_marks:
            _l.s(f"ROW {idx}: apply MARKS tail")
            if row_marks in ("0", "-"):
                marks_current = None
            else:
                try:
                    marks_current = DSL.parse_marks_block(row_marks)
                except Exception as ex:
                    _l.w(f"marks tail inválido '{row_marks}': {ex}")
        # Collect any @page requests from this row (they'll be executed when their referenced slot is reached)
        _queue_page_requests(row, row_map)

        slot_x, slot_y = _get_or_advance_slot(planner)

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
            'slot_in_page': int(planner.slot_index),
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
        card_group = inkex.Group()
        inst_main = deepcopy(proto_root)
        _flatten_group_transform(inst_main)
        suffix_main = f"_pnp{next_n}"; next_n += 1
        SVG.uniquify_all_ids_in_scope(inst_main, suffix_main, root.get_unique_id)
        inst_jobs = []  # list of dicts: {node, use_jobs, fa_jobs, suffix, bbox_id}
        inst_jobs.append({
            'node': inst_main,
            'use_jobs': [],
            'fa_jobs': [],
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
            inst_ov = deepcopy((ot or {}).get('template_root'))
            if inst_ov is None:
                continue
            _flatten_group_transform(inst_ov)
            suffix_ov = f"_pnp{next_n}_t{ot_i}"; next_n += 1
            SVG.uniquify_all_ids_in_scope(inst_ov, suffix_ov, root.get_unique_id)
            inst_jobs.append({
                'node': inst_ov,
                'use_jobs': [],
                'fa_jobs': [],
                'suffix': suffix_ov,
                'bbox_id': ((ot or {}).get('bbox_id') or ''),
                'overlay_ops': ctrl_val,
            })
        def _apply_field_any(key, raw):
            for j in inst_jobs:
                cnt, st = apply_field_in_clone(
                    j['node'], key, raw, row_map,
                    root_doc=root, use_jobs=j['use_jobs'], fa_jobs=j['fa_jobs'],
                    use_seq=use_seq, sm=SM, ss_registry=ss_registry
                )
                if st != 'miss':
                    return cnt, st
            return 0, 'miss'

        # Phase-1: per-row keep-visible set for rect anchors (populated by parse_header_key on headers with '+').
        # We reset it once per placed card so visibility decisions are deterministic and do not leak across rows.
        global _P1_KEEP_SET
        _P1_KEEP_SET = set()

        for key, raw in _iter_row_fields(headers, row):
            if not key or not key.startswith('clone_'):
                continue
            _apply_field_any(key, raw)
        for key, raw in _iter_row_fields(headers, row):
            if (not key) or key.startswith('clone_'):
                continue
            if key.startswith('__dm_') or key.startswith('_'):
                continue
            _apply_field_any(key, raw)
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
        _l.s(f"ROW {idx}: place card")
        placed_node = None
        if declared_bbox_node is not None and declared_bbox_id:
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
                    suffix_part = f"_pnp{next_n}_sb{part_idx}"; next_n += 1
                    SVG.uniquify_all_ids_in_scope(part, suffix_part, root.get_unique_id)
                    out_layer.append(part)
                    SVG.apply_clip_from_rect(
                        root,
                        part,
                        (src_x, src_y, tile_w, tile_h),
                        stage='split_board',
                        clip_id=f"clip{suffix_part}",
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
                    part_name = f"{proto_root.get('id','card')}_{page1}_split_{rr+1}_{cc+1}"
                    part.set('id', part_name)
                    part.set(inkex.addNS('label', 'inkscape'), part_name)
            placed += 1
            planner.commit_slot()
            continue

        try:
            _, _, slot_w, slot_h = planner.local_slots[planner.slot_index]
        except Exception:
            slot_w, slot_h = bw, bh
        out_layer.append(card_group)
        placed_node = card_group
        T_fit = SVG.transform_bbox_to_rect(
            bx=bx, by=by, bw=bw, bh=bh,
            dst_x=slot_x, dst_y=slot_y, dst_w=slot_w, dst_h=slot_h,
            fit='a',
            anchor=(0.0, 0.0),
            shift=(0.0, 0.0),
            rot_deg=0.0, mir_h=False, mir_v=False
        )
        try:
            curT = inkex.Transform(card_group.get('transform') or "")
        except Exception:
            curT = inkex.Transform()
        card_group.set('transform', str(T_fit @ curT))
        if marks_current is not None:
            try:
                ms = marks_current
                within = int(planner.slot_index)
                r0, c0 = _slot_index_to_rc(within, planner.plan, planner.current.layout)
                jobs = _marks_pending_by_page.setdefault(int(planner.page_index), [])
                jobs.append({
                    'ms': ms,
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
        # DEBUG: collect rect/placeholder ids that FA will touch in this ROW so we can detect early placeholder removals
        global _DBG_FA_RECT_IDS
        try:
            _DBG_FA_RECT_IDS = set()
            for _jdbg in (inst_jobs or []):
                for (_b, _r, _ops, _pm, _ph_rm, _rect_elem) in (_jdbg.get('fa_jobs') or []):
                    if _ph_rm is not None:
                        _pid = _ph_rm.get('id') or ''
                        if _pid: _DBG_FA_RECT_IDS.add(_pid)
                    if _rect_elem is not None:
                        _rid = _rect_elem.get('id') or ''
                        if _rid: _DBG_FA_RECT_IDS.add(_rid)
        except Exception:
            _DBG_FA_RECT_IDS = None
        for j in inst_jobs:
            for placeholder, u in (j.get('use_jobs') or []):
                try:
                    _center_use_over_placeholder(u, placeholder)
                except Exception as ex:
                    _l.w(f"Centering <use> failed for placeholder '{placeholder.get('id')}': {ex}")
        _l.s(f"ROW {idx}: fit-anchor")
        _fa_remove_later = []
        for j in inst_jobs:
            inst0 = j.get('node')
            for (base_id, r_id, ops_full, place_mode, placeholder_to_remove, rect_elem) in (j.get('fa_jobs') or []):
                # DEBUG: detect orphan rects (would force FA parent fallback to root)
                try:
                    if rect_elem is not None and rect_elem.getparent() is None:
                        _l.w(f"[dbg.fa_orphan] BEFORE apply base='{base_id}' r_id='{r_id}' rect_elem_id='{rect_elem.get('id')}' placeholder_rm='{(placeholder_to_remove.get('id') if placeholder_to_remove is not None else None)}'")
                    elif rect_elem is not None:
                        _l.d(f"[dbg.fa_parent] BEFORE apply base='{base_id}' r_id='{r_id}' rect_elem_id='{rect_elem.get('id')}' rect_parent='{(rect_elem.getparent().get('id') if rect_elem.getparent() is not None else None)}'")
                except Exception:
                    pass
                try:
                    FA.apply_to_by_ids(inst0, base_id, r_id, ops_full, place=place_mode, rect_elem=rect_elem)
                    if placeholder_to_remove is not None:
                        _fa_remove_later.append(placeholder_to_remove)
                except Exception as ex:
                    _l.w(f"[deckmaker.fa] EXEC FAILED base='{base_id}' rect='{r_id}' ops='{ops_full or '~'}': {ex}")
        # Remove placeholders at the end so the same rect can be reused (multivalue/dup headers).
        for _ph in list(dict.fromkeys(_fa_remove_later)):
            try:
                par = _ph.getparent()
                if par is not None:
                    if not _is_rect_elem(_ph):

                        par.remove(_ph)
            except Exception:
                pass

        # Phase-1: hide rect anchors at the end of the row, unless any duplicate header for that base id used '+'.
        # This must happen AFTER all bbox measurements / placements.
        try:
            _keep = _P1_KEEP_SET if isinstance(_P1_KEEP_SET, set) else set()
        except Exception:
            _keep = set()

        # Collect candidate anchor ids from headers (base ids only, parse_header_key strips '+').
        _rect_ids = set()
        try:
            for _k, _raw in _iter_row_fields(headers, row):
                if not _k or _k.startswith('__dm_') or _k.startswith('_'):
                    continue
                _tid = (parse_header_key_full(_k).get('target_id') or '').strip()
                if _tid:
                    _rect_ids.add(_tid)
        except Exception:
            pass

        def _apply_anchor_visibility(_scope):
            for _rid in _rect_ids:
                try:
                    _e = SVG.find_target_exact_in(_scope, _rid)
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
                    if _e is None or (not _is_rect_elem(_e)):
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
        try:
            for _j in (inst_jobs or []):
                _scope = _j.get('node')
                if _scope is not None:
                    _apply_anchor_visibility(_scope)
        except Exception:
            pass

        # Clear per-row keep-visible set to avoid leaking across rows.
        try:
            _P1_KEEP_SET = None
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
        new_name = f"{proto_root.get('id','card')}_{page1}_{row1}_{col1}"
        placed_node.set('id', new_name)
        placed_node.set(inkex.addNS('label','inkscape'), new_name)
        placed += 1
        planner.commit_slot()
        try:
            holes = _coerce_holes(row.get('__dm_holes__', []) or [])
            cur_copy_idx = int(row.get('_i', 0) or 0) + 1
            n_extra = holes.count(cur_copy_idx) if holes else 0
            if n_extra > 0:
                for _ in range(n_extra):
                    planner.commit_slot()
                _l.d(f"[holes] extra slots={n_extra} after copy={cur_copy_idx} row={idx}")
        except Exception:
            pass
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
                    inst = deepcopy(tmpl_root)
                    _flatten_group_transform(inst)
                    suffix = f"_pnp{next_n}_b{bt_i}"; next_n += 1
                    SVG.uniquify_all_ids_in_scope(inst, suffix, root.get_unique_id)

                    use_jobs = []
                    fa_jobs = []
                    _fill_instance_fields(inst, row, row_map, use_jobs, fa_jobs, clone_first=False)

                    _fa_remove_later = _exec_use_and_fa(inst, use_jobs, fa_jobs, warn_tag='[@back]')
                    for _ph in list(dict.fromkeys(_fa_remove_later)):
                        try:
                            par = _ph.getparent()
                            if par is not None:
                                if not _is_rect_elem(_ph):

                                    par.remove(_ph)
                        except Exception:
                            pass

                    card_group.append(inst)
                    out_layer.append(card_group)

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
                    card_group.set('transform', str(T_fit @ curT))
                    placed_back += 1
# Any remaining pending @page requests that referenced slots beyond the placed range are ignored.
    if pending_page_req:
        _l.w(f"[@page] ignored {sum(len(v) for v in pending_page_req.values())} pending requests: slot ref out of range")
        pending_page_req.clear()
    if pending_page_back_req:
        _l.w(f"[@page @back] ignored {sum(len(v) for v in pending_page_back_req.values())} pending requests: slot ref out of range")
        pending_page_back_req.clear()

    if placed_back > 0:
        try:
            n_back_pages = len([p for p in planner.pages if (p.get('id') or '').startswith('dm_page_')])
        except Exception:
            n_back_pages = 0
        _l.i(f"[@back] placed={placed_back} instances; skipped={skipped_back_instances}; back_pages_created={n_back_pages}")
    _l.i(f"[datasets] #{ds_idx}: placed={placed} cards; end_page={planner.page_index+1}")
    placed_total += placed
    start_page_index = planner.page_index + 1
    ctx.next_n = next_n
    ctx.placed_total = placed_total
    ctx.start_page_index = start_page_index
