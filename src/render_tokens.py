# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Optional, Tuple

import log as LOG
import dsl as DSL
import transform_fx as TFX

_l = LOG

_ALIAS_TOKEN_RE = re.compile(
    r"^@(?P<name>[A-Za-z][\w\-\.]*)((?:\[[^\]]+\])+)"
    r"(?:(?:~(?P<ops_tilde>.*))|(?P<ops_compact>[\^!\|].*))?$"
)


def expand_index_expr(expr: str):
    s = (expr or "").strip()
    if not s:
        return []
    if "," in s:
        raise ValueError("No se permiten comas en índices")
    toks = s.split()
    out = []
    for t in toks:
        if t == "*":
            out.append("*")
            continue
        if t == "-":
            out.append(None)
            continue
        m = re.match(r"^(\d+)-$", t)
        if m:
            out.extend([None] * int(m.group(1)))
            continue
        m = re.match(r"^(\d+)\*(\d+)$", t)
        if m:
            k = int(m.group(1))
            v = int(m.group(2))
            out.extend([v] * k)
            continue
        m = re.match(r"^(\d+)-(\d+)$", t)
        if m:
            a = int(m.group(1))
            b = int(m.group(2))
            step = 1 if b >= a else -1
            out.extend(list(range(a, b + step, step)))
            continue
        if re.match(r"^\d+$", t):
            out.append(int(t))
            continue
        raise ValueError(f"Índice no reconocido: '{t}'")
    return out


def parse_sprite_alias_token(raw_token: str):
    m = _ALIAS_TOKEN_RE.match((raw_token or "").strip())
    if not m:
        return None
    name = m.group("name")
    idxs_raw = m.group(2) or ""
    ops = (m.group("ops_tilde") or m.group("ops_compact") or "").strip()
    inner = idxs_raw[1:-1]
    chunks = inner.split("][") if inner else []
    dims = []
    for ch in chunks:
        try:
            dims.append(expand_index_expr(ch))
        except Exception as ex:
            _l.w(f"[spritesheets] token '{raw_token}': invalid index expression '[{ch}]' ({ex})")
            return None
    return name, dims, ops


def parse_object_token(token: str) -> Tuple[str, str, str]:
    m = re.match(r"""
        ^(?P<id>[A-Za-z_][-A-Za-z0-9_:.]*\*?)
        (?P<mode>[=+])?
        (?:
            ~(?P<ops_tilde>.+)
          |
            (?P<ops_compact>[\^!\|].*)
        )?
        \s*$
    """, token or "", re.VERBOSE)
    if not m:
        raise ValueError(f"Invalid object token: '{token}'")
    base_id = m.group("id")
    mod = m.group("mode")
    ops = (m.group("ops_tilde") or m.group("ops_compact") or "")
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
    tail = ""
    m_tail = re.match(
        r"^(?P<core>.*?)(?P<tail>(?:\.(?:Fit)\s*\{[^}]*\}|~.*|[\^!\|].*))?\s*$",
        s,
        re.IGNORECASE,
    )
    if m_tail:
        s = (m_tail.group("core") or "").strip()
        tail = (m_tail.group("tail") or "").strip()
    specs = []
    rx = re.compile(r"^(?P<base>.*?)(?P<mod>\.(?:Transform|T)\s*\{[^{}]*\})\s*$", re.IGNORECASE)
    while True:
        m = rx.match(s)
        if not m:
            break
        mod_txt = (m.group("mod") or "").strip()
        try:
            ch = DSL.maybe_parse_chain("X" + mod_txt)
        except Exception:
            ch = None
        if ch is None:
            break
        mod = next((mm for mm in (getattr(ch, "modules", None) or []) if str(getattr(mm, "name", "")).lower() in ("transform", "t")), None)
        if mod is None or getattr(mod, "spec", None) is None:
            break
        specs.insert(0, getattr(mod, "spec"))
        s = (m.group("base") or "").strip()
    out = s
    if tail:
        out = (out + tail).strip()
    return out, TFX.merge_specs(specs)


def fit_suffix_to_ops(fit_suffix: str) -> str:
    s = (fit_suffix or "").strip()
    if not s:
        return ""
    try:
        fit_cmd = DSL.parse(f"X.{s}")
        fs = getattr(fit_cmd, "fit", None)
        return DSL.ops_from_fit_spec(fs) if fs else ""
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
        r'(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$',
        s,
        re.IGNORECASE,
    )
    tag = "@{...}"
    if not m_all:
        m_all = re.match(
            r'^\s*(?:Source|S)\s*\{\s*(?P<body>[^}]*)\s*\}\s*'
            r'(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$',
            s,
            re.IGNORECASE,
        )
        tag = "Source{...}"
    if m_all:
        body_for_dsl = (m_all.group("body") or "").strip()
        src_val = None
        body_low = body_for_dsl.lower()
        if body_low.startswith("osm://") or body_low.startswith("ofm://"):
            src_val = body_for_dsl
        else:
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
        compact_ops = m_all.group("ops_compact")
        if fit_text:
            try:
                fit_cmd = DSL.parse(f"X.{fit_text}")
                fs = getattr(fit_cmd, "fit", None)
                ops = DSL.ops_from_fit_spec(fs) if fs else ""
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
        r"^\s*(?P<url>https?://\S+?)\s*(?:(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$",
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

    m_local = re.match(
        r"^\s*(?P<sigil>@)?(?P<path>[^\s\[\]~]+?\.(?:png|jpe?g|gif|bmp|webp|svgz?|pdf|tiff?))\s*"
        r"(?:(?:\.(?P<fit>Fit\s*\{[^}]*\}))|(?:~(?P<ops>.*))|(?P<ops_compact>[\^!\|].*))?\s*$",
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
                fit_cmd = DSL.parse(f"X.{fit_text}")
                fs = getattr(fit_cmd, "fit", None)
                ops = DSL.ops_from_fit_spec(fs) if fs else ""
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
        r"^\s*(?P<core>(?:@\{[^}]*\}|(?:Source|S)\s*\{[^}]*\}|https?://\S+?))\s*"
        r"(?P<sel>\[[^\]]*\])?\s*"
        r"(?P<tail>(?:\.(?:Fit)\s*\{[^}]*\}|~.*|[\^!\|].*)?)\s*$",
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
    except Exception:
        pass
    if frag:
        return [f"@{{{u}#{frag}}}" for u in urls]
    return [f"@{{{u}}}" for u in urls]


__all__ = [
    "expand_index_expr",
    "parse_sprite_alias_token",
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
