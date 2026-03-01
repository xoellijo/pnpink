#!/usr/bin/env python3
# PnPInk installer — single cross-platform script.
#
# Behavior:
# - Payload zip contains manifest.json AND a folder "inx/" somewhere inside.
# - We extract zip to temp, locate first "inx/" folder, and define payload_root = parent(inx/).
# - Install keeps the same structure as the zip:
#     extensions/pnpink/(... + inx/*.inx)
#     extensions/pnpink_dev_<vsafe>/(... + inx/*.inx)
# - INX files are patched IN-PLACE under dst_folder/inx/*.inx (NOT copied to extensions root).

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List


APP_NAME = "PnPInk"


# -----------------------------
# Utilities
# -----------------------------
def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def run_capture(cmd: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def which(exe: str) -> Optional[str]:
    return shutil.which(exe)


def newest_zip_in_dir(d: Path) -> Path:
    zips = sorted(d.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        die(f"No .zip found next to installer in: {d}")
    return zips[0]


def vsafe(version: str) -> str:
    s = re.sub(r"[^0-9A-Za-z.\-]+", "_", version.strip())
    s = s.replace(".", "_")
    return s or "unknown"


def copy_tree_merge(src: Path, dst: Path) -> None:
    """
    Copy src directory contents into dst (dst created if missing).
    Existing files are overwritten. This keeps directory structure (including inx/).
    """
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                copy_tree_merge(item, target)
            else:
                shutil.copytree(item, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


# -----------------------------
# ZIP / payload discovery
# -----------------------------
@dataclass
class Manifest:
    name: str
    version: str
    channel: str  # "main" or "dev"


def parse_manifest_data(data: dict) -> Manifest:
    name = str(data.get("name", "")).strip() or APP_NAME
    version = str(data.get("version", "")).strip()
    channel = str(data.get("channel", "")).strip().lower()

    if not version:
        die("manifest.json missing 'version'")
    if channel not in ("main", "dev"):
        die("manifest.json 'channel' must be 'main' or 'dev'")

    return Manifest(name=name, version=version, channel=channel)


def read_manifest_from_file(path: Path) -> Manifest:
    if not path.exists():
        die(f"manifest.json not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        die(f"manifest.json invalid JSON at {path}: {e}")
    return parse_manifest_data(data)


def read_manifest_from_zip(zip_path: Path, fallback_manifest_paths: Optional[List[Path]] = None) -> Manifest:
    with zipfile.ZipFile(zip_path, "r") as z:
        candidates = [n for n in z.namelist() if n.endswith("manifest.json")]
        if candidates:
            candidates.sort(key=lambda s: (s.count("/"), len(s)))
            raw = z.read(candidates[0]).decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                die(f"manifest.json invalid JSON inside zip: {e}")
            return parse_manifest_data(data)

    # Fallback: manifest.json outside zip
    for p in (fallback_manifest_paths or []):
        if p and p.exists():
            log(f"[2] manifest.json not found inside zip, using fallback file: {p}")
            return read_manifest_from_file(p)

    die("manifest.json not found inside zip payload and no fallback manifest found")


def extract_zip_to_temp(zip_path: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="pnpink_install_"))
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp_dir)
    return tmp_dir


def find_payload_root(extracted_root: Path) -> Path:
    # Find first directory named "inx" and use its parent as payload root.
    inx_dirs = sorted([p for p in extracted_root.rglob("inx") if p.is_dir()])
    if not inx_dirs:
        die("Could not find 'inx/' folder inside extracted zip.")
    payload_root = inx_dirs[0].parent
    if not any((payload_root / "inx").glob("*.inx")):
        die(f"Found inx folder at {inx_dirs[0]}, but no .inx files inside.")
    return payload_root


# -----------------------------
# Inkscape discovery
# -----------------------------
def find_inkscape_from_env() -> Optional[str]:
    v = os.environ.get("PNPINK_INKSCAPE", "").strip()
    if v:
        p = Path(v)
        if p.exists():
            return str(p)
    return None


def find_inkscape_from_path() -> Optional[str]:
    exe = "inkscape.exe" if os.name == "nt" else "inkscape"
    return which(exe)


def find_inkscape_typical() -> Optional[str]:
    system = platform.system().lower()
    candidates: List[Path] = []

    if system == "windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        la = os.environ.get("LocalAppData", "")
        candidates += [
            Path(pf) / "Inkscape" / "bin" / "inkscape.exe",
            Path(pf) / "Inkscape" / "inkscape.exe",
            Path(pf86) / "Inkscape" / "bin" / "inkscape.exe",
            Path(pf86) / "Inkscape" / "inkscape.exe",
        ]
        if la:
            candidates += [
                Path(la) / "Programs" / "Inkscape" / "bin" / "inkscape.exe",
                Path(la) / "Programs" / "Inkscape" / "inkscape.exe",
            ]

    elif system == "darwin":
        candidates += [
            Path("/Applications/Inkscape.app/Contents/MacOS/inkscape"),
            Path.home() / "Applications/Inkscape.app/Contents/MacOS/inkscape",
        ]

    else:
        candidates += [
            Path("/usr/bin/inkscape"),
            Path("/usr/local/bin/inkscape"),
            Path("/snap/bin/inkscape"),
        ]

    for c in candidates:
        if c.exists():
            return str(c)
    return None


def find_inkscape_from_running_process_best_effort() -> Optional[str]:
    system = platform.system().lower()

    if system == "windows":
        ps = which("powershell") or which("pwsh")
        if not ps:
            return None
        code = "(Get-Process inkscape -ErrorAction SilentlyContinue | Select-Object -First 1).Path"
        rc, out, _ = run_capture([ps, "-NoProfile", "-Command", code], timeout=10)
        p = out.strip().splitlines()[0].strip() if out.strip() else ""
        return p if p else None

    if system == "linux":
        rc, out, _ = run_capture(["pgrep", "-x", "inkscape"], timeout=5)
        if rc != 0 or not out.strip():
            return None
        pid = out.strip().splitlines()[0]
        proc_exe = Path("/proc") / pid / "exe"
        if proc_exe.exists():
            try:
                return str(proc_exe.resolve())
            except Exception:
                return None
        return None

    if system == "darwin":
        rc, out, _ = run_capture(["pgrep", "-x", "inkscape"], timeout=5)
        if rc != 0 or not out.strip():
            return None
        pid = out.strip().splitlines()[0]
        rc, out2, _ = run_capture(["ps", "-p", pid, "-o", "comm="], timeout=5)
        p = out2.strip().splitlines()[0].strip() if out2.strip() else ""
        return p if p else None

    return None


def wait_for_inkscape() -> str:
    log("Inkscape not found. Please open Inkscape now.")
    log("Waiting for Inkscape process... (Ctrl-C to cancel)")
    while True:
        p = (
            find_inkscape_from_running_process_best_effort()
            or find_inkscape_from_path()
            or find_inkscape_typical()
        )
        if p:
            return p
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
    line = out.strip().splitlines()[0].strip() if out.strip() else ""
    if not line:
        die("Inkscape returned empty --user-data-directory")
    return Path(line)


# -----------------------------
# INX patching
# -----------------------------
ID_RE = re.compile(r"<id>.*?</id>", re.DOTALL)
SUBMENU_RE = re.compile(r'<submenu\b([^>]*)_name="[^"]*"([^>]*)/>', re.DOTALL)
COMMAND_RE = re.compile(r"(<command\b[^>]*>).*?(</command>)", re.DOTALL)



# Visible-name/label version patching:
# Replace tokens like " v07", " v0.7b", etc. inside <_name> and <label> text nodes.
# If you want to keep a fixed version, use "v.2" (dot after v), which will NOT match.
VERSION_TOKEN_RE = re.compile(r"(\sv)(\d[0-9A-Za-z._-]*)(?=\s|$)")
NAME_TAG_RE = re.compile(r"(<_name>)(.*?)(</_name>)", re.DOTALL)
LABEL_TAG_RE = re.compile(r"(<label\b[^>]*>)(.*?)(</label>)", re.DOTALL)

def _patch_version_tokens(s: str, version: str) -> str:
    return VERSION_TOKEN_RE.sub(lambda m: f"{m.group(1)}{version}", s)

def patch_visible_versions(text: str, version: str) -> str:
    # Only touch <_name>...</_name> and <label ...>...</label> contents.
    text = NAME_TAG_RE.sub(lambda m: f"{m.group(1)}{_patch_version_tokens(m.group(2), version)}{m.group(3)}", text)
    text = LABEL_TAG_RE.sub(lambda m: f"{m.group(1)}{_patch_version_tokens(m.group(2), version)}{m.group(3)}", text)
    return text

def patch_inx_text(text: str, new_id: str, submenu_name: str, new_command: str, version: str) -> str:
    if not ID_RE.search(text):
        raise ValueError("Missing <id>...</id>")
    if not SUBMENU_RE.search(text):
        raise ValueError('Missing <submenu ... _name="..."/>')
    if not COMMAND_RE.search(text):
        raise ValueError("Missing <command>...</command>")

    text = ID_RE.sub(f"<id>{new_id}</id>", text, count=1)
    text = SUBMENU_RE.sub(lambda m: f'<submenu{m.group(1)}_name="{submenu_name}"{m.group(2)}/>', text, count=1)
    text = COMMAND_RE.sub(lambda m: f"{m.group(1)}{new_command}{m.group(2)}", text, count=1)
    text = patch_visible_versions(text, version)
    return text


def patch_inx_file(inx_path: Path, new_id: str, submenu_name: str, new_command: str, version: str) -> None:
    raw = inx_path.read_text(encoding="utf-8", errors="replace")
    patched = patch_inx_text(raw, new_id=new_id, submenu_name=submenu_name, new_command=new_command, version=version)
    inx_path.write_text(patched, encoding="utf-8", newline="\n")


# -----------------------------
# Main install
# -----------------------------
def main() -> int:
    here = Path(__file__).resolve().parent

    if len(sys.argv) >= 2:
        zip_path = Path(sys.argv[1]).expanduser().resolve()
        if not zip_path.exists():
            die(f"Zip not found: {zip_path}")
    else:
        zip_path = newest_zip_in_dir(here)

    log(f"[1] Using payload zip: {zip_path}")

    fallback_manifest_paths = [
        here / "manifest.json",
        zip_path.parent / "manifest.json",
    ]
    manifest = read_manifest_from_zip(zip_path, fallback_manifest_paths=fallback_manifest_paths)
    log(f"[2] Manifest: name={manifest.name} version={manifest.version} channel={manifest.channel}")

    inkscape_exe = resolve_inkscape()
    log(f"[3] Using Inkscape: {inkscape_exe}")

    user_dir = inkscape_user_data_dir(inkscape_exe)
    ext_dir = user_dir / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    log(f"[4] Inkscape user dir: {user_dir}")
    log(f"[4] Extensions dir   : {ext_dir}")

    tmp_root = extract_zip_to_temp(zip_path)
    try:
        payload_root = find_payload_root(tmp_root)
        log(f"[5] Payload root: {payload_root}")

        v_safe = vsafe(manifest.version)
        if manifest.channel == "dev":
            dst_folder_name = f"pnpink_dev_{v_safe}"
        else:
            dst_folder_name = "pnpink"

        dst_folder = ext_dir / dst_folder_name

        if manifest.channel == "main" and dst_folder.exists():
            log(f"[6] Removing previous main install: {dst_folder}")
            shutil.rmtree(dst_folder)

        dst_folder.mkdir(parents=True, exist_ok=True)
        log(f"[6] Installing payload to: {dst_folder}")

        # Copy full payload root INCLUDING inx/ (respect zip structure)
        copy_tree_merge(payload_root, dst_folder)

        # Patch INX in-place inside dst_folder/inx
        inx_dir = dst_folder / "inx"
        inx_files = sorted(inx_dir.glob("*.inx"))
        if not inx_files:
            die(f"No .inx files found in installed folder: {inx_dir}")

        # Main channel must be stable (same menu/id) so upgrades overwrite in place.
        submenu = "PnPInk" if manifest.channel == "main" else f"PnPInk DEV v{manifest.version}"
        if manifest.channel == "main":
            id_prefix = "pnpink"
        else:
            id_prefix = f"pnpink_dev_v{v_safe}"
            
        log(f"[7] Patching INX in-place: {inx_dir}")
        for inx in inx_files:
            tool = inx.stem
            new_id = f"{id_prefix}.{tool}"

            # <command reldir="extensions"> must be relative to extensions root
            if manifest.channel == "main":
                new_cmd = f"pnpink/{tool}.py"
            else:
                new_cmd = f"{dst_folder_name}/{tool}.py"

            try:
                patch_inx_file(inx, new_id=new_id, submenu_name=submenu, new_command=new_cmd, version=manifest.version)
            except Exception as e:
                die(f"Failed to patch INX {inx.name}: {e}")

            log(f"    - {inx.name}: id={new_id} cmd={new_cmd}")

    finally:
        try:
            shutil.rmtree(tmp_root)
        except Exception:
            pass

    log("")
    log(f"Installed {manifest.name} ({manifest.channel}) v{manifest.version}.")
    log("Close and re-open Inkscape for the Extensions menu to refresh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
