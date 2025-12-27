from __future__ import annotations

import time
import threading
from typing_extensions import Protocol


class Clock(Protocol):
    def time(self) -> float: ...
    def monotonic(self) -> float: ...


class RealClock:
    def time(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()


class SimClock:
    """
    Zegar symulowany.

    - auto=True: czas symulowany płynie jako dt_real * scale (dt_real liczony wewnątrz).
    - auto=False: czas stoi, dopóki nie zrobisz advance(dt_sim).
    """

    def __init__(self, *, scale: float = 1.0, start_ts: float | None = None, auto: bool = True) -> None:
        if scale <= 0:
            raise ValueError("scale must be > 0")

        self._scale = float(scale)
        self._auto = bool(auto)

        self._ts = time.time() if start_ts is None else float(start_ts)
        self._mono = 0.0

        self._last_real_mono = time.monotonic()
        self._lock = threading.Lock()

    def set_scale(self, scale: float) -> None:
        if scale <= 0:
            raise ValueError("scale must be > 0")
        with self._lock:
            self._sync_locked()
            self._scale = float(scale)

    def set_auto(self, auto: bool) -> None:
        with self._lock:
            self._sync_locked()
            self._auto = bool(auto)

    def advance(self, dt_sim: float) -> None:
        if dt_sim < 0:
            raise ValueError("dt_sim must be >= 0")
        with self._lock:
            # w manualu nie opieramy się o real time
            self._mono += dt_sim
            self._ts += dt_sim

    def _sync_locked(self) -> None:
        if not self._auto:
            return
        now_real = time.monotonic()
        dt_real = now_real - self._last_real_mono
        if dt_real < 0:
            dt_real = 0.0
        self._last_real_mono = now_real

        dt_sim = dt_real * self._scale
        self._mono += dt_sim
        self._ts += dt_sim

    def time(self) -> float:
        with self._lock:
            self._sync_locked()
            return self._ts

    def monotonic(self) -> float:
        with self._lock:
            self._sync_locked()
            return self._mono
