import threading
import time

from deckmaker_web import DeckMakerWebHost


def host_without_server() -> DeckMakerWebHost:
    host = object.__new__(DeckMakerWebHost)
    host._session_lock = threading.Lock()
    host._sessions = {}
    host._started_at = time.monotonic()
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
