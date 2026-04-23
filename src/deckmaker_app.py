#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resident DeckMaker launcher app.

This is intentionally small: the Inkscape extension only sends the current SVG
template to this process, and the process runs the existing engine on demand.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import log as LOG

_l = LOG

HOST = "127.0.0.1"
PORT = 48751
ENV_DIRECT_RUN = "PNPINK_DECKMAKER_DIRECT"


@dataclass
class AppRequest:
    template: str
    sheet_id: str = ""
    sheet_range: str = ""
    log_level: str = "global"


def _normalize_path(path: str) -> str:
    return os.path.normpath(os.path.abspath(str(path or "").strip()))


def _app_icon_path() -> str:
    return os.path.join(os.path.dirname(__file__), "examples", "assets", "deckmaker_icon.png")


def _send_request(req: AppRequest, timeout: float = 0.35) -> bool:
    payload = {
        "cmd": "open",
        "template": req.template,
        "sheet_id": req.sheet_id,
        "sheet_range": req.sheet_range,
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


def _candidate_python_launchers() -> list[str]:
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
    for p in out:
        pp = os.path.normpath(p)
        key = pp.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if os.path.isfile(pp) and os.path.getsize(pp) > 0:
                good.append(pp)
        except Exception:
            continue
    return good


def notify_or_launch(template: str, sheet_id: str = "", sheet_range: str = "", log_level: str = "global") -> bool:
    req = AppRequest(
        template=_normalize_path(template),
        sheet_id=str(sheet_id or "").strip(),
        sheet_range=str(sheet_range or "").strip(),
        log_level=str(log_level or "global").strip() or "global",
    )
    if _send_request(req):
        _l.i(f"[deckmaker_app] notified resident app template='{req.template}'")
        return True

    script = os.path.abspath(__file__)
    args_tail = [
        script,
        "--template", req.template,
        "--sheet-id", req.sheet_id,
        "--sheet-range", req.sheet_range,
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
    for py in _candidate_python_launchers():
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


class _EngineEffect:
    def __init__(self, template: str, sheet_id: str, sheet_range: str, log_level: str):
        import inkex

        self._template = _normalize_path(template)
        with open(self._template, "rb") as fh:
            raw = fh.read()
        self.document = inkex.load_svg(raw)
        self.svg = self.document.getroot()
        self.options = SimpleNamespace(
            tab="data",
            csv_path="",
            sheet_id=str(sheet_id or "").strip(),
            sheet_range=str(sheet_range or "").strip(),
            prototypes_layer="Prototypes",
            preset="{A4}",
            stop_on_error=False,
            log_level=str(log_level or "global").strip() or "global",
        )

    def document_path(self) -> str:
        return self._template

    def _document_path_or_abort(self) -> str:
        import inkex

        if not self._template or not os.path.isfile(self._template):
            raise inkex.AbortExtension("Save the SVG template before running DeckMaker.")
        return self._template

    def _find_or_create_layer(self, root, label: str):
        import inkex

        for child in list(root):
            try:
                if not (hasattr(child, "tag") and isinstance(child.tag, str) and child.tag.endswith("g")):
                    continue
                if child.get(inkex.addNS("groupmode", "inkscape")) == "layer":
                    if (child.get(inkex.addNS("label", "inkscape")) or "") == label:
                        return child
            except Exception:
                continue
        layer = inkex.Group()
        layer.set(inkex.addNS("groupmode", "inkscape"), "layer")
        layer.set(inkex.addNS("label", "inkscape"), label)
        root.append(layer)
        return layer


class DeckMakerApp:
    def __init__(self, initial: Optional[AppRequest] = None):
        import tkinter as tk
        from tkinter import scrolledtext
        from tkinter import ttk

        self.tk = tk
        self.scrolledtext = scrolledtext
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("PnPInk DeckMaker")
        self.root.geometry("680x360")
        self.root.minsize(560, 300)
        self._icon_image = None
        self._apply_window_icon()

        self._queue: "queue.Queue[AppRequest]" = queue.Queue()
        self._server_stop = threading.Event()
        self._render_thread: Optional[threading.Thread] = None

        self.template_var = tk.StringVar(value=(initial.template if initial else ""))
        self.sheet_id_var = tk.StringVar(value=(initial.sheet_id if initial else ""))
        self.sheet_range_var = tk.StringVar(value=(initial.sheet_range if initial else ""))
        self.status_var = tk.StringVar(value="Ready")
        self._warm_sheet_id = ""
        self._request_serial = 0
        self._autorun_serial = 0

        self._build_ui()
        if initial:
            self._set_request(initial)
        self._start_server()
        self.root.after(150, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.sheet_id_var.trace_add("write", lambda *_: self._schedule_auth_warmup())

    def _build_ui(self):
        tk = self.tk
        scrolledtext = self.scrolledtext
        ttk = self.ttk

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(frame, text="Template SVG").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.template_var, state="readonly").grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="GSheet ID").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.sheet_id_var).grid(row=1, column=1, sticky="ew", pady=4)

        ttk.Label(frame, text="Range / gid").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(frame, textvariable=self.sheet_range_var).grid(row=2, column=1, sticky="ew", pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 4))
        buttons.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(buttons, text="Run", command=self._run_clicked)
        self.run_btn.grid(row=0, column=0, sticky="w")
        ttk.Label(buttons, textvariable=self.status_var).grid(row=0, column=1, sticky="w", padx=(12, 0))

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.log_text = scrolledtext.ScrolledText(frame, height=7, wrap="word", state="disabled")
        self.log_text.grid(row=5, column=0, columnspan=2, sticky="nsew", pady=(10, 0))

    def _apply_window_icon(self):
        icon_path = _app_icon_path()
        if not os.path.isfile(icon_path):
            return
        try:
            self._icon_image = self.tk.PhotoImage(file=icon_path)
            self.root.iconphoto(True, self._icon_image)
        except Exception:
            self._icon_image = None

    def _log(self, message: str):
        text = str(message or "").strip()
        if not text:
            return
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _set_request(self, req: AppRequest):
        self._request_serial += 1
        serial = self._request_serial
        self.template_var.set(_normalize_path(req.template))
        if req.sheet_id:
            self.sheet_id_var.set(req.sheet_id)
        if req.sheet_range:
            self.sheet_range_var.set(req.sheet_range)
        self.status_var.set("Template received")
        self._log(f"Template: {os.path.basename(_normalize_path(req.template))}")
        if req.sheet_id:
            detail = f" range={req.sheet_range}" if req.sheet_range else ""
            self._log(f"Google Sheets source ready{detail}")
        self._schedule_auth_warmup()
        try:
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        self.root.after(120, lambda: self._autorun(serial))

    def _schedule_auth_warmup(self):
        sheet_id = self.sheet_id_var.get().strip()
        if not sheet_id or sheet_id == self._warm_sheet_id:
            return
        self._warm_sheet_id = sheet_id
        self.root.after(600, self._auth_warmup)

    def _auth_warmup(self):
        sheet_id = self.sheet_id_var.get().strip()
        if not sheet_id or sheet_id != self._warm_sheet_id:
            return

        def worker():
            try:
                import gsheets_client_pkce as GS

                self.root.after(0, lambda: self._log("Checking Google Sheets session..."))
                ok = GS.warm_session()
                if ok:
                    self.root.after(0, lambda: self.status_var.set("Google Sheets session ready"))
                    self.root.after(0, lambda: self._log("Google Sheets session ready"))
            except Exception:
                pass

        threading.Thread(target=worker, name="pnpink-gsheets-auth-warmup", daemon=True).start()

    def _start_server(self):
        def serve():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((HOST, PORT))
            srv.listen(5)
            srv.settimeout(0.4)
            try:
                while not self._server_stop.is_set():
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
                            if msg.get("cmd") == "open":
                                self._queue.put(AppRequest(
                                    template=_normalize_path(msg.get("template") or ""),
                                    sheet_id=str(msg.get("sheet_id") or "").strip(),
                                    sheet_range=str(msg.get("sheet_range") or "").strip(),
                                    log_level=str(msg.get("log_level") or "global").strip() or "global",
                                ))
                                conn.sendall(b"OK\n")
                            else:
                                conn.sendall(b"ERR\n")
                        except Exception:
                            conn.sendall(b"ERR\n")
            finally:
                srv.close()

        threading.Thread(target=serve, name="pnpink-deckmaker-app-server", daemon=True).start()

    def _drain_queue(self):
        try:
            while True:
                self._set_request(self._queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(150, self._drain_queue)

    def _autorun(self, serial: int):
        if serial <= self._autorun_serial:
            return
        self._autorun_serial = serial
        if self._render_thread and self._render_thread.is_alive():
            return
        if not _normalize_path(self.template_var.get()) or not os.path.isfile(_normalize_path(self.template_var.get())):
            return
        self._run_clicked(autorun=True)

    def _run_clicked(self, autorun: bool = False):
        if self._render_thread and self._render_thread.is_alive():
            return
        template = _normalize_path(self.template_var.get())
        if not template or not os.path.isfile(template):
            self.status_var.set("Save/open a template SVG first")
            self._log("Save/open a template SVG first")
            return
        req = AppRequest(
            template=template,
            sheet_id=self.sheet_id_var.get().strip(),
            sheet_range=self.sheet_range_var.get().strip(),
        )
        _l.i(f"[deckmaker_app] run clicked template='{req.template}' sheet_id={'yes' if req.sheet_id else 'no'} range='{req.sheet_range}'")
        self.run_btn.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("Running...")
        self._log(("Auto run" if autorun else "Run") + " started")
        self._render_thread = threading.Thread(target=self._render_worker, args=(req,), daemon=True)
        self._render_thread.start()

    def _render_worker(self, req: AppRequest):
        try:
            import dataset_state as DSTATE
            import engine as ENG
            from deckmaker import __version__ as deckmaker_version

            self.root.after(0, lambda: self._log("Loading template and dataset..."))
            effect = _EngineEffect(req.template, req.sheet_id, req.sheet_range, req.log_level)
            self.root.after(0, lambda: self._log("Rendering output SVG..."))
            ENG.run(effect, deckmaker_version)
            try:
                access_mode = str(getattr(effect.options, "_dataset_access_mode", "") or "").strip().lower()
                if req.sheet_id:
                    DSTATE.set_gsheet_for_svg(req.template, req.sheet_id, req.sheet_range, access_mode)
            except Exception:
                _l.w("[deckmaker_app] dataset state save failed", exc_info=True)
            self.root.after(0, lambda: self._render_done("Done"))
        except Exception as ex:
            _l.w("[deckmaker_app] render failed:\n" + traceback.format_exc())
            self.root.after(0, lambda: self._render_done(f"Error: {ex}"))

    def _render_done(self, status: str):
        self.progress.stop()
        self.run_btn.configure(state="normal")
        self.status_var.set(status)
        self._log(status)

    def _on_close(self):
        self._server_stop.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="")
    ap.add_argument("--sheet-id", default="")
    ap.add_argument("--sheet-range", default="")
    ap.add_argument("--log-level", default="global")
    ns = ap.parse_args(argv)

    initial = None
    if ns.template:
        initial = AppRequest(
            template=_normalize_path(ns.template),
            sheet_id=ns.sheet_id,
            sheet_range=ns.sheet_range,
            log_level=ns.log_level,
        )
    app = DeckMakerApp(initial)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
