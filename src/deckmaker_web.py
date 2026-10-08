# -*- coding: utf-8 -*-
"""Browser-based DeckMaker UI served by the Inkscape Python runtime."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import queue
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request

_inkscape_extensions = Path(sys.executable).resolve().parent.parent / "share" / "inkscape" / "extensions"
if _inkscape_extensions.is_dir() and str(_inkscape_extensions) not in sys.path:
    sys.path.append(str(_inkscape_extensions))

import deckmaker_ipc as IPC
import deckmaker_paths as DMPATHS
from deckmaker_service import DeckMakerService
from deckmaker_types import AppRequest
import prefs
import temp_paths as TEMPPATHS
from web_app import open_web_app


HTTP_HOST = "127.0.0.1"
HTTP_PORT = int(os.environ.get("PNPINK_DECKMAKER_WEB_PORT") or 48753)
ASSET_DIR = Path(__file__).resolve().parent / "assets" / "deckmaker_web"


def _open_browser(url: str) -> bool:
    prefs.reload()
    separator = "&" if "?" in url else "?"
    return open_web_app(
        f"{url}{separator}launch={time.time_ns()}",
        preferred_browser=prefs.get_deckmaker_web_browser(),
    )


def _wait_until_listening(timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HTTP_HOST, HTTP_PORT), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _browser_window_active() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{HTTP_HOST}:{HTTP_PORT}/api/browser-active", timeout=0.25,
        ) as response:
            return bool(json.loads(response.read().decode("utf-8")).get("active"))
    except Exception:
        return False


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class DeckMakerWebHost:
    SESSION_TIMEOUT = 60.0
    CLOSE_GRACE = 2.0
    STARTUP_TIMEOUT = 30.0

    def __init__(self, initial: AppRequest | None = None):
        self.token = os.environ.get("PNPINK_DECKMAKER_WEB_TOKEN") or secrets.token_urlsafe(24)
        self._session_lock = threading.Lock()
        self._sessions: dict[str, dict[str, object]] = {}
        self._started_at = time.monotonic()
        self._browser_expected_until = self._started_at + self.STARTUP_TIMEOUT
        self._empty_since: float | None = None
        self._had_session = False
        self.service = DeckMakerService(initial)
        self.requests: queue.Queue[AppRequest] = queue.Queue()
        self.stop_event = threading.Event()
        self.httpd = LocalThreadingHTTPServer((HTTP_HOST, HTTP_PORT), self._handler_type())
        self.ipc_thread = IPC.start_server(self.requests, self.stop_event, port=IPC.WEB_PORT)

    def browser_opened(self, session_id: str, *, visible: bool = True) -> None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._session_lock:
            now = time.monotonic()
            self._sessions[session_id] = {
                "seen_at": now,
                "visible": bool(visible),
                "hidden_since": None if visible else now,
            }
            self._browser_expected_until = now
            self._empty_since = None
            self._had_session = True

    def browser_touched(self, session_id: str) -> None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._session_lock:
            session = self._sessions.get(session_id)
            now = time.monotonic()
            if session is None:
                self._sessions[session_id] = {
                    "seen_at": now,
                    "visible": True,
                    "hidden_since": None,
                }
                self._had_session = True
                self._empty_since = None
            else:
                session["seen_at"] = now

    def browser_visibility(self, session_id: str, visible: bool) -> None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._session_lock:
            session = self._sessions.get(session_id)
            now = time.monotonic()
            if session is None:
                session = {
                    "seen_at": now,
                    "visible": bool(visible),
                    "hidden_since": None if visible else now,
                }
                self._sessions[session_id] = session
                self._had_session = True
                self._empty_since = None
            session["seen_at"] = now
            session["visible"] = bool(visible)
            session["hidden_since"] = None if visible else (session.get("hidden_since") or now)
            if visible:
                self._empty_since = None

    def browser_closed(self, session_id: str) -> None:
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._session_lock:
            self._sessions.pop(session_id, None)
            if self._had_session and not self._sessions and self._empty_since is None:
                self._empty_since = time.monotonic()

    def expect_browser(self) -> None:
        with self._session_lock:
            self._browser_expected_until = time.monotonic() + self.STARTUP_TIMEOUT
            self._empty_since = None

    def browser_active(self) -> bool:
        now = time.monotonic()
        with self._session_lock:
            expired = [
                key for key, session in self._sessions.items()
                if now - float(session.get("seen_at") or 0.0) >= self.SESSION_TIMEOUT
            ]
            for key in expired:
                self._sessions.pop(key, None)
            if self._had_session and not self._sessions and self._empty_since is None:
                self._empty_since = now
            return any(bool(session.get("visible")) for session in self._sessions.values())

    def browser_present(self) -> bool:
        self.browser_active()
        with self._session_lock:
            return bool(self._sessions)

    def _session_id(self, handler) -> str:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
        return str(
            handler.headers.get("X-DeckMaker-Session")
            or (query.get("session") or [""])[0]
        ).strip()

    @property
    def url(self) -> str:
        return f"http://{HTTP_HOST}:{HTTP_PORT}/?token={urllib.parse.quote(self.token)}"

    def current_url(self) -> str:
        sequence = int(self.service.state().get("seq") or 0)
        return f"{self.url}&request={sequence}"

    def _handler_type(self):
        host = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PnPInkDeckMaker/1"

            def log_message(self, _format, *_args):
                return

            def _authorized(self) -> bool:
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                supplied = self.headers.get("X-DeckMaker-Token", "") or (query.get("token") or [""])[0]
                return secrets.compare_digest(str(supplied), host.token)

            def _json(self, payload, status=HTTPStatus.OK):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(int(status))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> dict:
                size = int(self.headers.get("Content-Length") or 0)
                if size <= 0:
                    return {}
                value = json.loads(self.rfile.read(size).decode("utf-8"))
                return value if isinstance(value, dict) else {}

            def _asset(self, relative: str):
                target = (ASSET_DIR / relative).resolve()
                if ASSET_DIR.resolve() not in target.parents and target != ASSET_DIR.resolve():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not target.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                raw = target.read_bytes()
                content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(raw)

            def _file(self, path: str, content_type: str, *, cache_control: str = "private, max-age=3600"):
                target = Path(path)
                if not target.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                raw = target.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", cache_control)
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/api/browser-active":
                    self._json({"active": host.browser_active()})
                    return
                if parsed.path.startswith("/api/") and not self._authorized():
                    self._json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                if parsed.path.startswith("/api/"):
                    host.browser_touched(host._session_id(self))
                if parsed.path == "/api/state":
                    self._json(host.service.state())
                    return
                if parsed.path == "/api/events":
                    query = urllib.parse.parse_qs(parsed.query)
                    after = int((query.get("after") or ["0"])[0] or 0)
                    timeout = float((query.get("timeout") or ["20"])[0] or 20)
                    self._json(host.service.wait_events(after, timeout))
                    return
                if parsed.path == "/api/spritesheet/preview":
                    try:
                        self._file(host.service.spritesheet_preview(), "image/png")
                    except Exception as error:
                        self.send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                if parsed.path == "/api/preview/image":
                    try:
                        self._file(host.service.preview_image(), "image/png", cache_control="no-store, max-age=0")
                    except Exception as error:
                        self.send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                if parsed.path == "/api/template/preview":
                    try:
                        self._file(host.service.template_preview(), "image/svg+xml")
                    except Exception as error:
                        self.send_error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                if parsed.path == "/api/template/headers":
                    self._json(host.service.template_headers())
                    return
                if parsed.path in {"", "/"}:
                    if not self._authorized():
                        query = urllib.parse.parse_qs(parsed.query)
                        location = host.current_url()
                        if (query.get("shell") or [""])[0] == "app":
                            separator = "&" if "?" in location else "?"
                            location = f"{location}{separator}shell=app"
                        self.send_response(HTTPStatus.FOUND)
                        self.send_header("Location", location)
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        return
                    self._asset("index.html")
                    return
                if parsed.path.startswith("/shared-assets/"):
                    relative = parsed.path.removeprefix("/shared-assets/")
                    target = (ASSET_DIR.parent / relative).resolve()
                    if target.parent != ASSET_DIR.parent.resolve():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    self._file(str(target), mimetypes.guess_type(str(target))[0] or "application/octet-stream")
                    return
                self._asset(parsed.path.lstrip("/"))

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                if not self._authorized():
                    self._json({"error": "Unauthorized"}, HTTPStatus.UNAUTHORIZED)
                    return
                try:
                    body = self._body()
                    session_id = host._session_id(self)
                    if parsed.path == "/api/session/open":
                        host.browser_opened(session_id, visible=bool(body.get("visible", True)))
                        self._json({"ok": True})
                        return
                    if parsed.path == "/api/session/close":
                        host.browser_closed(session_id)
                        self._json({"ok": True})
                        return
                    if parsed.path == "/api/session/activity":
                        host.browser_visibility(session_id, bool(body.get("visible")))
                        self._json({"ok": True})
                        return
                    host.browser_touched(session_id)
                    if parsed.path == "/api/settings":
                        self._json(host.service.update(body))
                        return
                    if parsed.path == "/api/sheet-activity":
                        self._json(host.service.set_sheet_editor_activity(bool(body.get("active"))))
                        return
                    if parsed.path == "/api/command":
                        accepted = host.service.command(str(body.get("command") or ""))
                        self._json({"accepted": accepted, "state": host.service.state()})
                        return
                    self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                except Exception as error:
                    self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

        return Handler

    def run(self) -> None:
        threading.Thread(target=self._drain_requests, name="deckmaker-web-ipc", daemon=True).start()
        threading.Thread(target=self._watch_browser_sessions, name="deckmaker-web-lifecycle", daemon=True).start()
        try:
            self.httpd.serve_forever(poll_interval=0.25)
        finally:
            self.stop_event.set()
            self.httpd.server_close()
            self.ipc_thread.join(timeout=1.0)
            self.service.close()

    def _watch_browser_sessions(self) -> None:
        while not self.stop_event.wait(0.5):
            present = self.browser_present()
            now = time.monotonic()
            with self._session_lock:
                empty_since = self._empty_since
                had_session = self._had_session
                browser_expected_until = self._browser_expected_until
            should_stop = (
                had_session
                and not present
                and empty_since is not None
                and now >= browser_expected_until
                and now - empty_since >= self.CLOSE_GRACE
            )
            never_opened = not had_session and now >= browser_expected_until
            if should_stop or never_opened:
                self.stop_event.set()
                self.httpd.shutdown()
                return

    def _drain_requests(self) -> None:
        while not self.stop_event.wait(0.15):
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                continue
            self.expect_browser()
            self.service.open_request(request)


def notify_or_launch(
    template: str,
    snapshot_path: str = "",
    sheet_id: str = "",
    sheet_range: str = "",
    log_level: str = "global",
    dataset_source_mode: str = "",
    autorun: bool = False,
    selected_ids: tuple[str, ...] = (),
) -> bool:
    try:
        TEMPPATHS.cleanup_runs_now(keep_paths=[snapshot_path] if snapshot_path else None)
    except Exception:
        pass
    browser_active = _browser_window_active()
    launched = IPC.notify_or_launch(
        __file__, template, snapshot_path, sheet_id, sheet_range, log_level,
        dataset_source_mode, autorun, port=IPC.WEB_PORT,
        selected_ids=selected_ids,
    )
    if launched and not browser_active and not os.environ.get("PNPINK_DECKMAKER_WEB_NO_BROWSER"):
        if not _wait_until_listening():
            return False
        if not _open_browser(f"http://{HTTP_HOST}:{HTTP_PORT}/"):
            return False
    return launched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default="")
    parser.add_argument("--snapshot-path", default="")
    parser.add_argument("--sheet-id", default="")
    parser.add_argument("--sheet-range", default="")
    parser.add_argument("--dataset-source-mode", default="")
    parser.add_argument("--log-level", default="global")
    parser.add_argument("--autorun", dest="autorun", action="store_true", default=False)
    parser.add_argument("--no-autorun", dest="autorun", action="store_false")
    parser.add_argument("--selected-ids-json", default="[]")
    options = parser.parse_args(argv)
    try:
        selected_ids = tuple(str(value).strip() for value in json.loads(options.selected_ids_json) if str(value).strip())
    except Exception:
        selected_ids = ()
    template = DMPATHS.normalize(options.template or prefs.get("deckmaker_web_last_template", ""))
    initial = AppRequest(
        template=template,
        snapshot_path=DMPATHS.normalize(options.snapshot_path) if options.snapshot_path else "",
        sheet_id=options.sheet_id,
        sheet_range=options.sheet_range,
        dataset_source_mode=options.dataset_source_mode,
        log_level=options.log_level,
        autorun=bool(options.autorun),
        selected_ids=selected_ids,
    ) if template and os.path.isfile(template) else None
    if _wait_until_listening(timeout=0.1):
        if initial:
            IPC.send_request(initial, port=IPC.WEB_PORT)
        if not _browser_window_active() and not os.environ.get("PNPINK_DECKMAKER_WEB_NO_BROWSER"):
            _open_browser(f"http://{HTTP_HOST}:{HTTP_PORT}/")
        return 0
    host = DeckMakerWebHost(initial)
    if not os.environ.get(IPC.ENV_DIRECT_RUN) and not os.environ.get("PNPINK_DECKMAKER_WEB_NO_BROWSER"):
        threading.Timer(0.1, _open_browser, args=(host.current_url(),)).start()
    host.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
