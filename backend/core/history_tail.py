"""
The last hours of the history CSV (rows every 30 s) - modules that keep a
window in memory refill it after a restart instead of starting from zero.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.core.analytics import _float, history_rows


def history_dir(data_root: Optional[Path]) -> Optional[Path]:
    return None if data_root is None else Path(data_root) / "modules" / "history" / "data"


def recent_rows(directory: Optional[Path], hours: float, now: Optional[datetime] = None) -> List[Dict[str, str]]:
    """Rows of the last `hours`, oldest first; [] when there is no history (tests, first start)."""
    if directory is None or not directory.exists():
        return []
    now = now or datetime.now()
    start = now - timedelta(hours=hours)
    rows: List[Dict[str, str]] = []
    d = start.date()
    try:
        while d <= now.date():
            rows.extend(history_rows(directory, d))
            d += timedelta(days=1)
    except Exception:  # pylint: disable=broad-except
        return []      # a damaged file must never stop the controller
    lo, hi = start.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")
    return [r for r in rows if lo <= (r.get("data_czas") or "") <= hi]


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
