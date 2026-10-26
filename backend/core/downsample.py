"""Averaging history rows into time buckets, so long ranges load fast."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List


def downsample(items: List[Dict[str, Any]], max_points: int, time_key: str = "data_czas") -> List[Dict[str, Any]]:
    """
    Splits the time range into max_points equal buckets; per bucket: numeric
    fields averaged, other fields (e.g. tryb_pracy) - the most frequent value.
    Empty buckets are skipped (gaps stay gaps). Rows must be sorted by time.
    """
    if max_points <= 0 or len(items) <= max_points:
        return items
    times = [datetime.fromisoformat(r[time_key]).timestamp() for r in items]
    t0, t1 = times[0], times[-1]
    width = max((t1 - t0) / max_points, 1e-9)

    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for t, row in zip(times, items):
        buckets.setdefault(min(int((t - t0) / width), max_points - 1), []).append(row)

    out: List[Dict[str, Any]] = []
    for idx in sorted(buckets):
        rows = buckets[idx]
        merged: Dict[str, Any] = {time_key: rows[len(rows) // 2][time_key]}
        for key in rows[0]:
            if key == time_key:
                continue
            values = [r.get(key) for r in rows if r.get(key) not in (None, "")]
            if not values:
                merged[key] = ""
                continue
            try:
                merged[key] = round(sum(float(v) for v in values) / len(values), 2)
            except (TypeError, ValueError):
                merged[key] = Counter(values).most_common(1)[0][0]
        out.append(merged)
    return out
