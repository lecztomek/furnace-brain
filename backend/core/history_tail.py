"""
The last hours of the history CSV (rows every 30 s) - modules that keep a
window in memory refill it after a restart instead of starting from zero.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from backend.core.analytics import _float, _rows


def history_dir(data_root: Optional[Path]) -> Optional[Path]:
    return None if data_root is None else Path(data_root) / "modules" / "history" / "data"


def recent_rows(directory: Optional[Path], hours: float, now: Optional[datetime] = None,
                prefix: str = "boiler") -> List[Dict[str, str]]:
    """
    Rows of the last `hours`, oldest first; [] when there is no history (tests, first start).
    The history is one file per hour (boiler_YYYYMMDD_HH.csv): only the files of the
    wanted hours are read - a dozen small files, not whole days.
    """
    if directory is None or not directory.exists():
        return []
    now = now or datetime.now()
    start = now - timedelta(hours=hours)
    first = start.strftime("%Y%m%d_%H")
    last = now.strftime("%Y%m%d_%H")
    rows: List[Dict[str, str]] = []
    try:
        for name in sorted(os.listdir(directory)):
            if not (name.startswith(prefix + "_") and name.endswith(".csv")):
                continue
            stem = name[len(prefix) + 1:-4]          # YYYYMMDD_HH, or YYYYMMDD for an older daily file
            hourly = len(stem) >= 11 and first <= stem[:11] <= last
            daily = len(stem) == 8 and first[:8] <= stem <= last[:8]
            if hourly or daily:
                rows.extend(_rows(directory / name))
    except Exception:  # pylint: disable=broad-except
        return []      # a damaged file must never stop the controller
    lo, hi = start.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")
    return sorted((r for r in rows if lo <= (r.get("data_czas") or "") <= hi), key=lambda r: r.get("data_czas") or "")


class Refill:
    """Reads the history in a background thread - the control loop never waits for the files."""

    def __init__(self) -> None:
        self.started = False
        self._result: Optional[list] = None

    def start(self, fn: Callable[[], list]) -> None:
        self.started = True

        def run() -> None:
            try:
                self._result = fn()
            except Exception:  # pylint: disable=broad-except
                self._result = []

        threading.Thread(target=run, daemon=True, name="history_refill").start()

    def take(self) -> Optional[list]:
        """The rows once ready (returned once), else None."""
        out, self._result = self._result, None
        return out


def to_mono(rows: List[Dict[str, str]], key: str, now_ctrl: float,
            now_wall: Optional[float] = None) -> List[Tuple[float, Dict[str, str], float]]:
    """(control time, row, value of `key`) for rows that have the value - wall time mapped onto ts_mono."""
    now_wall = time.time() if now_wall is None else now_wall
    out = []
    for r in rows:
        v = _float(r.get(key))
        try:
            ts = datetime.fromisoformat(r.get("data_czas") or "").timestamp()
        except ValueError:
            continue
        if v is not None:
            out.append((now_ctrl - (now_wall - ts), r, v))
    return out
