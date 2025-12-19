from __future__ import annotations

import threading
from collections import deque
from contextlib import contextmanager
from typing import Iterator, List, Tuple

from backend.core.state import Event, SystemState


class StateStore:
    def __init__(self, event_buffer_size: int = 1000) -> None:
        self._lock = threading.RLock()
        self._state = SystemState()

        self._event_seq = 0
        self._event_buf: deque[Tuple[int, Event]] = deque(maxlen=event_buffer_size)

    @contextmanager
    def locked(self) -> Iterator[SystemState]:
        with self._lock:
            yield self._state

    def snapshot(self) -> SystemState:
        import copy
        with self._lock:
            return copy.deepcopy(self._state)

    def publish_events(self, events: List[Event]) -> None:
        if not events:
            return
        with self._lock:
            for ev in events:
                self._event_seq += 1
                if ev.data is None:
                    ev.data = {}
                elif not isinstance(ev.data, dict):
                    ev.data = {"_data": ev.data}
                ev.data["seq"] = self._event_seq
                self._event_buf.append((self._event_seq, ev))

    def events_since(self, last_seq: int) -> tuple[list[Event], int, bool]:
        with self._lock:
            if not self._event_buf:
                return [], last_seq, False
            oldest = self._event_buf[0][0]
            newest = self._event_buf[-1][0]
            overflow = last_seq < (oldest - 1)
            out = [ev for seq, ev in self._event_buf if seq > last_seq]
            return out, newest, overflow
