# -*- coding: utf-8 -*-
"""Detect browsers and open local web tools with the selected one."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser


BROWSER_LABELS = {
    "system": "System default",
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "firefox": "Mozilla Firefox",
    "safari": "Safari",
}


def normalize_browser(value: str, default: str = "system") -> str:
    key = str(value or "").strip().lower()
    return key if key in BROWSER_LABELS else default


def _with_query(url: str, **values: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((key, value) for key, value in values.items() if value)
    return urllib.parse.urlunsplit((*parsed[:3], urllib.parse.urlencode(query), parsed.fragment))


def _candidate_paths(browser: str) -> list[str]:
    names = {
        "edge": ["msedge.exe"] if os.name == "nt" else ["microsoft-edge", "microsoft-edge-stable"],
        "chrome": ["chrome.exe"] if os.name == "nt" else ["google-chrome", "google-chrome-stable"],
        "chromium": ["chromium.exe"] if os.name == "nt" else ["chromium", "chromium-browser"],
        "firefox": ["firefox.exe"] if os.name == "nt" else ["firefox"],
        "safari": [],
    }
    candidates = [path for name in names.get(browser, []) if (path := shutil.which(name))]
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relatives = {
            "edge": [Path("Microsoft/Edge/Application/msedge.exe")],
            "chrome": [Path("Google/Chrome/Application/chrome.exe")],
            "chromium": [Path("Chromium/Application/chrome.exe")],
            "firefox": [Path("Mozilla Firefox/firefox.exe")],
        }.get(browser, [])
        candidates.extend(
            str(path)
            for root in roots if root
            for relative in relatives
            if (path := Path(root) / relative).is_file()
        )
    elif sys.platform == "darwin":
        applications = {
            "edge": [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")],
            "chrome": [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")],
            "chromium": [Path("/Applications/Chromium.app/Contents/MacOS/Chromium")],
            "firefox": [Path("/Applications/Firefox.app/Contents/MacOS/firefox")],
            "safari": [Path("/Applications/Safari.app/Contents/MacOS/Safari")],
        }.get(browser, [])
        candidates.extend(str(path) for path in applications if path.is_file())
    return list(dict.fromkeys(os.path.normpath(path) for path in candidates))


def detected_browsers(*, include_system: bool = True) -> list[dict[str, str]]:
    browsers = []
    if include_system:
        browsers.append({"id": "system", "label": BROWSER_LABELS["system"], "path": ""})
    for browser in ("edge", "chrome", "chromium", "firefox", "safari"):
        paths = _candidate_paths(browser)
        if paths:
            browsers.append({"id": browser, "label": BROWSER_LABELS[browser], "path": paths[0]})
    return browsers


def _launch(executable: str, args: list[str]) -> bool:
    try:
        kwargs = {
            "args": [executable, *args],
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": os.name != "nt",
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        subprocess.Popen(**kwargs)
        return True
    except OSError:
        return False


def open_web_app(url: str, preferred_browser: str = "system") -> bool:
    """Open a local web tool using the configured installed browser."""
    browser = normalize_browser(preferred_browser)
    if browser == "system":
        try:
            return bool(webbrowser.open(url, new=2, autoraise=True))
        except Exception:
            return False

    paths = _candidate_paths(browser)
    if paths:
        chromium = browser in {"edge", "chrome", "chromium"}
        target = _with_query(url, shell="app") if chromium else url
        args = [f"--app={target}"] if chromium else [target]
        if _launch(paths[0], args):
            return True
    try:
        return bool(webbrowser.open(url, new=2, autoraise=True))
    except Exception:
        return False
