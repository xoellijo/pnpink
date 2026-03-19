#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional


def _app_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PnPInk", "gsheets")
    if os.uname().sysname.lower() == "darwin":  # type: ignore[attr-defined]
        return os.path.join(os.path.expanduser("~/Library/Application Support"), "PnPInk", "gsheets")
    return os.path.join(os.path.expanduser("~/.pnpink"), "gsheets")


STATE_FILE = os.path.join(_app_dir(), "dataset_state.json")


def _norm_svg_path(path: str) -> str:
    p = os.path.abspath(path or "")
    p = os.path.normpath(p)
    return os.path.normcase(p)


def _load_state() -> Dict[str, object]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"version": 1, "by_svg": {}}


def _save_state(data: Dict[str, object]) -> None:
    d = os.path.dirname(STATE_FILE)
    os.makedirs(d, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def set_gsheet_for_svg(svg_path: str, sheet_id: str, sheet_range: str = "") -> None:
    sp = _norm_svg_path(svg_path)
    sid = str(sheet_id or "").strip()
    srg = str(sheet_range or "").strip()
    if not sp or not sid:
        return
    state = _load_state()
    by_svg = state.get("by_svg")
    if not isinstance(by_svg, dict):
        by_svg = {}
    by_svg[sp] = {
        "kind": "gsheet",
        "sheet_id": sid,
        "sheet_range": srg,
        "updated_at": int(time.time()),
    }
    state["version"] = 1
    state["by_svg"] = by_svg
    _save_state(state)


def get_gsheet_for_svg(svg_path: str) -> Optional[Dict[str, str]]:
    sp = _norm_svg_path(svg_path)
    if not sp:
        return None
    state = _load_state()
    by_svg = state.get("by_svg")
    if not isinstance(by_svg, dict):
        return None
    rec = by_svg.get(sp)
    if not isinstance(rec, dict):
        return None
    sid = str(rec.get("sheet_id") or "").strip()
    srg = str(rec.get("sheet_range") or "").strip()
    if not sid:
        return None
    return {"sheet_id": sid, "sheet_range": srg}

