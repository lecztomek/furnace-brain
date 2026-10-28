"""
Krzywa grzewcza: the radiator target. With the curve on, it follows the outside
temperature averaged over curve_avg_h hours (the house and the water react slowly);
with it off - or with no outside temperature for a whole window - it is the fixed
target_temp. Publishes runtime["curve"]; the mixing valve only goes to that target.
After a restart the average is refilled from the history in the background.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.history_tail import Refill, history_dir, recent_rows, to_mono
from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.outside import outside_temp
from backend.core.state import Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState


@dataclass
class CurveConfig:
    target_temp: float = 40.0         # fixed target (curve off, or no outside temperature)
    # Defaults fitted to how the target was set by hand in the 2025/26 season
    # (about 30 C at 0 C outside, 0.45 C more water per degree of frost).
    curve_enabled: bool = False
    curve_at_0: float = 30.0          # target at 0 C outside
    curve_slope: float = 0.45         # C of water per C of outside temperature
    curve_min: float = 22.0
    curve_max: float = 45.0
    curve_avg_h: float = 12.0         # outside temperature averaged over


CURVE_SAMPLE_S = 60.0          # outside temperature sampled once a minute
FLOAT_KEYS = ("target_temp", "curve_at_0", "curve_slope", "curve_min", "curve_max", "curve_avg_h")
# up to 1.45 these settings were the mixing valve's - taken over once (see _take_over_from_mixer)
MIXER_KEYS = ("target_temp", "curve_enabled") + FLOAT_KEYS[1:]
TAKEN_OVER_MARK = ".taken_over_from_mixer"


class CurveModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, config: Optional[CurveConfig] = None,
                 data_root: Optional[Path] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = config or CurveConfig()
        self._curve_changed = False
        self._take_over_from_mixer()
        self._load_config_from_file()

        # outside temperature samples (ts_mono, temp), refilled from the history after a restart
        self._outside: deque = deque()
        self._history_dir = history_dir(data_root)
        self._outside_refill = Refill()
        self._target: float = self._config.target_temp
        self._curve_active: bool = False

    @property
    def id(self) -> str:
        return "curve"

    # ---------- the target ----------

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        now_ctrl = system_state.ts_mono
        outside = outside_temp(sensors.outside_temp, system_state.runtime, now)
        if not self._outside_refill.started and self._history_dir is not None:
            hours = max(self._config.curve_avg_h, 0.25)
            self._outside_refill.start(lambda: to_mono(recent_rows(self._history_dir, hours), "temp_zewn", now_ctrl))
        self._merge_outside(self._outside_refill.take())
        if outside is not None and (not self._outside or now_ctrl - self._outside[-1][0] >= CURVE_SAMPLE_S):
            self._outside.append((now_ctrl, float(outside)))
        avg = self._outside_avg(now_ctrl)
        active = bool(self._config.curve_enabled) and avg is not None
        self._target = self.curve_target(avg) if active else self._config.target_temp

        if active and self._curve_active and self._curve_changed:
            c = self._config
            events.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO, type="CURVE_CHANGED",
                message=(f"Krzywa grzewcza: nowe ustawienia - zadana {self._target:.1f}°C "
                         f"(przy 0°C {c.curve_at_0:g}°C, nachylenie {c.curve_slope:g}, "
                         f"na dworze średnio {avg:.1f}°C)"),
                data={"target": self._target, "outside_avg": avg, "curve_at_0": c.curve_at_0,
                      "curve_slope": c.curve_slope, "curve_min": c.curve_min, "curve_max": c.curve_max},
            ))
        self._curve_changed = False
        if self._config.curve_enabled and active != self._curve_active:
            events.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO if active else EventLevel.WARNING,
                type="CURVE_ON" if active else "CURVE_NO_WEATHER",
                message=(f"Krzywa grzewcza: zadana {self._target:.1f}°C (na dworze średnio {avg:.1f}°C)" if active
                         else f"Krzywa grzewcza: brak temperatury zewnętrznej - stała zadana {self._target:.1f}°C"),
                data={"target": self._target, "outside_avg": avg},
            ))
        self._curve_active = active
        system_state.runtime["curve"] = {
            "target": self._target,
            "curve_enabled": bool(self._config.curve_enabled),
            "curve_active": active,
            "outside_avg": None if avg is None else round(avg, 1),
            "outside_avg_h": self._config.curve_avg_h,
        }
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

    def curve_target(self, outside: float) -> float:
        c = self._config
        t = c.curve_at_0 - c.curve_slope * outside
        return round(max(c.curve_min, min(c.curve_max, t)), 1)

    def _merge_outside(self, rows) -> None:
        """Outside temperatures of the last curve_avg_h hours from the history (read in the background), one a minute."""
        if not rows:
            return
        first_live = self._outside[0][0] if self._outside else float("inf")
        old: List[tuple] = []
        for t, _, v in rows:
            if t < first_live - CURVE_SAMPLE_S / 2 and (not old or t - old[-1][0] >= CURVE_SAMPLE_S):
                old.append((t, v))
        self._outside = deque(old + list(self._outside))

    def _outside_avg(self, now_ctrl: float) -> Optional[float]:
        window = max(self._config.curve_avg_h, 0.25) * 3600.0
        while self._outside and self._outside[0][0] < now_ctrl - window:
            self._outside.popleft()
        # no new readings (internet down, weather service failing): the average of
        # what is left keeps working; only a whole window without data falls back
        # to the fixed target_temp
        if not self._outside:
            return None
        return sum(v for _, v in self._outside) / len(self._outside)

    # ---------- settings ----------

    def _take_over_from_mixer(self) -> None:
        """Once, after the update that split the curve off the mixing valve: the target and
        curve settings saved in the valve's values.yaml become this module's. They stay in
        the valve's file too, so going back to an older version keeps them."""
        mark = self._base_path / TAKEN_OVER_MARK
        mixer_values = self._base_path.parent / "mixer" / "values.yaml"
        if mark.exists() or not mixer_values.exists():
            return
        try:
            old = yaml.safe_load(mixer_values.read_text(encoding="utf-8")) or {}
            own = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {} if self._config_path.exists() else {}
            taken = {k: old[k] for k in MIXER_KEYS if k in old}
            if taken:
                self._config_path.write_text(yaml.safe_dump({**own, **taken}, allow_unicode=True), encoding="utf-8")
            mark.write_text("the target and curve settings were taken over from mixer/values.yaml\n", encoding="utf-8")
        except (OSError, yaml.YAMLError):
            pass    # tried again at the next start

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
        before = self._curve_settings()
        if "curve_enabled" in values:
            self._config.curve_enabled = bool(values["curve_enabled"])
        for name in FLOAT_KEYS:
            if name in values:
                setattr(self._config, name, float(values[name]))
        if before != self._curve_settings():
            self._curve_changed = True   # logged with the new target on the next tick

    def _curve_settings(self) -> tuple:
        c = self._config
        return (c.curve_at_0, c.curve_slope, c.curve_min, c.curve_max, c.curve_avg_h)
