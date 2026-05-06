"""Shared temp path helpers for DeckMaker/export artifacts."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from pathlib import Path


_ROOT = os.path.join(tempfile.gettempdir(), "pnpink")


def root_dir() -> str:
    os.makedirs(_ROOT, exist_ok=True)
    return os.path.normpath(_ROOT)


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text[:80] or "run"


def make_work_dir(kind: str, *, stem: str = "") -> str:
    base = os.path.join(root_dir(), _safe_name(kind))
    os.makedirs(base, exist_ok=True)
    prefix = _safe_name(stem) + "_" if stem else ""
    return os.path.normpath(tempfile.mkdtemp(prefix=prefix, dir=base))


def named_dir(kind: str, *, stem: str = "") -> str:
    path = os.path.join(root_dir(), _safe_name(kind), _safe_name(stem) if stem else "default")
    os.makedirs(path, exist_ok=True)
    return os.path.normpath(path)


def reset_named_dir(kind: str, *, stem: str = "") -> str:
    path = os.path.join(root_dir(), _safe_name(kind), _safe_name(stem) if stem else "default")
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return os.path.normpath(path)


def cleanup_old_runs(*, max_age_hours: int = 36, keep_paths: list[str] | tuple[str, ...] | None = None) -> int:
    keep = {os.path.normcase(os.path.normpath(p)) for p in (keep_paths or []) if str(p or "").strip()}
    cutoff = time.time() - max(1, int(max_age_hours or 1)) * 3600
    removed = 0
    root = root_dir()
    try:
        kinds = [os.path.join(root, name) for name in os.listdir(root)]
    except Exception:
        return 0
    for kind_dir in kinds:
        if not os.path.isdir(kind_dir):
            continue
        if os.path.basename(kind_dir) == "inkscape_profile":
            continue
        try:
            entries = [os.path.join(kind_dir, name) for name in os.listdir(kind_dir)]
        except Exception:
            continue
        for path in entries:
            norm = os.path.normcase(os.path.normpath(path))
            if norm in keep:
                continue
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                continue
            if mtime >= cutoff:
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif os.path.isfile(path):
                    os.remove(path)
                removed += 1
            except Exception:
                pass
    return removed


def cleanup_runs_now(*, keep_paths: list[str] | tuple[str, ...] | None = None) -> int:
    keep = {os.path.normcase(os.path.normpath(p)) for p in (keep_paths or []) if str(p or "").strip()}
    removed = 0
    root = root_dir()
    try:
        kinds = [os.path.join(root, name) for name in os.listdir(root)]
    except Exception:
        return 0
    for kind_dir in kinds:
        if not os.path.isdir(kind_dir):
            continue
        if os.path.basename(kind_dir) == "inkscape_profile":
            continue
        try:
            entries = [os.path.join(kind_dir, name) for name in os.listdir(kind_dir)]
        except Exception:
            continue
        for path in entries:
            norm = os.path.normcase(os.path.normpath(path))
            if norm in keep:
                continue
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                elif os.path.isfile(path):
                    os.remove(path)
                removed += 1
            except Exception:
                pass
    return removed


def stem_for_path(path: str) -> str:
    return Path(str(path or "").strip() or "run").stem or "run"
