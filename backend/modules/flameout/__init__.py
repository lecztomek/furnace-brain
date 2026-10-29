"""
Flame-out: when the fire has gone out in WORK, stop feeding coal into a cold
retort. The PID sees a cold boiler and asks for maximum power, so without
this the feeder keeps pushing coal forever.

Rule (fitted to the 2025/26 season: 6 real flame-outs, no false trip on a
boiler that came back by itself): in WORK for the whole window, the boiler is
below `boiler_below_temp`, the power averaged at least `power_min_percent`
over the window, the boiler didn't rise more than `rise_max_degC` and the
flue gas not more than `flue_rise_max_degC` in it (the controller feeds, but
nothing burns). Without a flue reading the flue condition is skipped.
Then the furnace goes to OFF (feeder and fan stop) with an ALARM. Ignition is
not watched - someone is next to the furnace then.

Early warning (only a warning, nothing is stopped): the power at least
warn_power_percent for warn_after_min, the flue gas below warn_flue_below and still
falling at warn_flue_drop C/min. After a dip with the power at its maximum the fire
comes back - the flue rises within minutes; when it keeps falling, nothing reaches
the grate (coal hung up in the hopper, or none left). Fitted to 26-29.10: all 3 real
flame-outs (29.10 05:11 - two hours before the alarm at 07:13), no false warning.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.history_tail import Refill, history_dir, recent_rows, to_mono
from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import BoilerMode, Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

SAMPLE_S = 30.0
WARN_TREND_S = 300.0      # the flue trend of the early warning: over the last 5 min
WARN_BACK_C = 3.0         # the warning goes when the flue is this much up from its lowest (the fire is back)


@dataclass
class FlameoutConfig:
    enabled: bool = True
    boiler_below_temp: float = 40.0
    power_min_percent: float = 85.0
    window_min: float = 30.0
    rise_max_degC: float = 0.5
    flue_rise_max_degC: float = 1.0
    warn_enabled: bool = True
    warn_power_percent: float = 85.0
    warn_after_min: float = 5.0
    warn_flue_below: float = 55.0
    warn_flue_drop: float = 0.3        # C/min over the last 5 min


class FlameoutModule(ModuleInterface):
    def __init__(self, base_path: Path | None = None, config: FlameoutConfig | None = None,
                 data_root: Path | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = config or FlameoutConfig()
        self._load_config_from_file()

        self._samples: deque = deque()          # (ts_mono, boiler, power) in WORK
        self._tripped: Optional[Dict[str, Any]] = None
        self._warning: Optional[Dict[str, Any]] = None   # the early warning now (once per fading)
        # after a restart the window is refilled from the history, so the
        # protection works at once and not only after window_min
        self._history_dir = history_dir(data_root)
        self._refill_job = Refill()

    @property
    def id(self) -> str:
        return "flameout"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        now_ctrl = system_state.ts_mono
        mode = system_state.mode
        boiler = sensors.boiler_temp

        # the user started the furnace again: the alarm is over
        if self._tripped and mode in (BoilerMode.WORK, BoilerMode.IGNITION):
            self._tripped = None

        if not self._refill_job.started and self._history_dir is not None:
            window = max(self._config.window_min, 1.0) * 60.0
            self._refill_job.start(lambda: to_mono(recent_rows(self._history_dir, (window + 300) / 3600.0),
                                                   "temp_pieca", now_ctrl))
        rows = self._refill_job.take()
        if rows and mode == BoilerMode.WORK and self._config.enabled:
            self._merge(rows, now_ctrl)

        if mode != BoilerMode.WORK or not self._config.enabled or boiler is None:
            self._samples.clear()
            self._warning = None
        elif not self._samples or now_ctrl - self._samples[-1][0] >= SAMPLE_S:
            flue = sensors.flue_gas_temp
            self._samples.append((now_ctrl, float(boiler), float(system_state.outputs.power_percent or 0.0),
                                  None if flue is None else float(flue)))
            window = max(self._config.window_min, 1.0) * 60.0
            while self._samples and self._samples[0][0] < now_ctrl - window - SAMPLE_S:
                self._samples.popleft()
            warn = self._check_warning(now, events)
            if self._flamed_out(window):
                events.append(self._trip(now, system_state))
                self._warning = None
            elif not warn:
                self._warning = None

        out = dict(self._tripped) if self._tripped else {}
        if self._warning:
            out["warning"] = dict(self._warning)
        system_state.runtime["flameout"] = out
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

    def _merge(self, rows, now_ctrl: float) -> None:
        """The last window of WORK from the history (read in the background): only the unbroken WORK stretch."""
        window = max(self._config.window_min, 1.0) * 60.0
        first_live = self._samples[0][0] if self._samples else float("inf")
        rows = [x for x in rows if x[0] < first_live - SAMPLE_S / 2]
        tail: List[tuple] = []
        for t, r, boiler in reversed(rows):
            gap = tail and tail[0][0] - t > 300           # a long gap = it didn't work in between
            if (r.get("tryb_pracy") or "").upper() != "WORK" or gap or t < now_ctrl - window - SAMPLE_S:
                break
            flue = r.get("temp_spalin")
            try:
                power = float(r.get("power") or 0.0)
                flue_v = float(flue) if flue not in (None, "") else None
            except ValueError:
                continue
            tail.insert(0, (t, boiler, power, flue_v))
        self._samples = deque(tail + list(self._samples))

    def _check_warning(self, now: float, events: List[Event]) -> bool:
        """The early warning: True while the fire fades at full power (the event once per fading)."""
        c = self._config
        s = self._samples
        if not c.warn_enabled or len(s) < 3:
            return False
        t_now, _, power_now, flue_now = s[-1]
        if self._warning is not None:
            # held until the fire is back (the flue up WARN_BACK_C from its lowest) or the power is off its maximum
            if flue_now is None or power_now < c.warn_power_percent:
                return False
            self._warning["flue_min"] = min(self._warning["flue_min"], flue_now)
            return flue_now < self._warning["flue_min"] + WARN_BACK_C
        if flue_now is None or flue_now >= c.warn_flue_below:
            return False
        high = [x for x in s if x[0] >= t_now - c.warn_after_min * 60.0]
        if high[0][0] > t_now - c.warn_after_min * 60.0 + SAMPLE_S or any(x[2] < c.warn_power_percent for x in high):
            return False                       # not warn_after_min of full power yet
        before = [x for x in s if x[0] <= t_now - WARN_TREND_S + SAMPLE_S / 2 and x[3] is not None]
        if not before:
            return False
        t_then, flue_then = before[-1][0], before[-1][3]
        rate = (flue_now - flue_then) / max(t_now - t_then, 1.0) * 60.0
        if rate > -c.warn_flue_drop:
            return False
        self._warning = {"at": now, "flue": round(flue_now, 1), "rate": round(rate, 2), "flue_min": flue_now}
        events.append(Event(
            ts=now, source=self.id, level=EventLevel.WARNING, type="FLAMEOUT_WARNING",
            message=(f"Ogień słabnie mimo pełnej mocy: spaliny {flue_now:.0f}°C i spadają "
                     f"{abs(rate):.1f}°C/min - sprawdź węgiel w zasypie (zawieszony albo skończył się)"),
            data={"flue": flue_now, "rate": rate},
        ))
        return True

    def _flamed_out(self, window: float) -> bool:
        s = self._samples
        if len(s) < 3 or s[-1][0] - s[0][0] < window:
            return False                                    # not a whole window of WORK yet
        c = self._config
        boiler_now, boiler_then = s[-1][1], s[0][1]
        avg_power = sum(x[2] for x in s) / len(s)
        flue_now, flue_then = s[-1][3], s[0][3]
        flue_still = flue_now is None or flue_then is None or flue_now - flue_then <= c.flue_rise_max_degC
        return boiler_now < c.boiler_below_temp and avg_power >= c.power_min_percent \
            and boiler_now - boiler_then <= c.rise_max_degC and flue_still

    def _trip(self, now: float, system_state: SystemState) -> Event:
        s = self._samples
        avg_power = sum(x[2] for x in s) / len(s)
        boiler = s[-1][1]
        minutes = round((s[-1][0] - s[0][0]) / 60)
        system_state.mode = BoilerMode.OFF           # feeder and fan stop
        self._samples.clear()
        self._tripped = {"at": now, "boiler_temp": round(boiler, 1), "power": round(avg_power)}
        return Event(
            ts=now, source=self.id, level=EventLevel.ALARM, type="FLAMEOUT",
            message=(f"Piec wygasł: kocioł {boiler:.1f}°C, kocioł i spaliny nie rosną od {minutes} min mimo mocy "
                     f"{avg_power:.0f}% - ślimak i dmuchawa zatrzymane (tryb STOP). Rozpal ponownie."),
            data={"boiler_temp": boiler, "avg_power": avg_power, "minutes": minutes},
        )

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
        if "enabled" in values:
            self._config.enabled = bool(values["enabled"])
        if "warn_enabled" in values:
            self._config.warn_enabled = bool(values["warn_enabled"])
        for name in ("boiler_below_temp", "power_min_percent", "window_min", "rise_max_degC", "flue_rise_max_degC",
                     "warn_power_percent", "warn_after_min", "warn_flue_below", "warn_flue_drop"):
            if name in values:
                setattr(self._config, name, float(values[name]))
