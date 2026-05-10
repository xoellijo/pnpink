#!/usr/bin/env python3
"""PnPInk installer.

Simple install flow:
- use the given zip, or the newest .zip next to this script
- locate Inkscape and query its user-data directory
- extract the payload zip to a temp folder
- locate the payload root by finding the bundled inx/ folder
- replace extensions/pnpink with the extracted payload as-is

The payload already contains stable .inx files. The installer does not patch
manifest data, dev versions, ids, menus, or command paths.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional


APP_DIR_NAME = "pnpink"


def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def run_capture(cmd: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def newest_zip_in_dir(directory: Path) -> Path:
    zips = sorted(directory.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        die(f"No .zip found next to installer in: {directory}")
    return zips[0]


def extract_zip_to_temp(zip_path: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="pnpink_install_"))
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)
    return tmp_dir


def find_payload_root(extracted_root: Path) -> Path:
    inx_dirs = sorted(p for p in extracted_root.rglob("inx") if p.is_dir())
    if not inx_dirs:
        die("Could not find 'inx/' folder inside extracted zip.")
    payload_root = inx_dirs[0].parent
    if not any((payload_root / "inx").glob("*.inx")):
        die(f"Found inx folder at {inx_dirs[0]}, but no .inx files inside.")
    return payload_root


def find_inkscape_from_env() -> Optional[str]:
    value = os.environ.get("PNPINK_INKSCAPE", "").strip()
    if not value:
        return None
    path = Path(value)
    return str(path) if path.exists() else None


def find_inkscape_from_path() -> Optional[str]:
    exe = "inkscape.exe" if os.name == "nt" else "inkscape"
    return shutil.which(exe)


def find_inkscape_typical() -> Optional[str]:
    system = platform.system().lower()
    candidates: list[Path] = []

    if system == "windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        la = os.environ.get("LocalAppData", "")
        candidates.extend(
            [
                Path(pf) / "Inkscape" / "bin" / "inkscape.exe",
                Path(pf) / "Inkscape" / "inkscape.exe",
                Path(pf86) / "Inkscape" / "bin" / "inkscape.exe",
                Path(pf86) / "Inkscape" / "inkscape.exe",
            ]
        )
        if la:
            candidates.extend(
                [
                    Path(la) / "Programs" / "Inkscape" / "bin" / "inkscape.exe",
                    Path(la) / "Programs" / "Inkscape" / "inkscape.exe",
                ]
            )
    elif system == "darwin":
        candidates.extend(
            [
                Path("/Applications/Inkscape.app/Contents/MacOS/inkscape"),
                Path.home() / "Applications/Inkscape.app/Contents/MacOS/inkscape",
            ]
        )
    else:
        candidates.extend(
            [
                Path("/usr/bin/inkscape"),
                Path("/usr/local/bin/inkscape"),
                Path("/snap/bin/inkscape"),
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def find_inkscape_from_running_process_best_effort() -> Optional[str]:
    system = platform.system().lower()

    if system == "windows":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return None
        code = "(Get-Process inkscape -ErrorAction SilentlyContinue | Select-Object -First 1).Path"
        rc, out, _err = run_capture([ps, "-NoProfile", "-Command", code], timeout=10)
        if rc != 0:
            return None
        value = out.strip().splitlines()[0].strip() if out.strip() else ""
        return value or None

    if system == "linux":
        rc, out, _err = run_capture(["pgrep", "-x", "inkscape"], timeout=5)
        if rc != 0 or not out.strip():
            return None
        pid = out.strip().splitlines()[0]
        proc_exe = Path("/proc") / pid / "exe"
        if not proc_exe.exists():
            return None
        try:
            return str(proc_exe.resolve())
        except Exception:
            return None

    if system == "darwin":
        rc, out, _err = run_capture(["pgrep", "-x", "inkscape"], timeout=5)
        if rc != 0 or not out.strip():
            return None
        pid = out.strip().splitlines()[0]
        rc, out2, _err2 = run_capture(["ps", "-p", pid, "-o", "comm="], timeout=5)
        if rc != 0:
            return None
        value = out2.strip().splitlines()[0].strip() if out2.strip() else ""
        return value or None

    return None


def wait_for_inkscape() -> str:
    log("Inkscape not found. Please open Inkscape now.")
    log("Waiting for Inkscape process... (Ctrl-C to cancel)")
    while True:
        found = (
            find_inkscape_from_running_process_best_effort()
            or find_inkscape_from_path()
            or find_inkscape_typical()
        )
        if found:
            return found
        time.sleep(0.8)


def resolve_inkscape() -> str:
    return (
        find_inkscape_from_env()
        or find_inkscape_from_running_process_best_effort()
        or find_inkscape_from_path()
        or find_inkscape_typical()
        or wait_for_inkscape()
    )


def inkscape_user_data_dir(inkscape_exe: str) -> Path:
    rc, out, err = run_capture([inkscape_exe, "--user-data-directory"], timeout=20)
    if rc != 0:
        die(f"Failed to run Inkscape --user-data-directory. rc={rc}, err={err.strip()}")
    value = out.strip().splitlines()[0].strip() if out.strip() else ""
    if not value:
        die("Inkscape returned empty --user-data-directory")
    return Path(value)


def main() -> int:
    here = Path(__file__).resolve().parent

    if len(sys.argv) >= 2:
        zip_path = Path(sys.argv[1]).expanduser().resolve()
        if not zip_path.exists():
            die(f"Zip not found: {zip_path}")
    else:
        zip_path = newest_zip_in_dir(here)

    log(f"[1] Using payload zip: {zip_path}")

    inkscape_exe = resolve_inkscape()
    log(f"[2] Using Inkscape: {inkscape_exe}")

    user_dir = inkscape_user_data_dir(inkscape_exe)
    ext_dir = user_dir / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    log(f"[3] Inkscape user dir: {user_dir}")
    log(f"[3] Extensions dir   : {ext_dir}")

    tmp_root = extract_zip_to_temp(zip_path)
    try:
        payload_root = find_payload_root(tmp_root)
        log(f"[4] Payload root: {payload_root}")

        dst_folder = ext_dir / APP_DIR_NAME
        if dst_folder.exists():
            log(f"[5] Removing previous install: {dst_folder}")
            shutil.rmtree(dst_folder)

        log(f"[6] Installing payload to: {dst_folder}")
        shutil.copytree(payload_root, dst_folder)
    finally:
        try:
            shutil.rmtree(tmp_root)
        except Exception:
            pass

    log("")
    log("Installed PnPInk.")
    log("Close and re-open Inkscape for the Extensions menu to refresh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
