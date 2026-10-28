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
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.history_tail import history_dir, recent_rows, to_mono
from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import BoilerMode, Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

SAMPLE_S = 30.0


@dataclass
class FlameoutConfig:
    enabled: bool = True
    boiler_below_temp: float = 40.0
    power_min_percent: float = 85.0
    window_min: float = 30.0
    rise_max_degC: float = 0.5
    flue_rise_max_degC: float = 1.0


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
        # after a restart the window is refilled from the history, so the
        # protection works at once and not only after window_min
        self._history_dir = history_dir(data_root)
        self._refilled = False

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

        if not self._refilled:
            self._refilled = True
            if mode == BoilerMode.WORK and self._config.enabled:
                self._refill(now_ctrl)

        if mode != BoilerMode.WORK or not self._config.enabled or boiler is None:
            self._samples.clear()
        elif not self._samples or now_ctrl - self._samples[-1][0] >= SAMPLE_S:
            flue = sensors.flue_gas_temp
            self._samples.append((now_ctrl, float(boiler), float(system_state.outputs.power_percent or 0.0),
                                  None if flue is None else float(flue)))
            window = max(self._config.window_min, 1.0) * 60.0
            while self._samples and self._samples[0][0] < now_ctrl - window - SAMPLE_S:
                self._samples.popleft()
            if self._flamed_out(window):
                events.append(self._trip(now, system_state))

        system_state.runtime["flameout"] = dict(self._tripped) if self._tripped else {}
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

    def _refill(self, now_ctrl: float) -> None:
        """The last window of WORK from the history CSV (only the unbroken WORK stretch up to now)."""
        window = max(self._config.window_min, 1.0) * 60.0
        rows = to_mono(recent_rows(self._history_dir, (window + 300) / 3600.0), "temp_pieca", now_ctrl)
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
        self._samples.extend(tail)

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
        for name in ("boiler_below_temp", "power_min_percent", "window_min", "rise_max_degC", "flue_rise_max_degC"):
            if name in values:
                setattr(self._config, name, float(values[name]))
