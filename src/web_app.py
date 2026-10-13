# -*- coding: utf-8 -*-
"""Detect browsers and open local web tools with the selected one."""

from __future__ import annotations

import os
from pathlib import Path
import json
import shutil
import subprocess
import sys
import threading
import urllib.parse
import webbrowser

import app_paths


BROWSER_LABELS = {
    "system": "System default",
    "edge": "Microsoft Edge",
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "firefox": "Mozilla Firefox",
    "safari": "Safari",
}

WINDOW_GEOMETRY_FILE = app_paths.data_path("deckmaker_web_window.json")
_WINDOW_GEOMETRY_LOCK = threading.Lock()


def normalize_window_geometry(value) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    try:
        geometry = {
            "x": int(value["x"]),
            "y": int(value["y"]),
            "width": int(value["width"]),
            "height": int(value["height"]),
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return {}
    if not (320 <= geometry["width"] <= 16384 and 240 <= geometry["height"] <= 16384):
        return {}
    if not (-10000 <= geometry["x"] <= 10000 and -10000 <= geometry["y"] <= 10000):
        return {}
    return geometry


def load_window_geometry() -> dict[str, int]:
    with _WINDOW_GEOMETRY_LOCK:
        try:
            payload = json.loads(WINDOW_GEOMETRY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
    return normalize_window_geometry(payload.get("geometry") if isinstance(payload, dict) else None)


def save_window_geometry(value) -> bool:
    geometry = normalize_window_geometry(value)
    if not geometry:
        return False
    payload = json.dumps({"version": 1, "geometry": geometry}, indent=2) + "\n"
    with _WINDOW_GEOMETRY_LOCK:
        WINDOW_GEOMETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = WINDOW_GEOMETRY_FILE.with_suffix(WINDOW_GEOMETRY_FILE.suffix + ".tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, WINDOW_GEOMETRY_FILE)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass
    return True


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
        args = []
        if chromium:
            geometry = load_window_geometry()
            if geometry:
                args.extend([
                    f"--window-position={geometry['x']},{geometry['y']}",
                    f"--window-size={geometry['width']},{geometry['height']}",
                ])
            args.append(f"--app={target}")
        else:
            args.append(target)
        if _launch(paths[0], args):
            return True
    try:
        return bool(webbrowser.open(url, new=2, autoraise=True))
    except Exception:
        return False
