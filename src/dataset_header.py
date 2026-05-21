# -*- coding: utf-8 -*-
"""Dataset header helpers.

This module owns user-facing dataset header normalization:
  - template declaration columns: {template_id @page @back}
  - Inkscape label aliases: "visible label" -> SVG id
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Set, Tuple

import const as CONST

_VALID_MODS = {"@page", "@back"}


def parse_template_header_cell(cell: str) -> Optional[Dict]:
    s = (cell or "").strip()
    m = re.fullmatch(r"\{\s*(.*?)\s*\}", s)
    if not m:
        return None
    body = (m.group(1) or "").strip()
    if not body:
        return None

    # Split tokens (space-separated). Keep this intentionally simple.
    toks = [t for t in re.split(r"\s+", body) if t]

    bbox_id = None
    mods: Set[str] = set()
    for t in toks:
        if t in _VALID_MODS:
            mods.add(t)
            continue
        if t.startswith("@"):
            # Unknown modifier: ignore for forward compatibility.
            continue
        if bbox_id is None:
            m2 = re.fullmatch(r"(?:t|template_bbox)\s*=\s*([A-Za-z][A-Za-z0-9_.-]*)", t)
            if m2:
                bbox_id = m2.group(1)
            elif re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", t):
                bbox_id = t
            else:
                return None
        else:
            # Extra non-modifier tokens are not supported.
            return None

    if not bbox_id:
        return None
    return {"bbox_id": bbox_id, "mods": mods}


def extract_template_columns(headers: List[str], key_prefix: str = "__dm_tcol__") -> Tuple[List[str], List[Dict]]:
    """Normalize headers and extract declared template columns.

    Returns (headers_norm, template_cols), where template_cols are dicts:
      {bbox_id, key, col_index, mods:[...]}.
    """
    headers_norm = list(headers or [])
    cols = []
    used_keys = set()
    for i, h in enumerate(headers_norm):
        info = parse_template_header_cell(h)
        if not info:
            continue
        bid = info["bbox_id"]
        mods = sorted(list(info.get("mods") or []))

        key = f"{key_prefix}{bid}"
        if key in used_keys:
            n = 2
            while f"{key}_{n}" in used_keys:
                n += 1
            key = f"{key}_{n}"
        used_keys.add(key)

        headers_norm[i] = key
        cols.append({"bbox_id": bid, "key": key, "col_index": i, "mods": mods})

    return headers_norm, cols


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _unquote(value: str) -> str:
    s = str(value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {"'", '"'}:
        return s[1:-1].strip()
    return s


def build_label_id_map(root) -> Dict[str, str]:
    """Return first inkscape:label -> element id mapping in document order."""
    out: Dict[str, str] = {}
    if root is None:
        return out
    label_attr = f"{{{CONST.NS_INKSCAPE}}}label"
    for el in root.iter():
        node_id = str(el.get("id") or "").strip()
        if not node_id:
            continue
        text = _norm_text(el.get(label_attr) or "")
        if text and text not in out:
            out[text] = node_id
    return out


def _lookup_alias(value: str, label_to_id: Dict[str, str]) -> str | None:
    raw = _norm_text(value)
    if raw in label_to_id:
        return label_to_id[raw]
    unquoted = _norm_text(_unquote(raw))
    if unquoted in label_to_id:
        return label_to_id[unquoted]
    return None


def _split_quoted_tokens(value: str) -> list[str]:
    s = str(value or "").strip()
    if not s:
        return []
    out = []
    cur = []
    quote = ""
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            cur.append(ch)
            i += 1
            continue
        if ch.isspace():
            if cur:
                out.append("".join(cur).strip())
                cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        out.append("".join(cur).strip())
    return [x for x in out if x]


def _translate_left_targets(left: str, label_to_id: Dict[str, str]) -> tuple[str, bool]:
    alias = _lookup_alias(left, label_to_id)
    if alias:
        return alias, True
    tokens = _split_quoted_tokens(left)
    if len(tokens) <= 1:
        return left, False
    changed = False
    out = []
    for tok in tokens:
        mapped = _lookup_alias(tok, label_to_id)
        if mapped:
            out.append(mapped)
            changed = True
        else:
            out.append(tok)
    return " ".join(out), changed


def translate_header_key(header: str, label_to_id: Dict[str, str]) -> str:
    """Translate only the header target side, preserving modifiers/defaults."""
    raw = str(header or "").strip()
    if not raw or not label_to_id:
        return raw
    if raw.startswith("__dm_"):
        return raw

    left, has_eq, right = raw.partition("=")
    left = (left or "").strip()

    prop = ""
    m_prop = re.match(r"^(?P<id>.+?)\[(?P<prop>[A-Za-z_][A-Za-z0-9_-]*)\]\s*$", left)
    if m_prop:
        prop = (m_prop.group("prop") or "").strip()
        left = (m_prop.group("id") or "").strip()

    plus = ""
    if left.endswith("+"):
        plus = "+"
        left = left[:-1].strip()

    translated_left, changed = _translate_left_targets(left, label_to_id)
    if not changed:
        return raw

    out_left = translated_left
    if prop:
        out_left = f"{out_left}[{prop}]"
    if plus:
        out_left += plus
    if has_eq:
        return f"{out_left}={right.strip()}"
    return out_left


def translate_datasets(datasets: Iterable[dict], root) -> int:
    """Translate dataset headers in-place. Returns number of rewritten headers."""
    label_to_id = build_label_id_map(root)
    if not label_to_id:
        return 0
    changed = 0
    for ds in datasets or []:
        headers = ds.get("headers") if isinstance(ds, dict) else None
        if not isinstance(headers, list):
            continue
        for i, h in enumerate(list(headers)):
            new_h = translate_header_key(str(h or ""), label_to_id)
            if new_h != str(h or "").strip():
                headers[i] = new_h
                changed += 1
    return changed
