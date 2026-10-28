"""
How long the background analyses take (statistics, tuning, weather) - shown
on the diagnostics page, so a slow Pi is visible before the GUI suffers.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

_lock = threading.Lock()
_entries: Dict[str, Dict[str, Any]] = {}

# name -> (label shown, how often it runs)
KNOWN = {
    "analytics": ("Statystyki", "co 1 min, gdy ktoś pyta (statystyki, podgląd zdalny)"),
    "tuning": ("Strojenie", "co 10 min, gdy ktoś pyta (Serwis, podgląd zdalny)"),
    "weather": ("Pogoda (temperatura)", "co 15 min"),
    "forecast": ("Prognoza pogody", "co 3 h"),
}


@contextmanager
def measure(name: str, note: Optional[str] = None) -> Iterator[None]:
    """`with measure("tuning"):` - records start, duration and failure of one run."""
    start = time.monotonic()
    with _lock:
        e = _entries.setdefault(name, {"runs": 0, "max_s": 0.0})
        e["running_since"] = time.time()
    ok = False
    try:
        yield
        ok = True
    finally:
        took = time.monotonic() - start
        with _lock:
            e["running_since"] = None
            e["runs"] += 1
            e["last_at"] = time.time()
            e["last_s"] = took
            e["max_s"] = max(e["max_s"], took)
            e["ok"] = ok
            if note is not None:
                e["note"] = note


def snapshot() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return {k: dict(v) for k, v in _entries.items()}


def reset() -> None:
    with _lock:
        _entries.clear()
