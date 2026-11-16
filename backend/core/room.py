"""
The temperature in the house for whoever shows or records it (history, main page). The room
module puts it into the shared state (runtime["room"]); this reads it while it is fresh.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional


def room_temp(runtime: Dict[str, Any], now: Optional[float] = None) -> Optional[float]:
    room = (runtime or {}).get("room") or {}
    try:
        temp, valid_until = room.get("temp"), float(room.get("valid_until") or 0)
    except (TypeError, ValueError):
        return None
    if temp is None or (time.time() if now is None else now) > valid_until:
        return None
    return float(temp)
