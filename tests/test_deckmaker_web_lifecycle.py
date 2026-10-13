import threading
import time
from unittest.mock import Mock

from deckmaker_web import DeckMakerWebHost


def host_without_server() -> DeckMakerWebHost:
    host = object.__new__(DeckMakerWebHost)
    host._session_lock = threading.Lock()
    host._sessions = {}
    host._started_at = time.monotonic()
    host._last_session_check = host._started_at
    host._browser_expected_until = host._started_at
    host._empty_since = None
    host._had_session = False
    return host


def test_hidden_browser_remains_present_while_it_sends_heartbeats():
    host = host_without_server()
    host.browser_opened("browser", visible=False)
    with host._session_lock:
        host._sessions["browser"]["hidden_since"] = time.monotonic() - host.SESSION_TIMEOUT - 1

    host.browser_touched("browser")

    assert host.browser_present()


def test_authenticated_activity_recovers_a_missing_session():
    host = host_without_server()

    host.browser_touched("browser")

    assert host.browser_present()
    assert host._had_session


def test_system_resume_preserves_an_open_browser_session():
    host = host_without_server()
    host.browser_opened("browser", visible=True)
    with host._session_lock:
        host._sessions["browser"]["seen_at"] = time.monotonic() - host.SESSION_TIMEOUT - 1
        host._last_session_check = time.monotonic() - host.SUSPEND_GAP - 1

    assert host.browser_present()
    assert "browser" in host._sessions


def test_stale_browser_session_still_expires_without_a_suspend_gap():
    host = host_without_server()
    host.browser_opened("browser", visible=True)
    with host._session_lock:
        host._sessions["browser"]["seen_at"] = time.monotonic() - host.SESSION_TIMEOUT - 1
        host._last_session_check = time.monotonic()

    assert not host.browser_present()
    assert "browser" not in host._sessions


def test_closing_last_browser_session_closes_preview_immediately():
    host = host_without_server()
    host.service = Mock()
    host.browser_opened("browser")

    host.browser_closed("browser")

    host.service.close_preview.assert_called_once_with()


def test_closing_one_of_two_browser_sessions_keeps_preview_open():
    host = host_without_server()
    host.service = Mock()
    host.browser_opened("first")
    host.browser_opened("second")

    host.browser_closed("first")

    host.service.close_preview.assert_not_called()
