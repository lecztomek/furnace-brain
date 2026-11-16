"""
The temperature in the house for whoever shows or records it (history, main page). The
thermometers module puts every role's fresh reading into the shared state
(runtime["thermometers"]["roles"]); this reads the "w_domu" one while it is fresh.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


def role_temp(runtime: Dict[str, Any], role: str, now: Optional[float] = None) -> Optional[float]:
    dev = (((runtime or {}).get("thermometers") or {}).get("roles") or {}).get(role) or {}
    try:
        temp, valid_until = dev.get("temp"), float(dev.get("valid_until") or 0)
    except (TypeError, ValueError):
        return None
    if temp is None or (time.time() if now is None else now) > valid_until:
        return None
    return float(temp)


def room_temp(runtime: Dict[str, Any], now: Optional[float] = None) -> Optional[float]:
    return role_temp(runtime, "w_domu", now)
