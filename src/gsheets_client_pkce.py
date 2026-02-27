#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [2026-02-19] Chore: translate comments to English.
"""
gsheets_client_pkce.py v2.0 ? Google Sheets read-only client (stable PKCE loopback)

Compatibility with the ORIGINAL client:
- Tokens in %APPDATA%/PnPInk/gsheets/tokens.json (Windows)
  macOS: ~/Library/Application Support/PnPInk/gsheets/tokens.json
  Linux: ~/.pnpink/gsheets/tokens.json
- Same store key: "google_sheets_pkce"
- Legacy expiry field: "expiry" (int, epoch seconds)

PKCE robustness:
- Loopback server starts BEFORE opening the browser (avoids race condition).
- /__ping route checks it is listening (does not consume the "code" callback).
- Clean shutdown and configurable timeout.

Refresh:
- If CLIENT_SECRET is present, it is sent on refresh and code exchange (as in the original),
  avoiding "client_secret is missing" with tokens issued by clients that require it.

Public API:
    * fetch_sheet(spreadsheet_id, range_a1, client_id=None) -> 2D list[str]
    * list_sheet_titles(spreadsheet_id, client_id=None) -> list[str]

Environment variables (optional):
    - PNPINK_GSHEETS_CLIENT_ID
    - PNPINK_GSHEETS_CLIENT_SECRET
    - PNPINK_GSHEETS_FORCE_IPV4=1|0     (default 1 on Windows; uses 127.0.0.1)
    - PNPINK_GSHEETS_LOOPBACK_PORT=NNNN (force loopback port; otherwise auto)
    - PNPINK_GSHEETS_AUTH_TIMEOUT=120   (loopback wait seconds)
"""
from __future__ import annotations

__version__ = 'v_1.0'
import log as LOG
_l = LOG
_l.i('gsheets ', __version__)

import base64, hashlib, json, os, random, socket, sys, time, threading, urllib.parse, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, Optional, Tuple, Any

try:
    import requests  # type: ignore
except Exception as e:
    raise RuntimeError("gsheets_client_pkce: 'requests' is required") from e

# ------------------------------------------------------------------
# Config (IMPORT-TIME) — hardcoded con override opcional por entorno
# ------------------------------------------------------------------

CLIENT_ID: str = os.environ.get(
    "PNPINK_GSHEETS_CLIENT_ID",
    "882767426843-fjlpn53eafi6tgqi2ks9ij0oj9tigk2i.apps.googleusercontent.com"
)
CLIENT_SECRET: str = os.environ.get(
    "PNPINK_GSHEETS_CLIENT_SECRET",
    "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"
)

AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL  = "https://oauth2.googleapis.com/token"
SCOPES     = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
USER_AGENT = "PnPInk-GSheetsPKCE/1.6"

# On Windows IPv4 tends to work better; enabled by default (disable with =0)
FORCE_IPV4     = os.environ.get("PNPINK_GSHEETS_FORCE_IPV4", "1") != "0"
FIXED_PORT_ENV = int(os.environ.get("PNPINK_GSHEETS_LOOPBACK_PORT", "0"))  # 0 = auto
AUTH_TIMEOUT_S = int(os.environ.get("PNPINK_GSHEETS_AUTH_TIMEOUT", "120")) # segundos

# ------------------------------------------------------------------
# Token store — **identical** to the original
# ------------------------------------------------------------------
def _legacy_app_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PnPInk", "gsheets")
    elif sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~/Library/Application Support"), "PnPInk", "gsheets")
    else:
        return os.path.join(os.path.expanduser("~/.pnpink"), "gsheets")

TOKENS_FILE = os.path.join(_legacy_app_dir(), "tokens.json")  # como el original

def _load_store() -> Dict[str, Any]:
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _save_store(store: Dict[str, Any]) -> None:
    d = os.path.dirname(TOKENS_FILE)
    os.makedirs(d, exist_ok=True)
    tmp = TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True)
    os.replace(tmp, TOKENS_FILE)

def _get_entry() -> Dict[str, Any]:
    return _load_store().get("google_sheets_pkce", {})

def _set_entry(entry: Dict[str, Any]) -> None:
    store = _load_store()
    store["google_sheets_pkce"] = entry
    _save_store(store)

# ------------------------------------------------------------------
# Utils
# ------------------------------------------------------------------
def _now() -> int:
    return int(time.time())

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def _rand_str(n: int = 64) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
    return "".join(random.choice(alphabet) for _ in range(n))

def _pkce_pair() -> Tuple[str, str]:
    verifier = _rand_str(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge

def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception:
        pass

# ------------------------------------------------------------------
# Loopback receiver (PKCE) — server listo antes de abrir navegador
# ------------------------------------------------------------------
class _CodeHandler(BaseHTTPRequestHandler):
    code: Optional[str] = None
    err: Optional[str] = None

    def do_GET(self):
        try:
            # /__ping → healthcheck (no consume el callback de auth)
            if self.path.startswith("/__ping"):
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            if "code" in q:
                _CodeHandler.code = q.get("code", [""])[0]
                body = b"<html><body><h2>Autorizado. Puedes volver a PnPInk.</h2></body></html>"
                self.send_response(200)
            elif "error" in q:
                _CodeHandler.err = q.get("error", ["unknown_error"])[0]
                body = b"<html><body><h2>Autorizacion cancelada o con error.</h2></body></html>"
                self.send_response(200)
            else:
                body = b"<html><body><h3>Falta 'code' en la URL.</h3></body></html>"
                self.send_response(400)

            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            # only close the server if we already received code or error
            if _CodeHandler.code is not None or _CodeHandler.err is not None:
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *_):  # silenciar
        return

class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True

def _pick_loopback_port() -> int:
    if FIXED_PORT_ENV:
        return FIXED_PORT_ENV
    # Preferidos (deterministas), sino 0 = SO elige
    for p in (53682, 8787, 8090, 8085):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except Exception:
                continue
    return 0

def _authorize_with_pkce(client_id: str) -> Dict[str, Any]:
    """
    Levanta el server loopback, luego abre el navegador, y espera el código con timeout.
    Evita la condición de carrera en la que el navegador redirige antes de que el server escuche.
    """
    host = "127.0.0.1" if FORCE_IPV4 else "localhost"
    wanted_port = _pick_loopback_port()

    # 1) Start server *BEFORE* opening the browser. If wanted_port=0, the OS chooses.
    server = _ReusableHTTPServer((host, wanted_port), _CodeHandler)
    actual_port = server.server_port  # si era 0, aquí tenemos el real
    redirect_uri = f"http://{host}:{actual_port}/"

    th = threading.Thread(target=server.serve_forever, daemon=True); th.start()

    try:
        # Local health-check: /__ping (does not consume the callback)
        try:
            import http.client
            conn = http.client.HTTPConnection(host, actual_port, timeout=3)
            conn.request("GET", "/__ping")
            resp = conn.getresponse(); conn.close()
            if resp.status != 200:
                raise RuntimeError(f"Loopback healthcheck status {resp.status}")
        except Exception as ex:
            raise RuntimeError(f"Loopback no accesible en {redirect_uri} ({ex})")

        # 2) Preparar PKCE
        verifier, challenge = _pkce_pair()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        url = AUTH_URL + "?" + urllib.parse.urlencode(params)

        # 3) Reset state and open the browser *when the server is already live*
        _CodeHandler.code = None; _CodeHandler.err = None
        _open_browser(url)

        # 4) Wait for the code with a hard timeout
        deadline = _now() + max(10, AUTH_TIMEOUT_S)
        while _CodeHandler.code is None and _CodeHandler.err is None and _now() < deadline:
            time.sleep(0.05)

        if _CodeHandler.err or _CodeHandler.code is None:
            raise RuntimeError(f"PKCE loopback did not complete (redirect: {redirect_uri}).")

        # 5) Exchange code for tokens (includes secret if present)
        data = {
            "grant_type": "authorization_code",
            "code": _CodeHandler.code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        if CLIENT_SECRET:
            data["client_secret"] = CLIENT_SECRET

        headers = {"User-Agent": USER_AGENT}
        r = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
        r.raise_for_status()
        tok = r.json()
        if not tok.get("refresh_token"):
            # Google may omit refresh_token if consent was not forced;
            # our flow uses prompt=consent, so it usually arrives.
            raise RuntimeError("No se recibió refresh_token. Borra tokens.json y reintenta la autorización.")
        expires = int(tok.get("expires_in", 3600))
        return {
            "access_token": tok.get("access_token", ""),
            "refresh_token": tok.get("refresh_token", ""),
            "scope": tok.get("scope", ""),
            "token_type": tok.get("token_type", "Bearer"),
            "expiry": _now() + expires,
        }

    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        th.join(timeout=2)
        try:
            server.server_close()
        except Exception:
            pass

# ------------------------------------------------------------------
# Token refresh + headers (compatible with the original) + secret usage
# ------------------------------------------------------------------
def _refresh_with_pkce(client_id: str, refresh_token: str) -> Dict[str, Any]:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if CLIENT_SECRET:
        data["client_secret"] = CLIENT_SECRET

    headers = {"User-Agent": USER_AGENT}
    r = requests.post(TOKEN_URL, data=data, headers=headers, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"refresh_token failed: {r.text}")
    tok = r.json()
    if "access_token" not in tok:
        raise RuntimeError("refresh_token response missing access_token")
    expires = int(tok.get("expires_in", 3600))
    return {
        "access_token": tok.get("access_token", ""),
        "refresh_token": refresh_token,  # Google puede omitirlo en refresh
        "scope": tok.get("scope", ""),
        "token_type": tok.get("token_type", "Bearer"),
        "expiry": _now() + expires,
    }

def _ensure_tokens(client_id: Optional[str] = None) -> Dict[str, Any]:
    cid = client_id or CLIENT_ID
    entry = _get_entry()
    access = entry.get("access_token")
    refresh = entry.get("refresh_token")
    expiry  = int(entry.get("expiry") or 0)

    # If we have a valid access token, use it
    if access and _now() < expiry - 30:
        return entry

    # If refresh_token exists, try refresh; if it fails, reauthorize PKCE and overwrite
    if refresh:
        try:
            entry = _refresh_with_pkce(cid, refresh)
            _set_entry(entry)
            return entry
        except Exception:
            entry = _authorize_with_pkce(cid)
            _set_entry(entry)
            return entry

    # Primera vez → iniciar flujo PKCE
    entry = _authorize_with_pkce(cid)
    _set_entry(entry)
    return entry

def _auth_header(client_id: Optional[str] = None) -> Dict[str, str]:
    tok = _ensure_tokens(client_id)
    # Re-validate in case we are near the TTL limit
    if _now() >= int(tok.get("expiry") or 0) - 30:
        tok = _ensure_tokens(client_id)
    return {"Authorization": f"Bearer {tok['access_token']}", "User-Agent": USER_AGENT}

# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------
def fetch_sheet(spreadsheet_id: str, range_a1: str, client_id: Optional[str] = None):
    """
    Devuelve una matriz (2D list[str]) para el rango A1 indicado.
    Puede incluir nombre de hoja: 'MiHoja!A1:Z999' o sólo 'A1:Z999'.
    """
    range_a1 = range_a1 or "A1:Z999"
    params = {"majorDimension": "ROWS", "valueRenderOption": "UNFORMATTED_VALUE"}
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{urllib.parse.quote(range_a1, safe='!:$')}"
    hdr = _auth_header(client_id)
    r = requests.get(url, headers=hdr, params=params, timeout=30)
    if r.status_code == 401:
        # intentar una vez tras refresh/relogin
        _ensure_tokens(client_id)
        hdr = _auth_header(client_id)
        r = requests.get(url, headers=hdr, params=params, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    values = data.get("values", [])
    # normalize to a rectangular matrix (pad with "")
    width = max((len(r) for r in values), default=0)
    return [row + [""]*(width-len(row)) for row in values]

def list_sheet_titles(spreadsheet_id: str, client_id: Optional[str] = None):
    """
    Devuelve la lista de títulos de pestañas (sheets) del spreadsheet.
    """
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}?fields=sheets(properties(title))"
    hdr = _auth_header(client_id)
    r = requests.get(url, headers=hdr, timeout=30)
    if r.status_code == 401:
        _ensure_tokens(client_id)
        hdr = _auth_header(client_id)
        r = requests.get(url, headers=hdr, timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    return [s.get("properties", {}).get("title", "") for s in data.get("sheets", []) if isinstance(s, dict)]

# ------------------------------------------------------------------
# Test manual opcional
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("CLIENT_ID:", CLIENT_ID)
    print("Tokens file:", TOKENS_FILE)
    # To manually test healthcheck:
    # 1) Start the flow: temporarily uncomment the line below:
    # _authorize_with_pkce(CLIENT_ID)
    # 2) Mientras espera, en otra consola:
    #    - PowerShell:  Test-NetConnection 127.0.0.1 -Port 53682  (or the port you use)
    #    - Navegador:   http://127.0.0.1:53682/__ping  → debe devolver 'ok'
    # Then comment that line again.
    # Y/o prueba:
    # print(list_sheet_titles("SPREADSHEET_ID_HERE"))
    # print(fetch_sheet("SPREADSHEET_ID_HERE", "Sheet1!A1:C5"))
