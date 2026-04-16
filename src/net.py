# -*- coding: utf-8 -*-
"""Shared network helpers with retry/backoff and TLS fallback."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

import log as LOG
_l = LOG

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import urllib3
    from urllib3.exceptions import InsecureRequestWarning
except Exception:  # pragma: no cover
    urllib3 = None
    InsecureRequestWarning = None


TRANSIENT_HTTP_STATUS = {429, 500, 502, 503, 504}
LOAD_SHEDDING_HTTP_STATUS = {429, 503}
DEFAULT_HEADERS = {"User-Agent": "PnPInk"}
DEFAULT_HOST_WORKERS = 8
WIKIMEDIA_HOST_WORKERS = 8
HOST_429_DELAY_BASE_S = 1.0
HOST_429_DELAY_MAX_S = 12.0
HOST_LOAD_EVENTS_PER_WORKER_DROP = 4

_HOST_LOCK = threading.RLock()
_HOST_429_DELAY_S: Dict[str, float] = {}
_HOST_429_COUNT: Dict[str, int] = {}
_HOST_SUCCESS_COUNT: Dict[str, int] = {}


class _HostGate:
    def __init__(self, limit: int):
        self.limit = max(1, int(limit or 1))
        self.active = 0
        self.cv = threading.Condition()

    def acquire(self) -> None:
        with self.cv:
            while self.active >= self.limit:
                self.cv.wait()
            self.active += 1

    def release(self) -> None:
        with self.cv:
            if self.active > 0:
                self.active -= 1
            self.cv.notify_all()

    def set_limit(self, limit: int) -> None:
        with self.cv:
            self.limit = max(1, int(limit or 1))
            self.cv.notify_all()


_HOST_GATES: Dict[str, _HostGate] = {}


def _sleep_backoff(attempt: int, base_s: float = 0.6) -> None:
    try:
        time.sleep(base_s * (2 ** max(0, attempt - 1)))
    except Exception:
        pass


def _is_cert_verify_error(ex: Exception) -> bool:
    return "CERTIFICATE_VERIFY_FAILED" in str(ex or "")


def _is_retryable_exception(ex: Exception) -> bool:
    if isinstance(ex, (TimeoutError, socket.timeout)):
        return True
    if isinstance(ex, urllib.error.URLError):
        reason = getattr(ex, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return True
        msg = str(reason or ex)
        return any(tok in msg for tok in ("timed out", "Connection reset", "Temporary failure", "Name or service not known"))
    if requests is not None:
        if isinstance(ex, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
    return False


def _request_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    out = dict(DEFAULT_HEADERS)
    if headers:
        out.update({str(k): str(v) for k, v in headers.items()})
    return out


def _url_host(url: str) -> str:
    try:
        return str(urllib.parse.urlparse(url).netloc or "").strip().lower()
    except Exception:
        return ""


def max_workers_for_url(url: str, *, default: int = DEFAULT_HOST_WORKERS) -> int:
    host = _url_host(url)
    if host.endswith("wikimedia.org"):
        return WIKIMEDIA_HOST_WORKERS
    return max(1, int(default or DEFAULT_HOST_WORKERS))


def _host_gate(url: str) -> _HostGate:
    host = _url_host(url) or "<default>"
    with _HOST_LOCK:
        gate = _HOST_GATES.get(host)
        if gate is None:
            gate = _HostGate(max_workers_for_url(url))
            _HOST_GATES[host] = gate
        return gate


def _host_delay_before_request(url: str) -> None:
    host = _url_host(url)
    if not host:
        return
    with _HOST_LOCK:
        delay_s = float(_HOST_429_DELAY_S.get(host, 0.0) or 0.0)
    if delay_s > 0.0:
        try:
            time.sleep(delay_s)
        except Exception:
            pass


def _host_penalize_load(url: str) -> Tuple[float, int]:
    host = _url_host(url)
    if not host:
        return 0.0, 1
    with _HOST_LOCK:
        prev = float(_HOST_429_DELAY_S.get(host, 0.0) or 0.0)
        new = min(HOST_429_DELAY_MAX_S, prev + HOST_429_DELAY_BASE_S)
        _HOST_429_DELAY_S[host] = new
        strikes = int(_HOST_429_COUNT.get(host, 0) or 0) + 1
        _HOST_429_COUNT[host] = strikes
        _HOST_SUCCESS_COUNT[host] = 0
        gate = _HOST_GATES.get(host)
        limit = gate.limit if gate is not None else max_workers_for_url(f"https://{host}")
        if strikes >= HOST_LOAD_EVENTS_PER_WORKER_DROP and gate is not None and gate.limit > 1:
            gate.set_limit(gate.limit - 1)
            _HOST_429_COUNT[host] = 0
            limit = gate.limit
        return new, limit


def _host_relax_success(url: str) -> None:
    host = _url_host(url)
    if not host:
        return
    with _HOST_LOCK:
        prev = float(_HOST_429_DELAY_S.get(host, 0.0) or 0.0)
        new = max(0.0, prev - 0.5)
        if new > 0.0:
            _HOST_429_DELAY_S[host] = new
        else:
            _HOST_429_DELAY_S.pop(host, None)
        _HOST_429_COUNT[host] = 0
        succ = int(_HOST_SUCCESS_COUNT.get(host, 0) or 0) + 1
        _HOST_SUCCESS_COUNT[host] = succ
        gate = _HOST_GATES.get(host)
        if gate is not None:
            max_limit = max_workers_for_url(f"https://{host}")
            if gate.limit < max_limit and succ >= 6:
                gate.set_limit(gate.limit + 1)
                _HOST_SUCCESS_COUNT[host] = 0


def _close_exc_response(ex: Exception) -> None:
    try:
        fp = getattr(ex, "fp", None)
        if fp is not None:
            fp.close()
    except Exception:
        pass
    try:
        close = getattr(ex, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def fetch_bytes(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    retries: int = 3,
    allow_unverified_tls: bool = True,
    log_prefix: str = "[net]",
) -> Tuple[bytes, Dict[str, str], int]:
    req = urllib.request.Request(url, headers=_request_headers(headers))
    tls_unverified = False
    last_ex: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        gate = _host_gate(url)
        gate.acquire()
        try:
            _host_delay_before_request(url)
            try:
                ctx = ssl._create_unverified_context() if tls_unverified else None
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    status = int(getattr(resp, "status", 200) or 200)
                    if status in TRANSIENT_HTTP_STATUS and attempt < retries:
                        if status in LOAD_SHEDDING_HTTP_STATUS:
                            delay_s, limit = _host_penalize_load(url)
                            _l.w(f"{log_prefix} transient HTTP {status}; retry {attempt}/{retries}; host delay={delay_s:.1f}s workers={limit}")
                        else:
                            _l.w(f"{log_prefix} transient HTTP {status}; retry {attempt}/{retries}")
                        _sleep_backoff(attempt)
                        continue
                    raw = resp.read()
                    hdrs = {str(k): str(v) for k, v in dict(getattr(resp, "headers", {}) or {}).items()}
                    _host_relax_success(url)
                    return raw, hdrs, status
            except urllib.error.HTTPError as ex:
                last_ex = ex
                status = int(getattr(ex, "code", 0) or 0)
                if status in TRANSIENT_HTTP_STATUS and attempt < retries:
                    _close_exc_response(ex)
                    if status in LOAD_SHEDDING_HTTP_STATUS:
                        delay_s, limit = _host_penalize_load(url)
                        _l.w(f"{log_prefix} transient HTTP {status}; retry {attempt}/{retries}; host delay={delay_s:.1f}s workers={limit}")
                    else:
                        _l.w(f"{log_prefix} transient HTTP {status}; retry {attempt}/{retries}")
                    _sleep_backoff(attempt)
                    continue
                if _is_cert_verify_error(ex) and allow_unverified_tls and (not tls_unverified):
                    _close_exc_response(ex)
                    _l.w(f"{log_prefix} SSL verify failed; retrying unverified TLS")
                    tls_unverified = True
                    continue
                _close_exc_response(ex)
                raise
            except Exception as ex:
                last_ex = ex
                _close_exc_response(ex)
                if _is_cert_verify_error(ex) and allow_unverified_tls and (not tls_unverified):
                    _l.w(f"{log_prefix} SSL verify failed; retrying unverified TLS")
                    tls_unverified = True
                    continue
                if _is_retryable_exception(ex) and attempt < retries:
                    _l.w(f"{log_prefix} transient error '{ex}'; retry {attempt}/{retries}")
                    _sleep_backoff(attempt)
                    continue
                raise
        finally:
            try:
                gate.release()
            except Exception:
                pass
    if last_ex is not None:
        raise last_ex
    raise RuntimeError(f"{log_prefix} fetch failed: unknown error")


def fetch_text(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    retries: int = 3,
    allow_unverified_tls: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
    log_prefix: str = "[net]",
) -> Tuple[str, Dict[str, str], int]:
    raw, hdrs, status = fetch_bytes(
        url,
        headers=headers,
        timeout=timeout,
        retries=retries,
        allow_unverified_tls=allow_unverified_tls,
        log_prefix=log_prefix,
    )
    return raw.decode(encoding, errors=errors), hdrs, status


def fetch_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    retries: int = 3,
    allow_unverified_tls: bool = True,
    log_prefix: str = "[net]",
) -> Dict[str, Any]:
    text, _hdrs, _status = fetch_text(
        url,
        headers=headers,
        timeout=timeout,
        retries=retries,
        allow_unverified_tls=allow_unverified_tls,
        log_prefix=log_prefix,
    )
    return json.loads(text)


def requests_get(
    url: str,
    *,
    session=None,
    timeout: int = 15,
    retries: int = 3,
    verify: bool = True,
    allow_unverified_tls: bool = True,
    headers: Optional[Dict[str, str]] = None,
    log_prefix: str = "[net]",
):
    if requests is None:
        raise RuntimeError("requests is not available")
    s = session or requests.Session()
    tls_unverified = (not verify)
    if tls_unverified and urllib3 is not None and InsecureRequestWarning is not None:
        try:
            urllib3.disable_warnings(InsecureRequestWarning)
        except Exception:
            pass
    req_headers = _request_headers(headers)
    last_ex: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        gate = _host_gate(url)
        gate.acquire()
        try:
            _host_delay_before_request(url)
            try:
                r = s.get(url, timeout=timeout, verify=(not tls_unverified), headers=req_headers)
                if (r.status_code in TRANSIENT_HTTP_STATUS) and attempt < retries:
                    try:
                        r.close()
                    except Exception:
                        pass
                    if r.status_code in LOAD_SHEDDING_HTTP_STATUS:
                        delay_s, limit = _host_penalize_load(url)
                        _l.w(f"{log_prefix} transient HTTP {r.status_code}; retry {attempt}/{retries}; host delay={delay_s:.1f}s workers={limit}")
                    else:
                        _l.w(f"{log_prefix} transient HTTP {r.status_code}; retry {attempt}/{retries}")
                    _sleep_backoff(attempt)
                    continue
                _host_relax_success(url)
                return r
            except Exception as ex:
                last_ex = ex
                if _is_cert_verify_error(ex) and allow_unverified_tls and (not tls_unverified):
                    _l.w(f"{log_prefix} SSL verify failed; retrying unverified TLS")
                    if urllib3 is not None and InsecureRequestWarning is not None:
                        try:
                            urllib3.disable_warnings(InsecureRequestWarning)
                        except Exception:
                            pass
                    tls_unverified = True
                    continue
                if _is_retryable_exception(ex) and attempt < retries:
                    _l.w(f"{log_prefix} transient error '{ex}'; retry {attempt}/{retries}")
                    _sleep_backoff(attempt)
                    continue
                raise
        finally:
            try:
                gate.release()
            except Exception:
                pass
    if last_ex is not None:
        raise last_ex
    raise RuntimeError(f"{log_prefix} requests_get failed: unknown error")
