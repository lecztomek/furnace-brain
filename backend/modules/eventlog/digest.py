"""
Compact logs: decides which events get written to the log files.

Only the log is affected - control modules emit exactly the same events as
before (they are still in /api/state and the event buffer):

- mixer: decision + START + STOP of one movement -> one entry when the
  movement ends; boiler protection -> first close + one summary at its end,
- blower: no entry per power change -> mode changes + a 15 min summary,
- feeder: no ON/OFF entries -> a 15 min summary; manual feeding (MANUAL mode)
  is logged right away,
- everything else is written unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from backend.core.state import BoilerMode, Event, EventLevel, Outputs

SUMMARY_PERIOD_S = 15 * 60

DROPPED = {"BLOWER_POWER_CHANGED", "FEEDER_ON", "FEEDER_OFF", "MIXER_MOVE", "MIXER_MOVE_START"}


@dataclass
class _Window:
    start: Optional[float] = None
    fan_sum: float = 0.0
    fan_on: int = 0
    samples: int = 0
    feeds: int = 0
    feed_s: float = 0.0


def _fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60} s" if seconds >= 60 else f"{seconds} s"


class LogDigest:
    def __init__(self) -> None:
        self._win = _Window()
        self._feeder_on_ts: Optional[float] = None
        self._move_reason: Optional[Event] = None
        self._protect_active = False
        self._protect_moves = 0
        self._protect_s = 0.0
        self._manual_feed_since: Optional[float] = None

    def process(self, events: List[Event], now: float, mode: BoilerMode, outputs: Outputs) -> List[Event]:
        out: List[Event] = []

        for e in sorted(events, key=lambda ev: ev.ts):
            out.extend(self._event(e))

        out.extend(self._manual_feeding(now, mode, outputs))
        out.extend(self._sample(now, outputs))
        return out

    # ---------- per event ----------

    def _event(self, e: Event) -> List[Event]:
        data = e.data or {}

        if e.type == "FEEDER_ON":
            self._feeder_on_ts = e.ts
            self._win.feeds += 1
        elif e.type == "FEEDER_OFF" and self._feeder_on_ts is not None:
            self._win.feed_s += max(0.0, e.ts - self._feeder_on_ts)
            self._feeder_on_ts = None

        if e.type == "MIXER_MOVE":
            self._move_reason = e
        if e.type == "MIXER_PROTECT_CLOSE":
            first = not self._protect_active
            self._protect_active = True
            return [e] if first else []
        if e.type == "MIXER_MODE_CHANGED" and data.get("prev_mode") == "protect" and self._protect_active:
            summary = self._protect_summary(e.ts)
            return [summary, e]
        if e.type == "MIXER_MOVE_STOP":
            return self._mixer_move(e)

        return [] if e.type in DROPPED else [e]

    def _mixer_move(self, stop: Event) -> List[Event]:
        data = stop.data or {}
        run = float(data.get("actual_run_s") or 0.0)
        if self._protect_active or data.get("mode") == "protect":
            self._protect_moves += 1
            self._protect_s += run
            return []

        reason = (self._move_reason.data or {}) if self._move_reason else {}
        self._move_reason = None
        direction = data.get("direction") or reason.get("direction")
        rad = data.get("radiators_temp", reason.get("radiators_temp"))
        target = data.get("target_temp", reason.get("target_temp"))
        mode = data.get("mode") or reason.get("mode")

        what = {"open": "otwieranie", "close": "zamykanie"}.get(direction, str(direction))
        details = []
        if rad is not None and target is not None:
            details.append(f"CO {float(rad):.1f}°C → zadana {float(target):.1f}°C")
        if mode:
            details.append(f"tryb {mode}")
        message = f"Zawór mieszający: {what} {run:.1f} s" + (f" ({', '.join(details)})" if details else "")

        return [Event(
            ts=stop.ts, source=stop.source, level=EventLevel.INFO, type="MIXER_MOVE",
            message=message,
            data={**reason, **data},
        )]

    def _protect_summary(self, ts: float) -> Event:
        ev = Event(
            ts=ts, source="mixer", level=EventLevel.INFO, type="MIXER_PROTECT_END",
            message=(
                f"Ochrona kotła zakończona: {self._protect_moves} przymknięć zaworu, "
                f"łącznie {_fmt_duration(self._protect_s)}"
            ),
            data={"moves": self._protect_moves, "run_s": round(self._protect_s, 1)},
        )
        self._protect_active = False
        self._protect_moves = 0
        self._protect_s = 0.0
        return ev

    # ---------- manual feeding (MANUAL mode bypasses the feeder module) ----------

    def _manual_feeding(self, now: float, mode: BoilerMode, outputs: Outputs) -> List[Event]:
        feeding = mode == BoilerMode.MANUAL and bool(outputs.feeder_on)
        if feeding and self._manual_feed_since is None:
            self._manual_feed_since = now
            return [Event(ts=now, source="feeder", level=EventLevel.INFO, type="FEEDER_MANUAL_ON",
                          message="Ręczne podawanie: start", data={})]
        if not feeding and self._manual_feed_since is not None:
            took = now - self._manual_feed_since
            self._manual_feed_since = None
            return [Event(ts=now, source="feeder", level=EventLevel.INFO, type="FEEDER_MANUAL_OFF",
                          message=f"Ręczne podawanie: stop (trwało {_fmt_duration(took)})",
                          data={"duration_s": round(took, 1)})]
        return []

    # ---------- 15 min summaries ----------

    def _sample(self, now: float, outputs: Outputs) -> List[Event]:
        w = self._win
        if w.start is None:
            w.start = now
        fan = float(outputs.fan_power or 0)
        w.fan_sum += fan
        w.fan_on += 1 if fan > 0 else 0
        w.samples += 1

        if now - w.start < SUMMARY_PERIOD_S:
            return []

        out: List[Event] = []
        if w.fan_on:
            out.append(Event(
                ts=now, source="blower", level=EventLevel.INFO, type="BLOWER_SUMMARY",
                message=(
                    f"Dmuchawa (15 min): średnio {w.fan_sum / w.samples:.0f}%, "
                    f"pracowała {100 * w.fan_on / w.samples:.0f}% czasu"
                ),
                data={"avg_percent": round(w.fan_sum / w.samples, 1),
                      "on_percent": round(100 * w.fan_on / w.samples, 1)},
            ))
        if w.feeds:
            out.append(Event(
                ts=now, source="feeder", level=EventLevel.INFO, type="FEEDER_SUMMARY",
                message=f"Podajnik (15 min): {w.feeds} podań, łącznie {_fmt_duration(w.feed_s)}",
                data={"feeds": w.feeds, "feed_s": round(w.feed_s, 1)},
            ))
        self._win = _Window(start=now)
        return out
