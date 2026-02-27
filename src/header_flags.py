# -*- coding: utf-8 -*-
"""header_flags.py — v0.1

Pure helpers for parsing dataset header template declarations.

This module is intentionally independent from inkex so it can be unit-tested
without requiring an Inkscape runtime.

Header cell syntax:
  {template_id @page @back}
  {t=template_id @page}
  {template_bbox=template_id @back}

Recognized modifiers:
  @page  — page-anchored placement
  @back  — back pass only
"""

from __future__ import annotations

import re
from typing import Optional, Dict, Tuple, List, Set

_VALID_MODS = {'@page', '@back'}


def parse_template_header_cell(cell: str) -> Optional[Dict]:
    s = (cell or '').strip()
    m = re.fullmatch(r"\{\s*(.*?)\s*\}", s)
    if not m:
        return None
    body = (m.group(1) or '').strip()
    if not body:
        return None

    # Split tokens (space-separated). We keep it intentionally simple.
    toks = [t for t in re.split(r"\s+", body) if t]

    bbox_id = None
    mods: Set[str] = set()
    for t in toks:
        if t in _VALID_MODS:
            mods.add(t)
            continue
        if t.startswith('@'):
            # Unknown modifier: ignore (forward compatible)
            continue
        if bbox_id is None:
            # t=... / template_bbox=... / bare id
            m2 = re.fullmatch(r"(?:t|template_bbox)\s*=\s*([A-Za-z][A-Za-z0-9_.-]*)", t)
            if m2:
                bbox_id = m2.group(1)
            else:
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", t):
                    bbox_id = t
                else:
                    return None
        else:
            # Extra tokens (non-mod) are not supported (avoid implicit mini-parsers)
            return None

    if not bbox_id:
        return None
    return {'bbox_id': bbox_id, 'mods': mods}


def extract_template_columns(headers: List[str], key_prefix: str = "__dm_tcol__") -> Tuple[List[str], List[Dict]]:
    """Normalize headers and extract declared template columns.

    Returns (headers_norm, template_cols)
    where template_cols are dicts:
      {bbox_id, key, col_index, mods:[...]}.

    For declared template columns, the returned header is replaced with an internal
    control key so regular field replacement does not try to match it to SVG ids.
    """
    headers_norm = list(headers or [])
    cols = []
    used_keys = set()
    for i, h in enumerate(headers_norm):
        info = parse_template_header_cell(h)
        if not info:
            continue
        bid = info['bbox_id']
        mods = sorted(list(info.get('mods') or []))

        key = f"{key_prefix}{bid}"
        # Ensure uniqueness if the same bbox_id is declared more than once.
        if key in used_keys:
            n = 2
            while f"{key}_{n}" in used_keys:
                n += 1
            key = f"{key}_{n}"
        used_keys.add(key)

        headers_norm[i] = key
        cols.append({'bbox_id': bid, 'key': key, 'col_index': i, 'mods': mods})

    return headers_norm, cols
