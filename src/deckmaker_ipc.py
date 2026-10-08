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
WEB_PORT = 48752
ENV_DIRECT_RUN = "PNPINK_DECKMAKER_DIRECT"

_l = LOG


def send_request(req: AppRequest, timeout: float = 0.35, *, port: int = PORT) -> bool:
    payload = {
        "cmd": "open",
        "template": req.template,
        "snapshot_path": req.snapshot_path,
        "sheet_id": req.sheet_id,
        "sheet_range": req.sheet_range,
        "dataset_source_mode": req.dataset_source_mode,
        "log_level": req.log_level,
        "autorun": bool(req.autorun),
        "selected_ids": list(req.selected_ids),
    }
    try:
        with socket.create_connection((HOST, int(port)), timeout=timeout) as s:
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
        if os.name == "nt" and base:
            out.append(os.path.join(base, "pythonw.exe"))
            out.append(os.path.join(base, "python.exe"))
    if os.name == "nt":
        # Common Inkscape portable layout used by this project.
        out.append(os.path.expandvars(r"%USERPROFILE%\inkscape\bin\pythonw.exe"))
        out.append(os.path.expandvars(r"%USERPROFILE%\inkscape\bin\python.exe"))

    seen = set()
    good = []
    for path in out:
        norm = os.path.normpath(path)
        key = os.path.normcase(norm)
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
    snapshot_path: str = "",
    sheet_id: str = "",
    sheet_range: str = "",
    log_level: str = "global",
    dataset_source_mode: str = "",
    autorun: bool = False,
    selected_ids: tuple[str, ...] = (),
    port: int = PORT,
) -> bool:
    req = AppRequest(
        template=DMPATHS.normalize(template),
        snapshot_path=DMPATHS.normalize(snapshot_path) if str(snapshot_path or "").strip() else "",
        sheet_id=str(sheet_id or "").strip(),
        sheet_range=str(sheet_range or "").strip(),
        dataset_source_mode=str(dataset_source_mode or "").strip().lower(),
        log_level=str(log_level or "global").strip() or "global",
        autorun=bool(autorun),
        selected_ids=tuple(str(value).strip() for value in selected_ids if str(value).strip()),
    )
    if send_request(req, port=port):
        _l.i(f"[deckmaker_app] notified resident app template='{req.template}'")
        return True

    script = os.path.abspath(app_script)
    args_tail = [
        script,
        "--template", req.template,
        "--snapshot-path", req.snapshot_path,
        "--sheet-id", req.sheet_id,
        "--sheet-range", req.sheet_range,
        "--dataset-source-mode", req.dataset_source_mode,
        "--log-level", req.log_level,
        "--autorun" if req.autorun else "--no-autorun",
    ]
    if req.selected_ids:
        args_tail.extend(["--selected-ids-json", json.dumps(list(req.selected_ids), ensure_ascii=False)])
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
            kwargs = {
                "args": [py] + args_tail,
                "cwd": os.path.dirname(script),
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = flags
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(**kwargs)
            # Avoid ResourceWarning in Inkscape's extension runner; we intentionally detach.
            proc.returncode = 0
            _l.i(f"[deckmaker_app] launched resident app python='{py}' template='{req.template}'")
            return True
        except Exception as ex:
            last_error = ex
            continue
    _l.w(f"[deckmaker_app] launch failed: {last_error}")
    return False


def start_server(request_queue, stop_event, *, port: int = PORT) -> threading.Thread:
    def serve():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_error = None
        for _attempt in range(30):
            try:
                srv.bind((HOST, int(port)))
                bind_error = None
                break
            except OSError as error:
                bind_error = error
                if stop_event.wait(0.1):
                    srv.close()
                    return
        if bind_error is not None:
            srv.close()
            raise bind_error
        srv.listen(5)
        srv.settimeout(0.4)
        try:
            while not stop_event.is_set():
                try:
                    conn, _addr = srv.accept()
                except socket.timeout:
                    continue
                if stop_event.is_set():
                    with conn:
                        try:
                            conn.sendall(b"ERR\n")
                        except OSError:
                            pass
                    break
                with conn:
                    conn.settimeout(0.5)
                    data = b""
                    try:
                        while b"\n" not in data:
                            chunk = conn.recv(8192)
                            if not chunk:
                                break
                            data += chunk
                    except socket.timeout:
                        continue
                    try:
                        msg = json.loads(data.decode("utf-8").strip() or "{}")
                        if msg.get("cmd") != "open":
                            conn.sendall(b"ERR\n")
                            continue
                        request_queue.put(AppRequest(
                            template=DMPATHS.normalize(msg.get("template") or ""),
                            snapshot_path=DMPATHS.normalize(msg.get("snapshot_path") or "") if str(msg.get("snapshot_path") or "").strip() else "",
                            sheet_id=str(msg.get("sheet_id") or "").strip(),
                            sheet_range=str(msg.get("sheet_range") or "").strip(),
                            dataset_source_mode=str(msg.get("dataset_source_mode") or "").strip().lower(),
                            log_level=str(msg.get("log_level") or "global").strip() or "global",
                            autorun=bool(msg.get("autorun")),
                            selected_ids=tuple(
                                str(value).strip() for value in (msg.get("selected_ids") or [])
                                if str(value).strip()
                            ),
                        ))
                        conn.sendall(b"OK\n")
                    except Exception:
                        conn.sendall(b"ERR\n")
        finally:
            srv.close()

    thread = threading.Thread(target=serve, name="pnpink-deckmaker-app-server", daemon=True)
    thread.start()
    return thread
