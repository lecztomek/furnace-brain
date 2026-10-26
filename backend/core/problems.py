"""
Problems reported to the logs page so they can be fixed remotely, without
access to the Pi: what failed, the HTTP status and the server's own answer;
logged when it starts or changes, repeated every 30 min while it lasts, and
once more when it is fixed.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
from typing import Any, Callable, Dict, List, Optional

from backend.core.state import Event, EventLevel

REPEAT_S = 30 * 60
BODY_MAX = 300


def describe_error(exc: BaseException, what: str = "") -> str:
    """
    Readable error text, e.g.
    'lokalne API /api/analytics/report: HTTP 500: ValueError: ...'.
    """
    prefix = f"{what}: " if what else ""
    if isinstance(exc, urllib.error.HTTPError):
        body = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace").strip()
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    raw = str(data.get("detail") or data.get("error") or data.get("reason") or raw)
            except ValueError:
                pass
            body = raw[:BODY_MAX]
        except Exception:  # pylint: disable=broad-except
            pass
        return f"{prefix}HTTP {exc.code} {exc.reason}" + (f": {body}" if body else "")
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, socket.timeout) or "timed out" in str(reason):
            return f"{prefix}brak odpowiedzi (timeout)"
        if isinstance(reason, socket.gaierror):
            return f"{prefix}nie można znaleźć serwera (DNS / brak internetu): {reason}"
        if isinstance(reason, ConnectionRefusedError):
            return f"{prefix}połączenie odrzucone (usługa nie działa?)"
        return f"{prefix}błąd połączenia: {reason}"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f"{prefix}brak odpowiedzi (timeout)"
    return f"{prefix}{type(exc).__name__}: {exc}"


class ProblemTracker:
    """Turns repeated failures into a few meaningful log entries."""

    def __init__(self, source: str, label: str, clock: Callable[[], float] = time.time) -> None:
        self.source = source
        self.label = label          # e.g. "Podgląd zdalny"
        self._clock = clock
        # key -> {"msg", "since", "last_log", "count"}
        self._open: Dict[str, Dict[str, Any]] = {}
        self.events: List[Event] = []

    def failed(self, key: str, message: str, type_: str = "ERROR") -> None:
        now = self._clock()
        p = self._open.get(key)
        if p is None or p["msg"] != message:
            self._open[key] = {"msg": message, "since": now, "last_log": now, "count": 1}
            self._log(EventLevel.WARNING, type_, f"{self.label}: {key}: {message}",
                      {"part": key, "error": message})
            return
        p["count"] += 1
        if now - p["last_log"] >= REPEAT_S:
            p["last_log"] = now
            since = time.strftime("%H:%M", time.localtime(p["since"]))
            self._log(EventLevel.WARNING, type_,
                      f"{self.label}: {key}: nadal nie działa (od {since}, prób: {p['count']}): {message}",
                      {"part": key, "error": message, "since": p["since"], "attempts": p["count"]})

    def ok(self, key: str, type_: str = "OK") -> None:
        p = self._open.pop(key, None)
        if p is not None:
            mins = round((self._clock() - p["since"]) / 60)
            self._log(EventLevel.INFO, type_, f"{self.label}: {key}: znowu działa (problem trwał {mins} min)",
                      {"part": key, "was": p["msg"], "minutes": mins})

    def failing(self) -> Dict[str, str]:
        return {k: v["msg"] for k, v in self._open.items()}

    def take_events(self) -> List[Event]:
        events, self.events = self.events, []
        return events

    def _log(self, level: EventLevel, type_: str, message: str, data: Dict[str, Any]) -> None:
        self.events.append(Event(ts=self._clock(), source=self.source, level=level, type=type_,
                                 message=message, data=data))
