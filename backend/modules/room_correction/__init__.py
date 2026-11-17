"""
Korekta pokojowa: the house thermometer (thermometers module, role "w domu") moves the heating
curve in small steps. Every few hours it looks at the average temperature in the house and its
trend:
  - too cold and not warming up  -> the curve +step,
  - too warm and not cooling down -> the curve -step,
  - otherwise (fine, or already heading the right way) -> it stays where it is.
The correction is limited (±limit), remembered across restarts, and the average after a
restart is taken from the history - a restart changes nothing.

The heating curve adds the correction to its target (runtime["room_correction"]["offset"]);
switched off, without the module or with a stale runtime it adds nothing - exactly as before.
Without house readings for stale_h hours the correction goes back to 0 step by step.
Off by default.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from backend.core.history_tail import Refill, history_dir, recent_rows
from backend.core.analytics import _float
from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.room import room_temp
from backend.core.state import BoilerMode, Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

SAMPLE_S = 30.0            # one house temperature sample in memory per this many seconds
STALE_H = 12.0             # no house readings this long: the correction goes back to 0
MIN_COVERAGE = 0.7         # a decision needs readings spread over at least this share of the window
MIN_SAMPLES = 20           # ... and at least this many of them


@dataclass
class RoomCorrectionConfig:
    enabled: bool = False             # off by default: the curve heats as before
    room_target: float = 21.0         # the temperature wanted in the house [°C]
    deadband: float = 0.5             # within ± this the house is fine - nothing changes
    interval_h: float = 1.0           # how often it checks [h]
    avg_h: float = 3.0                # the moving average and the trend of the last ... hours [h]
    hold_h: float = 3.0               # after a change at least ... hours before the next one (the house reacts slowly)
    lookahead_h: float = 3.0          # decides on where the house will be in ... hours (average + trend x hours);
                                      # helps a slow place (a hall without a radiator), 0 = on the average itself
    step: float = 0.5                 # one change of the curve [°C]
    limit: float = 2.0                # the correction never beyond ± this [°C]
    trend_min: float = 0.1            # already warming / cooling from this much [°C/h]


def decide(avg: float, trend: float, cfg: RoomCorrectionConfig) -> Tuple[int, str]:
    """(+1 raise / -1 lower / 0 stay, why) - the whole rule."""
    error = cfg.room_target - avg          # > 0: too cold
    if abs(error) <= cfg.deadband:
        return 0, "w domu dobrze"
    if error > 0:
        if trend >= cfg.trend_min:
            return 0, "za zimno, ale już się ogrzewa - czekam"
        return 1, "za zimno i się nie ogrzewa"
    if trend <= -cfg.trend_min:
        return 0, "za ciepło, ale już stygnie - czekam"
    return -1, "za ciepło i nie stygnie"


def window_stats(samples: List[Tuple[float, float]], now: float, hours: float) -> Optional[Tuple[float, float]]:
    """(average of the window, trend °C/h: the last hour against the first hour of the window);
    None when there are too few readings."""
    span = hours * 3600.0
    w = [(t, v) for t, v in samples if now - span <= t <= now]
    # readings at both ends of the window (the trend needs them) and spread over most of it
    if len(w) < MIN_SAMPLES or hours < 2.0 or w[-1][0] - w[0][0] < MIN_COVERAGE * span:
        return None
    avg = sum(v for _, v in w) / len(w)
    first = [v for t, v in w if t <= now - span + 3600.0]
    last = [v for t, v in w if t >= now - 3600.0]
    if not first or not last:
        return None
    trend = (sum(last) / len(last) - sum(first) / len(first)) / (hours - 1.0)
    return avg, trend


class RoomCorrectionModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, data_root: Optional[Path] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = RoomCorrectionConfig()
        self._load_config_from_file()
        self._history = history_dir(data_root)
        state_dir = (Path(data_root) / "modules" / self.id / "data") if data_root else (self._base_path / "data")
        self._state_path = state_dir / "room_correction_state.yaml"

        self._samples: List[Tuple[float, float]] = []      # (wall time, °C), one per SAMPLE_S
        self._refill = Refill()
        self._refilled = False
        self._offset = 0.0
        self._last_decision: Optional[float] = None
        self._last: Dict[str, Any] = {}
        self._last_reading: Optional[float] = None
        self._last_change: Optional[float] = None
        self._last_logged: Optional[str] = None
        self._started = time.time()
        self._load_state()

    @property
    def id(self) -> str:
        return "room_correction"

    # ---------- tick ----------

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        events: List[Event] = []
        cfg = self._config
        if not cfg.enabled:
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=[], status=status, runtime={})

        # after a start: the last hours of the house temperature from the history (in the background)
        if not self._refill.started:
            hours = self._window_h() + 0.5
            self._refill.start(lambda: recent_rows(self._history, hours))
        rows = self._refill.take()
        if rows is not None and not self._refilled:
            self._merge_history(rows)
            self._refilled = True

        temp = room_temp(system_state.runtime, now)
        if temp is not None:
            self._last_reading = now
        if temp is not None and (not self._samples or now - self._samples[-1][0] >= SAMPLE_S):
            self._samples.append((now, temp))
        keep = now - (self._window_h() + 1.0) * 3600.0
        if self._samples and self._samples[0][0] < keep:
            self._samples = [s for s in self._samples if s[0] >= keep]

        due = self._last_decision is None or now - self._last_decision >= cfg.interval_h * 3600.0
        if due and self._refilled:
            ev = self._decide(now, system_state)
            if ev is not None:
                events.append(ev)

        runtime = {
            "offset": self._offset,                 # the curve adds this to its target [°C]
            "ts": now,                              # the curve uses it only while fresh
            "last_decision": self._last_decision,
            "next_decision": None if self._last_decision is None else self._last_decision + cfg.interval_h * 3600.0,
            **self._last,
        }
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    def _decide(self, now: float, system_state: SystemState) -> Optional[Event]:
        cfg = self._config
        stats = window_stats(self._samples, now, self._window_h())
        if stats is None:
            # too few readings: wait; without any for STALE_H hours back to 0, a step at a time
            since = self._last_reading if self._last_reading is not None else self._started
            if self._offset == 0.0 or now - since < STALE_H * 3600.0:
                return None
            if self._last_change is not None and now - self._last_change < max(cfg.hold_h, cfg.interval_h) * 3600.0:
                return None                          # back to 0 at the same pace as the steps up
            before = self._offset
            self._offset = round(self._offset - max(-cfg.step, min(cfg.step, self._offset)), 2)
            self._last_decision = now
            self._last_change = now
            self._last = {"room_avg": None, "trend": None, "decision": "brak odczytów termometru",
                          "change": round(self._offset - before, 2)}
            self._save_state()
            num = lambda v: f"{v:+.1f}".replace(".", ",")
            return Event(ts=time.time(), source=self.id, level=EventLevel.WARNING, type="ROOM_CORRECTION",
                         message=f"Korekta pokojowa: brak odczytów termometru od {STALE_H:g} h - korekta wraca do 0 ({num(before)} → {num(self._offset)})",
                         data={"offset": self._offset, "change": round(self._offset - before, 2), "why": "brak odczytów"})
        avg, trend = stats
        # it only learns while the furnace really heats by the curve
        curve = system_state.runtime.get("curve") or {}
        skip = None
        if system_state.mode != BoilerMode.WORK:
            skip = "piec nie jest w PRACY"
        elif not curve.get("curve_active"):
            skip = "krzywa grzewcza wyłączona"
        elif (system_state.runtime.get("flameout") or {}).get("at"):
            skip = "piec wygasł"
        # where the house will be in lookahead_h hours at this trend - a slow house overshoots
        # if it waits until it is already too warm (or too cold)
        projected = avg + trend * max(0.0, cfg.lookahead_h)
        if skip:
            change, why = 0, skip
        else:
            change, why = decide(projected, trend, cfg)
            if change and self._last_change is not None and now - self._last_change < cfg.hold_h * 3600.0:
                change, why = 0, f"{why}, ale krzywa zmieniona niedawno - czekam na efekt"
        before = self._offset
        if change:
            self._offset = max(-cfg.limit, min(cfg.limit, round(self._offset + change * cfg.step, 2)))
        if self._offset != before:
            self._last_change = now
        self._last_decision = now
        self._last = {"room_avg": round(avg, 2), "trend": round(trend, 3), "projected": round(projected, 2), "decision": why,
                      "change": round(self._offset - before, 2)}
        self._save_state()

        if self._offset == before and why == self._last_logged:
            return None                              # same as the last check: not logged again
        self._last_logged = why
        sign = lambda v: f"{v:+.1f}".replace(".", ",")
        num = lambda v, d=1: f"{v:.{d}f}".replace(".", ",")
        if self._offset != before:
            what = f"krzywa {'w górę' if self._offset > before else 'w dół'} o {num(abs(self._offset - before))}°C (korekta {sign(self._offset)})"
        elif change:
            what = f"korekta już na granicy {sign(self._offset)} - bez zmian"
        else:
            what = f"bez zmian (korekta {sign(self._offset)})"
        return Event(
            ts=time.time(), source=self.id, level=EventLevel.INFO, type="ROOM_CORRECTION",
            message=(f"Korekta pokojowa: w domu {num(avg, 2)}°C (zadana {num(cfg.room_target)}), "
                     f"trend {num(trend, 2)}°C/h"
                     + (f", za {cfg.lookahead_h:g} h ~{num(projected, 1)}°C" if cfg.lookahead_h > 0 else "")
                     + f" - {why}: {what}"),
            data={"room_avg": round(avg, 2), "trend": round(trend, 3), "projected": round(projected, 2), "target": cfg.room_target,
                  "offset": self._offset, "change": round(self._offset - before, 2), "why": why},
        )

    def _window_h(self) -> float:
        return max(2.0, float(self._config.avg_h))

    def _merge_history(self, rows: List[Dict[str, str]]) -> None:
        old = []
        for r in rows:
            v = _float(r.get("temp_wewn"))
            try:
                t = datetime.fromisoformat(r.get("data_czas") or "").timestamp()
            except ValueError:
                continue
            if v is not None:
                old.append((t, v))
        first = self._samples[0][0] if self._samples else float("inf")
        self._samples = [s for s in old if s[0] < first] + self._samples

    # ---------- state (survives restarts) ----------

    def _load_state(self) -> None:
        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self._offset = float(data.get("offset", 0.0))
            ld = data.get("last_decision")
            self._last_decision = float(ld) if ld is not None else None
            lc = data.get("last_change")
            self._last_change = float(lc) if lc is not None else None
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            pass

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                yaml.safe_dump({"offset": self._offset, "last_decision": self._last_decision,
                                "last_change": self._last_change}, f)
            tmp.replace(self._state_path)
        except OSError:
            pass

    # ---------- config ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        self._apply(values)
        if persist:
            with self._config_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(asdict(self._config), f, sort_keys=True, allow_unicode=True)

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            self._apply(yaml.safe_load(f) or {})

    def _apply(self, values: Dict[str, Any]) -> None:
        for f in fields(RoomCorrectionConfig):
            if f.name in values and values[f.name] is not None:
                cast = {bool: bool, float: float, str: str}[type(getattr(self._config, f.name))]
                setattr(self._config, f.name, cast(values[f.name]))
