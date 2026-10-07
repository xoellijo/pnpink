# -*- coding: utf-8 -*-
"""Open local web tools as app windows, with a browser fallback."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser


def _with_query(url: str, **values: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in values.items() if value)
    return urllib.parse.urlunsplit((*parsed[:3], urllib.parse.urlencode(query), parsed.fragment))


def _chromium_candidates() -> list[str]:
    names = [
        "msedge.exe" if os.name == "nt" else "microsoft-edge",
        "chrome.exe" if os.name == "nt" else "google-chrome",
        "chromium.exe" if os.name == "nt" else "chromium",
        "chromium-browser",
    ]
    candidates = [path for name in names if (path := shutil.which(name))]
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relatives = [
            Path("Microsoft/Edge/Application/msedge.exe"),
            Path("Google/Chrome/Application/chrome.exe"),
            Path("Chromium/Application/chrome.exe"),
        ]
        candidates.extend(
            str(path)
            for root in roots if root
            for relative in relatives
            if (path := Path(root) / relative).is_file()
        )
    elif sys.platform == "darwin":
        candidates.extend(str(path) for path in (
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ) if path.is_file())
    return list(dict.fromkeys(candidates))


def open_web_app(url: str) -> bool:
    """Prefer a standalone Chromium window; otherwise open the default browser."""
    app_url = _with_query(url, shell="app")
    for executable in _chromium_candidates():
        try:
            subprocess.Popen(
                [executable, f"--app={app_url}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=os.name != "nt",
            )
            return True
        except OSError:
            continue
    try:
        return bool(webbrowser.open(url, new=2, autoraise=True))
    except Exception:
        return False
