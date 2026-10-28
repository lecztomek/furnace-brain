"""
The outside temperature for everyone who needs it (heating curve, history, diagnostics,
main page). The weather module puts it into the shared state (runtime["weather"]) like
every module publishes its data; this reads it from there - the outside sensor first if
one is ever connected, then the weather while it is fresh (valid_until).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


def outside_temp(sensor: Optional[float], runtime: Dict[str, Any], now: Optional[float] = None) -> Optional[float]:
    if sensor is not None:
        return float(sensor)
    weather = (runtime or {}).get("weather") or {}
    try:
        temp, valid_until = weather.get("temp"), float(weather.get("valid_until") or 0)
    except (TypeError, ValueError):
        return None
    if temp is None or (time.time() if now is None else now) > valid_until:
        return None
    return float(temp)
