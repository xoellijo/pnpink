#!/usr/bin/env python3
"""Install one PnPInk payload into the current Inkscape user profile."""

from __future__ import annotations

import argparse
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


def log(message: str) -> None:
    print(message, flush=True)


def die(message: str, code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def run_capture(command: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return process.returncode, process.stdout, process.stderr
    except FileNotFoundError:
        return 127, "", f"not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def find_inkscape_from_env() -> Optional[str]:
    value = os.environ.get("PNPINK_INKSCAPE", "").strip()
    return value if value and Path(value).exists() else None


def find_inkscape_from_process() -> Optional[str]:
    system = platform.system().lower()
    if system == "windows":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return None
        expression = "(Get-Process inkscape -ErrorAction SilentlyContinue | Select-Object -First 1).Path"
        returncode, stdout, _stderr = run_capture([powershell, "-NoProfile", "-Command", expression], timeout=10)
        return stdout.strip().splitlines()[0].strip() if returncode == 0 and stdout.strip() else None
    if system in {"linux", "darwin"}:
        returncode, stdout, _stderr = run_capture(["pgrep", "-x", "inkscape"], timeout=5)
        if returncode != 0 or not stdout.strip():
            return None
        process_id = stdout.strip().splitlines()[0]
        if system == "linux":
            try:
                return str((Path("/proc") / process_id / "exe").resolve())
            except Exception:
                return None
        returncode, stdout, _stderr = run_capture(["ps", "-p", process_id, "-o", "comm="], timeout=5)
        return stdout.strip().splitlines()[0].strip() if returncode == 0 and stdout.strip() else None
    return None


def find_inkscape_typical() -> Optional[str]:
    system = platform.system().lower()
    candidates: list[Path] = []
    if system == "windows":
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LocalAppData", ""),
        ]
        candidates.extend(Path(root) / "Inkscape" / "bin" / "inkscape.exe" for root in roots if root)
        candidates.extend(Path(root) / "Programs" / "Inkscape" / "bin" / "inkscape.exe" for root in roots[2:] if root)
    elif system == "darwin":
        candidates.extend(
            [
                Path("/Applications/Inkscape.app/Contents/MacOS/inkscape"),
                Path.home() / "Applications/Inkscape.app/Contents/MacOS/inkscape",
            ]
        )
    else:
        candidates.extend(Path(value) for value in ("/usr/bin/inkscape", "/usr/local/bin/inkscape", "/snap/bin/inkscape"))
    return next((str(path) for path in candidates if path.exists()), None)


def resolve_inkscape(explicit_path: str | None = None) -> str:
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if not path.exists():
            die(f"Inkscape not found: {path}")
        return str(path)
    executable = "inkscape.exe" if os.name == "nt" else "inkscape"
    found = find_inkscape_from_env() or find_inkscape_from_process() or shutil.which(executable) or find_inkscape_typical()
    if found:
        return found
    log("Inkscape not found. Open Inkscape now; waiting for its process...")
    while True:
        found = find_inkscape_from_process() or shutil.which(executable) or find_inkscape_typical()
        if found:
            return found
        time.sleep(0.8)


def inkscape_user_data_dir(inkscape_executable: str) -> Path:
    returncode, stdout, stderr = run_capture([inkscape_executable, "--user-data-directory"], timeout=20)
    if returncode != 0:
        die(f"Failed to run Inkscape --user-data-directory: {stderr.strip()}")
    value = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
    if not value:
        die("Inkscape returned an empty user-data directory")
    return Path(value)


def extract_payload(payload_zip: Path, destination: Path) -> Path:
    with zipfile.ZipFile(payload_zip, "r") as archive:
        archive.extractall(destination)
    for candidate in (destination / "src", destination):
        inx_dir = candidate / "inx"
        if inx_dir.is_dir() and any(inx_dir.glob("*.inx")):
            return candidate
    die("Invalid payload: expected src/inx/*.inx")


def replace_install(payload_root: Path, destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(payload_root, destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install a PnPInk release payload")
    parser.add_argument("payload", type=Path, help="Downloaded pnpink_payload_*.zip")
    parser.add_argument("--inkscape", help="Explicit Inkscape executable")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    payload_zip = arguments.payload.expanduser().resolve()
    if not payload_zip.is_file():
        die(f"Payload not found: {payload_zip}")

    log(f"[1] Payload: {payload_zip}")
    inkscape_executable = resolve_inkscape(arguments.inkscape)
    log(f"[2] Inkscape: {inkscape_executable}")
    extensions_dir = inkscape_user_data_dir(inkscape_executable) / "extensions"
    extensions_dir.mkdir(parents=True, exist_ok=True)
    destination = extensions_dir / APP_DIR_NAME
    log(f"[3] Destination: {destination}")

    temporary_root = Path(tempfile.mkdtemp(prefix="pnpink_install_"))
    try:
        payload_root = extract_payload(payload_zip, temporary_root)
        log(f"[4] Replacing previous installation")
        replace_install(payload_root, destination)
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    log("")
    log("PnPInk installed. Restart Inkscape to refresh the Extensions menu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
