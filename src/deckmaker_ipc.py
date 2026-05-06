# -*- coding: utf-8 -*-
"""Resident DeckMaker process IPC and launcher helpers."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading

import deckmaker_paths as DMPATHS
from deckmaker_types import AppRequest
import log as LOG

HOST = "127.0.0.1"
PORT = 48751
ENV_DIRECT_RUN = "PNPINK_DECKMAKER_DIRECT"

_l = LOG


def send_request(req: AppRequest, timeout: float = 0.35) -> bool:
    payload = {
        "cmd": "open",
        "template": req.template,
        "sheet_id": req.sheet_id,
        "sheet_range": req.sheet_range,
        "dataset_source_mode": req.dataset_source_mode,
        "log_level": req.log_level,
    }
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as s:
            s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            s.settimeout(timeout)
            data = s.recv(32)
        return data.strip() == b"OK"
    except Exception:
        return False


def candidate_python_launchers() -> list[str]:
    out: list[str] = []
    exe = str(sys.executable or "").strip()
    if exe:
        out.append(exe)
        base = os.path.dirname(exe)
        if base:
            out.append(os.path.join(base, "pythonw.exe"))
            out.append(os.path.join(base, "python.exe"))
    # Common Inkscape portable layout used by this project.
    out.append(os.path.expandvars(r"%USERPROFILE%\inkscape\bin\pythonw.exe"))
    out.append(os.path.expandvars(r"%USERPROFILE%\inkscape\bin\python.exe"))

    seen = set()
    good = []
    for path in out:
        norm = os.path.normpath(path)
        key = norm.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if os.path.isfile(norm) and os.path.getsize(norm) > 0:
                good.append(norm)
        except Exception:
            continue
    return good


def notify_or_launch(
    app_script: str,
    template: str,
    sheet_id: str = "",
    sheet_range: str = "",
    log_level: str = "global",
    dataset_source_mode: str = "",
) -> bool:
    req = AppRequest(
        template=DMPATHS.normalize(template),
        sheet_id=str(sheet_id or "").strip(),
        sheet_range=str(sheet_range or "").strip(),
        dataset_source_mode=str(dataset_source_mode or "").strip().lower(),
        log_level=str(log_level or "global").strip() or "global",
    )
    if send_request(req):
        _l.i(f"[deckmaker_app] notified resident app template='{req.template}'")
        return True

    script = os.path.abspath(app_script)
    args_tail = [
        script,
        "--template", req.template,
        "--sheet-id", req.sheet_id,
        "--sheet-range", req.sheet_range,
        "--dataset-source-mode", req.dataset_source_mode,
        "--log-level", req.log_level,
    ]
    env = os.environ.copy()
    env[ENV_DIRECT_RUN] = "1"

    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

    last_error = None
    for py in candidate_python_launchers():
        try:
            proc = subprocess.Popen(
                [py] + args_tail,
                cwd=os.path.dirname(script),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=flags,
            )
            # Avoid ResourceWarning in Inkscape's extension runner; we intentionally detach.
            proc.returncode = 0
            _l.i(f"[deckmaker_app] launched resident app python='{py}' template='{req.template}'")
            return True
        except Exception as ex:
            last_error = ex
            continue
    _l.w(f"[deckmaker_app] launch failed: {last_error}")
    return False


def start_server(request_queue, stop_event) -> threading.Thread:
    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((HOST, PORT))
        srv.listen(5)
        srv.settimeout(0.4)
        try:
            while not stop_event.is_set():
                try:
                    conn, _addr = srv.accept()
                except socket.timeout:
                    continue
                with conn:
                    data = b""
                    while b"\n" not in data:
                        chunk = conn.recv(8192)
                        if not chunk:
                            break
                        data += chunk
                    try:
                        msg = json.loads(data.decode("utf-8").strip() or "{}")
                        if msg.get("cmd") != "open":
                            conn.sendall(b"ERR\n")
                            continue
                        request_queue.put(AppRequest(
                            template=DMPATHS.normalize(msg.get("template") or ""),
                            sheet_id=str(msg.get("sheet_id") or "").strip(),
                            sheet_range=str(msg.get("sheet_range") or "").strip(),
                            dataset_source_mode=str(msg.get("dataset_source_mode") or "").strip().lower(),
                            log_level=str(msg.get("log_level") or "global").strip() or "global",
                        ))
                        conn.sendall(b"OK\n")
                    except Exception:
                        conn.sendall(b"ERR\n")
        finally:
            srv.close()

    thread = threading.Thread(target=serve, name="pnpink-deckmaker-app-server", daemon=True)
    thread.start()
    return thread
