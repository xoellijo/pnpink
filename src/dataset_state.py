#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, Optional

import app_paths

STATE_FILE = str(app_paths.data_path("gsheets", "dataset_state.json"))
_LEGACY_STATE_FILES = [str(path) for path in app_paths.legacy_data_paths("gsheets", "dataset_state.json")]
_STATE_LOCK = threading.RLock()


def _norm_svg_path(path: str) -> str:
    p = os.path.abspath(path or "")
    p = os.path.normpath(p)
    return os.path.normcase(p)


def _load_state() -> Dict[str, object]:
    for path in (STATE_FILE, *_LEGACY_STATE_FILES):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {"version": 1, "by_svg": {}}


def _save_state(data: Dict[str, object]) -> None:
    d = os.path.dirname(STATE_FILE)
    os.makedirs(d, exist_ok=True)
    temporary = f"{STATE_FILE}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(temporary, STATE_FILE)


def remember_svg(svg_path: str) -> None:
    path = os.path.normpath(os.path.abspath(svg_path or ""))
    key = _norm_svg_path(path)
    if not key or not os.path.isfile(path):
        return
    with _STATE_LOCK:
        state = _load_state()
        by_svg = state.get("by_svg")
        if not isinstance(by_svg, dict):
            by_svg = {}
        record = dict(by_svg.get(key) or {})
        record["svg_path"] = path
        record["updated_at"] = int(time.time())
        if not record.get("kind"):
            record["kind"] = "local_csv"
        by_svg[key] = record
        state["version"] = 2
        state["by_svg"] = by_svg
        _save_state(state)


def list_svg_templates() -> list[Dict[str, object]]:
    with _STATE_LOCK:
        state = _load_state()
        by_svg = state.get("by_svg")
        if not isinstance(by_svg, dict):
            return []
        templates = []
        for key, value in by_svg.items():
            record = value if isinstance(value, dict) else {}
            path = os.path.normpath(str(record.get("svg_path") or key))
            if not os.path.isfile(path):
                continue
            sheet_id = str(record.get("sheet_id") or "").strip()
            csv_path = os.path.splitext(path)[0] + ".csv"
            dataset = f"gsheet://{os.path.splitext(os.path.basename(path))[0]}" if sheet_id else (
                os.path.basename(csv_path) if os.path.isfile(csv_path) else "No dataset"
            )
            templates.append({
                "path": path,
                "name": os.path.basename(path),
                "dataset": dataset,
                "updated_at": int(record.get("updated_at") or 0),
            })
        templates.sort(key=lambda item: (-int(item["updated_at"]), str(item["name"]).lower()))
        return templates


def set_gsheet_for_svg(svg_path: str, sheet_id: str, sheet_range: str = "", access_mode: str = "") -> None:
    sp = _norm_svg_path(svg_path)
    sid = str(sheet_id or "").strip()
    srg = str(sheet_range or "").strip()
    am = str(access_mode or "").strip().lower()
    if am not in ("", "public", "oauth"):
        am = ""
    if not sp or not sid:
        return
    with _STATE_LOCK:
        state = _load_state()
        by_svg = state.get("by_svg")
        if not isinstance(by_svg, dict):
            by_svg = {}
        record = dict(by_svg.get(sp) or {})
        if str(record.get("sheet_id") or "").strip() != sid or str(record.get("sheet_range") or "").strip() != srg:
            record.pop("sheet_gid", None)
        record.update({
            "kind": "gsheet",
            "svg_path": os.path.normpath(os.path.abspath(svg_path)),
            "sheet_id": sid,
            "sheet_range": srg,
            "access_mode": am,
            "updated_at": int(time.time()),
        })
        by_svg[sp] = record
        state["version"] = 2
        state["by_svg"] = by_svg
        _save_state(state)


def set_gsheet_gid_for_svg(svg_path: str, sheet_id: str, sheet_range: str, sheet_gid: str) -> None:
    sp = _norm_svg_path(svg_path)
    sid = str(sheet_id or "").strip()
    srg = str(sheet_range or "").strip()
    gid = str(sheet_gid or "").strip()
    if not sp or not sid or not gid.isdigit():
        return
    with _STATE_LOCK:
        state = _load_state()
        by_svg = state.get("by_svg")
        if not isinstance(by_svg, dict):
            return
        record = by_svg.get(sp)
        if not isinstance(record, dict):
            return
        if str(record.get("sheet_id") or "").strip() != sid or str(record.get("sheet_range") or "").strip() != srg:
            return
        record["sheet_gid"] = gid
        record["updated_at"] = int(time.time())
        _save_state(state)


def get_gsheet_for_svg(svg_path: str) -> Optional[Dict[str, str]]:
    sp = _norm_svg_path(svg_path)
    if not sp:
        return None
    with _STATE_LOCK:
        state = _load_state()
        by_svg = state.get("by_svg")
        if not isinstance(by_svg, dict):
            return None
        rec = by_svg.get(sp)
    if not isinstance(rec, dict):
        return None
    sid = str(rec.get("sheet_id") or "").strip()
    srg = str(rec.get("sheet_range") or "").strip()
    am = str(rec.get("access_mode") or "").strip().lower()
    gid = str(rec.get("sheet_gid") or "").strip()
    if am not in ("", "public", "oauth"):
        am = ""
    if not sid:
        return None
    return {"sheet_id": sid, "sheet_range": srg, "access_mode": am, "sheet_gid": gid if gid.isdigit() else ""}
