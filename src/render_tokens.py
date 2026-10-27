# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shlex
from typing import Optional, Tuple

import log as LOG
import dsl as DSL
import transform_fx as TFX

_l = LOG


def unquote_iterator_item(value: str) -> tuple[str, bool]:
    text = str(value or "").strip()
    if len(text) < 2 or text[0] not in ('"', "'") or text[-1] != text[0]:
        return text, False
    try:
        parsed = shlex.split(text, posix=True)
        if len(parsed) == 1:
            return parsed[0], True
    except ValueError:
        pass
    return text[1:-1], True


excel_col_to_num = DSL.excel_col_to_num


def _excel_col_name(number: int) -> str:
    value = max(1, int(number or 1))
    out = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        out = chr(65 + remainder) + out
    return out


def _spritesheet_frame_cell(frame: int, definition) -> str:
    cols = max(0, int(getattr(definition, "cols", 0) or 0))
    rows = max(0, int(getattr(definition, "rows", 0) or 0))
    offset = max(0, min(cols * rows - 1, int(frame) - 1))
    if bool(getattr(definition, "sweep_rows_first", True)):
        row = offset // cols
        col = offset % cols
    else:
        col = offset // rows
        row = offset % rows
    if bool(getattr(definition, "invert_rows", False)):
        row = rows - 1 - row
    if bool(getattr(definition, "invert_cols", False)):
        col = cols - 1 - col
    return f"{_excel_col_name(col + 1)}{row + 1}"


def cell_ref_to_rc(ref: str):
    m = re.fullmatch(r"([A-Za-z]+)([1-9]\d*)", str(ref or "").strip())
    if not m:
        return None
    c = excel_col_to_num(m.group(1))
    return (int(c), int(m.group(2))) if c is not None else None


def grid_rc_to_index_1based(c1: int, r1: int, cols: int, rows: int, *, sweep_rows_first=True, invert_cols=False, invert_rows=False):
    cols = int(cols or 0)
    rows = int(rows or 0)
    c0 = int(c1) - 1
    r0 = int(r1) - 1
    if cols <= 0 or rows <= 0 or not (0 <= c0 < cols and 0 <= r0 < rows):
        return None
    if invert_rows:
        r0 = (rows - 1) - r0
    if invert_cols:
        c0 = (cols - 1) - c0
    return (r0 * cols + c0 + 1) if sweep_rows_first else (c0 * rows + r0 + 1)


def spritesheet_selector_frames(selector: str | DSL.SpriteSelector, definition) -> list[tuple[int, int]]:
    """Resolve the DSL selector AST into ordered ``(page, linear_frame)`` pairs."""
    cols = max(0, int(getattr(definition, "cols", 0) or 0))
    rows = max(0, int(getattr(definition, "rows", 0) or 0))
    pages = max(1, int(getattr(definition, "page_count", 1) or 1))
    per_page = cols * rows
    if per_page <= 0:
        return []

    def endpoint(reference: DSL.SpriteCellRef, default_page: int = 1):
        page = int(reference.page or default_page)
        if not 1 <= page <= pages:
            return None
        if reference.cell.isdigit():
            frame = int(reference.cell)
        else:
            rc = cell_ref_to_rc(reference.cell)
            if rc is None:
                return None
            frame = grid_rc_to_index_1based(
                rc[0], rc[1], cols, rows,
                sweep_rows_first=bool(getattr(definition, "sweep_rows_first", True)),
                invert_cols=bool(getattr(definition, "invert_cols", False)),
                invert_rows=bool(getattr(definition, "invert_rows", False)),
            )
        if frame is None or not 1 <= int(frame) <= per_page:
            return None
        return page, int(frame)

    parsed = selector if isinstance(selector, DSL.SpriteSelector) else DSL.parse_spritesheet_selector(selector)
    result = []
    for term in parsed.terms:
        if term.kind == "all":
            result.extend((page, frame) for page in range(1, pages + 1) for frame in range(1, per_page + 1))
            continue
        if term.kind == "linear":
            start = endpoint(term.start)
            end = endpoint(term.end, start[0] if start else 1)
            if start is None or end is None:
                continue
            first = (start[0] - 1) * per_page + start[1]
            last = (end[0] - 1) * per_page + end[1]
            step = 1 if last >= first else -1
            for absolute in range(first, last + step, step):
                result.append(((absolute - 1) // per_page + 1, (absolute - 1) % per_page + 1))
            continue
        if term.kind == "rectangle":
            start = endpoint(term.start)
            end = endpoint(term.end, start[0] if start else 1)
            if start is None or end is None or start[0] != end[0]:
                continue
            start_rc = cell_ref_to_rc(term.start.cell)
            end_rc = cell_ref_to_rc(term.end.cell)
            if start_rc is None or end_rc is None:
                continue
            frames = []
            for row in range(min(start_rc[1], end_rc[1]), max(start_rc[1], end_rc[1]) + 1):
                for col in range(min(start_rc[0], end_rc[0]), max(start_rc[0], end_rc[0]) + 1):
                    frame = grid_rc_to_index_1based(
                        col, row, cols, rows,
                        sweep_rows_first=bool(getattr(definition, "sweep_rows_first", True)),
                        invert_cols=bool(getattr(definition, "invert_cols", False)),
                        invert_rows=bool(getattr(definition, "invert_rows", False)),
                    )
                    if frame is not None:
                        frames.append(frame)
            result.extend((start[0], int(frame)) for frame in sorted(frames))
            continue
        single = endpoint(term.start)
        if single is not None:
            result.append(single)
    return result


def spritesheet_selector_tokens(alias: str, selector: str | DSL.SpriteSelector, definition) -> list[str]:
    pages = max(1, int(getattr(definition, "page_count", 1) or 1))
    return [
        f"@{alias}[{page}!{_spritesheet_frame_cell(frame, definition)}]"
        if pages > 1 else f"@{alias}[{_spritesheet_frame_cell(frame, definition)}]"
        for page, frame in spritesheet_selector_frames(selector, definition)
    ]


def spritesheet_wildcard_tokens(alias: str, definition) -> list[str]:
    return spritesheet_selector_tokens(alias, "*", definition)


def split_multivalue(value: str):
    out = []
    current = []
    depth_brace = depth_paren = depth_bracket = 0
    quote = None
    for ch in value or "":
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            current.append(ch)
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
            depth_bracket += 1
        elif ch == ']':
            depth_bracket = max(0, depth_bracket - 1)
        if ch.isspace() and depth_brace == 0 and depth_paren == 0 and depth_bracket == 0:
            token = "".join(current).strip()
            if token:
                out.append(token)
            current = []
            continue
        current.append(ch)
    token = "".join(current).strip()
    if token:
        out.append(token)
    return out


def parse_object_token(token: str) -> Tuple[str, str, str]:
    m = re.match(r"""
        ^(?P<id>[A-Za-z_][-A-Za-z0-9_:.]*\*?(?:\[[A-Za-z_][A-Za-z0-9_-]*\])?)
        (?P<mode>[=+])?
        (?:
            ~(?P<ops_tilde>.+)
          |
            (?P<ops_fit>\.(?:Fit|F)\s*\{[^}]*\})
          |
            (?P<ops_compact>[\^!\|].*)
        )?
        \s*$
    """, token or "", re.VERBOSE | re.IGNORECASE)
    if not m:
        raise ValueError(f"Invalid object token: '{token}'")
    base_id = m.group("id")
    mod = m.group("mode")
    ops = (m.group("ops_tilde") or m.group("ops_compact") or "")
    if m.group("ops_fit"):
        ops = fit_suffix_to_ops(m.group("ops_fit")).removeprefix("~")
    place = "clone" if mod is None else ("copy" if mod == "=" else "clone+unlink")
    return base_id, place, ops


def split_paths_suffix(token: str) -> Tuple[str, str]:
    s = str(token or "").strip()
    if not s:
        return "", ""
    m = re.match(r"^(?P<core>.*?)\.(?:P)\s*(?P<body>\{.*\})\s*$", s)
    if not m:
        return s, ""
    return (m.group("core") or "").strip(), ("P" + (m.group("body") or "")).strip()


def split_transform_suffixes(token: str):
    s = str(token or "").strip()
    if not s:
        return "", None
    specs = []
    rx = re.compile(r"^(?P<base>.*?)(?P<mod>\.(?:Transform|T)\s*\{[^{}]*\})\s*$", re.IGNORECASE)

    def _peel_transform_suffixes(value: str) -> str:
        current = value
        while True:
            match = rx.match(current)
            if not match:
                break
            mod_txt = (match.group("mod") or "").strip()
            try:
                chain = DSL.maybe_parse_chain("X" + mod_txt)
            except Exception:
                chain = None
            if chain is None:
                break
            module = next(
                (
                    item for item in (getattr(chain, "modules", None) or [])
                    if str(getattr(item, "name", "")).lower() in ("transform", "t")
                ),
                None,
            )
            if module is None or getattr(module, "spec", None) is None:
                break
            specs.insert(0, getattr(module, "spec"))
            current = (match.group("base") or "").strip()
        return current

    s = _peel_transform_suffixes(s)
    tail = ""
    m_tail = re.match(
        r"^(?P<core>.*?)(?P<tail>(?:\.(?:Fit|F)\s*\{[^}]*\}|~.*|[\^!\|].*))?\s*$",
        s,
        re.IGNORECASE,
    )
    if m_tail:
        s = (m_tail.group("core") or "").strip()
        tail = (m_tail.group("tail") or "").strip()
    s = _peel_transform_suffixes(s)
    out = s
    if tail:
        out = (out + tail).strip()
    return out, TFX.merge_specs(specs)


def fit_suffix_to_ops(fit_suffix: str) -> str:
    s = (fit_suffix or "").strip()
    if not s:
        return ""
    try:
        return DSL.ops_from_fit_spec(DSL.parse_fit_suffix(s))
    except Exception:
        return ""


def merge_fit_ops(prefix_ops: str, suffix_ops: str) -> str:
    a = (prefix_ops or "").strip()
    b = (suffix_ops or "").strip()
    if not a:
        return b
    if not b:
        return a
    try:
        fa = DSL.fit_spec_from_ops(a if a.startswith("~") else "~" + a)
        fb = DSL.fit_spec_from_ops(b if b.startswith("~") else "~" + b)
    except Exception:
        aa = a[1:] if a.startswith("~") else a
        bb = b[1:] if b.startswith("~") else b
        return "~" + aa + bb
    out = DSL.FitSpec()
    out.mode = fa.mode
    out.anchor = fa.anchor
    out.border = fa.border[:] if fa.border else None
    out.shift = fa.shift[:] if fa.shift else None
    out.rotate = fa.rotate
    out.mirror = fa.mirror
    out.clip = fa.clip
    out.clip_stage = fa.clip_stage
    if fb.mode is not None:
        out.mode = fb.mode
    if fb.anchor is not None:
        out.anchor = fb.anchor
    if fb.border is not None:
        out.border = fb.border[:] if fb.border else None
    if fb.shift is not None:
        out.shift = fb.shift[:] if fb.shift else None
    if fb.rotate is not None:
        out.rotate = (out.rotate or 0.0) + fb.rotate
    if fb.mirror is not None:
        out.mirror = fb.mirror
    if fb.clip is not None:
        out.clip = fb.clip
        out.clip_stage = fb.clip_stage
    return DSL.ops_from_fit_spec(out) or ""


def normalize_ops_chain(ops: str) -> str:
    s = (ops or "").strip()
    if not s:
        return ""
    had_leading = s.startswith("~")
    if had_leading:
        s = s[1:]
    parts = [p for p in s.split("~") if p.strip()]
    if not parts:
        return "~" if had_leading else ""

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
        body = DSL.ops_from_fit_spec(fs) or ""
        if not body:
            return "~" if had_leading else ""
        if body.startswith("~"):
            return body
        return ("~" + body) if had_leading else body

    merged = ""
    for p in parts:
        merged = merge_fit_ops(merged, "~" + p)
    return merged or ("~" if had_leading else "")


def parse_index_selector_1based(sel: str, size: int | None = None) -> list:
    return list(DSL.parse_index_selector_1based(sel, size=size) or [])


def select_1based_with_warning(items: list, selector: str, warn_tag: str) -> list:
    arr = list(items or [])
    idxs = parse_index_selector_1based(selector, size=len(arr))
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


def parse_source_like_token(raw_token: str):
    s = (raw_token or "").strip()
    if not s:
        return None, "", ""

    m_all = re.match(
        r'^\s*@\{\s*(?P<body>[^}]*)\s*\}\s*'
        r'(?:(?:\.(?P<fit>(?:Fit|F)\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$',
        s,
        re.IGNORECASE,
    )
    tag = "@{...}"
    if not m_all:
        m_all = re.match(
            r'^\s*(?:Source|S)\s*\{\s*(?P<body>[^}]*)\s*\}\s*'
            r'(?:(?:\.(?P<fit>(?:Fit|F)\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$',
            s,
            re.IGNORECASE,
        )
        tag = "Source{...}"
    if m_all:
        body_for_dsl = (m_all.group("body") or "").strip()
        src_val = None
        body_low = body_for_dsl.lower()
        if body_low.startswith("osm://") or body_low.startswith("ofm://") or body_low.startswith("pnp://") or body_low.startswith("gdrive://"):
            src_val = body_for_dsl
        else:
            try:
                cmd = DSL.maybe_parse(f"@{{{body_for_dsl}}}")
            except Exception:
                cmd = None
            if cmd and getattr(cmd, "name", None) == "Source" and getattr(cmd, "target", None) is not None:
                try:
                    args = dict(getattr(cmd.target, "args", {}) or {}) if hasattr(cmd.target, "args") else {}
                    src_val = args.get("src")
                    if not src_val:
                        src_val = getattr(cmd.target, "src", None)
                    if src_val and str(src_val).lower().startswith(("osm://", "ofm://")):
                        view_val = args.get("view") if args.get("view") not in (None, "") else args.get("v")
                        if view_val not in (None, ""):
                            src_val = f"{src_val} view={view_val}"
                except Exception:
                    src_val = None
        if not src_val:
            src_val = body_for_dsl

        fit_text = m_all.group("fit")
        legacy_ops = m_all.group("ops")
        compact_ops = m_all.group("ops_compact")
        if fit_text:
            try:
                ops = DSL.ops_from_fit_spec(DSL.parse_fit_suffix(fit_text))
            except Exception:
                ops = ""
        elif legacy_ops:
            ops = f"~{legacy_ops.strip()}"
        elif compact_ops:
            ops = f"~{compact_ops.strip()}"
        else:
            ops = ""
        ops = normalize_ops_chain(ops)
        return src_val, ops, tag

    m_url = re.match(
        r"^\s*(?P<url>(?:https?://|wkmc://|pxby://|oclp://|pnp://|gdrive://|osm://|ofm://)\S+?)\s*(?:(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$",
        s,
        re.IGNORECASE,
    )
    if m_url:
        url = (m_url.group("url") or "").strip()
        ops = (m_url.group("ops") or "").strip()
        ops_compact = (m_url.group("ops_compact") or "").strip()
        if ops:
            ops = f"~{ops}"
        elif ops_compact:
            ops = f"~{ops_compact}"
        else:
            ops = ""
        ops = normalize_ops_chain(ops)
        return url, ops, "url"

    m_local_quoted = re.match(
        r"^\s*(?P<q>['\"])(?P<path>.+?\.(?:png|jpe?g|gif|bmp|webp|svgz?|pdf|tiff?))(?P=q)\s*"
        r"(?:(?:\.(?P<fit>(?:Fit|F)\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$",
        s,
        re.IGNORECASE,
    )
    if m_local_quoted:
        src_val = (m_local_quoted.group("path") or "").strip()
        fit_text = m_local_quoted.group("fit")
        legacy_ops = m_local_quoted.group("ops")
        compact_ops = m_local_quoted.group("ops_compact")
        if fit_text:
            try:
                ops = DSL.ops_from_fit_spec(DSL.parse_fit_suffix(fit_text))
            except Exception:
                ops = ""
        elif legacy_ops:
            ops = f"~{legacy_ops.strip()}"
        elif compact_ops:
            ops = f"~{compact_ops.strip()}"
        else:
            ops = ""
        ops = normalize_ops_chain(ops)
        return src_val, ops, "file"

    m_local = re.match(
        r"^\s*(?P<sigil>@)?(?P<path>[^\s\[\]~]+?\.(?:png|jpe?g|gif|bmp|webp|svgz?|pdf|tiff?))\s*"
        r"(?:(?:\.(?P<fit>(?:Fit|F)\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$",
        s,
        re.IGNORECASE,
    )
    if m_local:
        src_val = (m_local.group("path") or "").strip()
        fit_text = m_local.group("fit")
        legacy_ops = m_local.group("ops")
        compact_ops = m_local.group("ops_compact")
        if fit_text:
            try:
                ops = DSL.ops_from_fit_spec(DSL.parse_fit_suffix(fit_text))
            except Exception:
                ops = ""
        elif legacy_ops:
            ops = f"~{legacy_ops.strip()}"
        elif compact_ops:
            ops = f"~{compact_ops.strip()}"
        else:
            ops = ""
        ops = normalize_ops_chain(ops)
        return src_val, ops, ("@file" if (m_local.group("sigil") or "") else "file")
    return None, "", ""


def parse_source_token_with_selector(raw_token: str):
    s = (raw_token or "").strip()
    m = re.match(
        r"^\s*(?P<core>(?:@\{[^}]*\}|(?:Source|S)\s*\{[^}]*\}|(?:https?://|wkmc://|pxby://|oclp://|pnp://|gdrive://|osm://|ofm://)\S+?))\s*"
        r"(?P<sel>\[[^\]]*\])?\s*"
        r"(?P<tail>(?:\.(?:Fit|F)\s*\{[^}]*\}|~.*|[\^!\|].*)?)\s*$",
        s,
        re.IGNORECASE,
    )
    if not m:
        src_val, ops, tag = parse_source_like_token(s)
        if src_val:
            return src_val, None, ops, tag
        return None, None, "", ""
    core = (m.group("core") or "").strip()
    sel = (m.group("sel") or "").strip() or None
    tail = (m.group("tail") or "").strip()
    src_val, ops, tag = parse_source_like_token(core + tail)
    return src_val, sel, ops, tag


def virtual_warn_tag(src_val: str, base_tag: str) -> str:
    s = (src_val or "").strip().lower()
    if s.startswith("pxby://"):
        return (base_tag or "pxby").replace("wkmc", "pxby")
    if s.startswith("oclp://"):
        return (base_tag or "oclp").replace("wkmc", "oclp")
    if s.startswith("osm://"):
        return (base_tag or "osm").replace("wkmc", "osm")
    if s.startswith("ofm://"):
        return (base_tag or "ofm").replace("wkmc", "ofm")
    if s.startswith("pnp://"):
        return (base_tag or "pnp").replace("wkmc", "pnp")
    if s.startswith("gdrive://") or "drive.google.com" in s:
        return (base_tag or "gdrive").replace("wkmc", "gdrive")
    return base_tag


def resolve_virtual_source_urls(sm, src_val: str, selector: Optional[str], *, warn_tag: str) -> Optional[list]:
    if sm is None:
        return None
    s = (src_val or "").strip()
    frag = ""
    sl0 = s.lower()
    hash_is_part_of_source = sl0.startswith("osm://#map=")
    if ("#" in s) and (not hash_is_part_of_source):
        s0, f0 = s.rsplit("#", 1)
        if s0.strip() and f0.strip():
            s = s0.strip()
            frag = f0.strip()
    sl = s.lower()
    if sl.startswith("wkmc://"):
        urls = list(sm.resolve_wkmc_urls(s) or [])
    elif sl.startswith("pxby://"):
        urls = list(sm.resolve_pxby_urls(s) or [])
    elif sl.startswith("oclp://"):
        urls = list(sm.resolve_oclp_urls(s) or [])
    elif sl.startswith("pnp://"):
        urls = list(sm.resolve_pnp_urls(s) or [])
    elif sl.startswith("gdrive://") or "drive.google.com" in sl:
        urls = list(sm.resolve_gdrive_urls(s) or [])
    elif sl.startswith("osm://"):
        urls = list(sm.resolve_osm_urls(s) or [])
        if not urls:
            urls = [s]
    elif sl.startswith("ofm://"):
        urls = [s]
    else:
        return None
    urls = select_1based_with_warning(urls, selector or "", warn_tag)
    try:
        if urls:
            sm.prefetch_urls(urls)
            if sl.startswith("gdrive://") or "drive.google.com" in sl:
                sm.prefetch_gdrive_files(urls)
    except Exception:
        pass
    if frag:
        return [f"@{{{u}#{frag}}}" for u in urls]
    return [f"@{{{u}}}" for u in urls]


__all__ = [
    "spritesheet_selector_frames",
    "spritesheet_selector_tokens",
    "split_multivalue",
    "parse_object_token",
    "split_paths_suffix",
    "split_transform_suffixes",
    "fit_suffix_to_ops",
    "merge_fit_ops",
    "normalize_ops_chain",
    "parse_index_selector_1based",
    "select_1based_with_warning",
    "parse_source_like_token",
    "parse_source_token_with_selector",
    "virtual_warn_tag",
    "resolve_virtual_source_urls",
]
