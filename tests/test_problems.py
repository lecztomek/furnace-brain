"""Problems reported to the logs page: cause included, repeated every 30 min, recovery logged."""
import io
import urllib.error

from backend.core.problems import ProblemTracker, describe_error


def test_http_error_with_server_detail():
    exc = urllib.error.HTTPError("http://x/api/a", 500, "Internal Server Error", {},
                                 io.BytesIO(b'{"detail": "ValueError: bad file"}'))
    assert describe_error(exc, "lokalne API /api/a") == "lokalne API /api/a: HTTP 500 Internal Server Error: ValueError: bad file"


def test_dns_and_timeout_are_readable():
    import socket
    assert "DNS" in describe_error(urllib.error.URLError(socket.gaierror(-2, "Name or service not known")), "Firebase")
    assert "timeout" in describe_error(urllib.error.URLError(socket.timeout("timed out")), "Open-Meteo")


def test_tracker_logs_start_repeat_and_recovery():
    now = [1000.0]
    t = ProblemTracker("remote_view", "Podgląd zdalny", clock=lambda: now[0])
    t.failed("statystyki", "HTTP 500")
    for _ in range(10):
        now[0] += 60
        t.failed("statystyki", "HTTP 500")        # same error: quiet
    assert len(t.take_events()) == 1
    now[0] += 30 * 60
    t.failed("statystyki", "HTTP 500")            # still failing after 30 min: reminder
    ev = t.take_events()
    assert len(ev) == 1 and "nadal nie działa" in ev[0].message and "prób: 12" in ev[0].message
    t.failed("statystyki", "HTTP 404")            # different error: logged right away
    assert len(t.take_events()) == 1
    t.ok("statystyki")
    ev = t.take_events()
    assert len(ev) == 1 and "znowu działa" in ev[0].message
    t.ok("statystyki")                            # nothing to report
    assert t.take_events() == []
