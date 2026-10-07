# -*- coding: utf-8 -*-
"""Shared per-user storage paths for PnPInk."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "PnPInk" if base else Path.home() / "AppData" / "Local" / "PnPInk"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PnPInk"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".local" / "share") / "PnPInk"


def data_path(*parts: str) -> Path:
    return data_root().joinpath(*parts)


def legacy_data_paths(*parts: str) -> list[Path]:
    """Return prior storage locations, excluding the current location."""
    candidates: list[Path] = []
    if os.name == "nt":
        if appdata := os.environ.get("APPDATA"):
            candidates.append(Path(appdata) / "PnPInk")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "PnPInk")
    else:
        candidates.append(Path.home() / ".pnpink")
    current = data_root()
    return [path.joinpath(*parts) for path in candidates if path != current]
