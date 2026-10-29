"""
Krzywa minimum mocy: how low the PID may take the power in WORK, set by hand like the
radiator curve - the minimum at 0 C outside and how much it grows per degree of frost,
from the outside temperature averaged over avg_h hours. An hour or more well below what
the house needs lets the fuel bed burn thin: the boiler holds on its embers for a while
and then drops by 3-6 C, while the new coal needs ~40 min to catch (28.10: 18:00 and
23:20). The PID (power_work) applies it with its anti-windup and lets go of it when the
boiler is well above the setpoint. No outside temperature - no floor (the fixed minimum
only). Publishes runtime["power_floor"]; nothing else. After a restart the average is
refilled from the history in the background.
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
class PowerFloorConfig:
    enabled: bool = True
    # 28.10 at ~10 C outside the house took ~60 % - the floor well below that
    min_at_0: float = 50.0         # the minimum at 0 C outside [%]
    slope: float = 1.5             # % more per degree of frost (less per degree above 0)
    floor_low: float = 20.0        # never below
    floor_high: float = 70.0       # never above
    avg_h: float = 3.0             # outside temperature averaged over
    release_above_C: float = 3.0   # boiler this much above the setpoint: the floor fades out (the overtemp correction starts at +4)


SAMPLE_S = 60.0                    # the outside temperature sampled once a minute
FLOAT_KEYS = ("min_at_0", "slope", "floor_low", "floor_high", "avg_h", "release_above_C")


class PowerFloorModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, config: Optional[PowerFloorConfig] = None,
                 data_root: Optional[Path] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = config or PowerFloorConfig()
        self._load_config_from_file()
        self._outside: deque = deque()          # (ts_mono, outside), one a minute
        self._history_dir = history_dir(data_root)
        self._refill = Refill()
        self._active = False                     # a floor published (for the ON / OFF events)

    @property
    def id(self) -> str:
        return "power_floor"

    def floor_at(self, outside: float) -> float:
        c = self._config
        f = c.min_at_0 - c.slope * outside
        return round(max(c.floor_low, min(c.floor_high, f)), 1)

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        now_ctrl = system_state.ts_mono
        c = self._config
        if not self._refill.started and self._history_dir is not None:
            hours = max(c.avg_h, 0.25)
            self._refill.start(lambda: to_mono(recent_rows(self._history_dir, hours), "temp_zewn", now_ctrl))
        self._merge(self._refill.take())
        outside = outside_temp(sensors.outside_temp, system_state.runtime, now)
        if outside is not None and (not self._outside or now_ctrl - self._outside[-1][0] >= SAMPLE_S):
            self._outside.append((now_ctrl, float(outside)))
        window = max(c.avg_h, 0.25) * 3600.0
        while self._outside and self._outside[0][0] < now_ctrl - window:
            self._outside.popleft()
        avg = sum(v for _, v in self._outside) / len(self._outside) if self._outside else None

        floor = self.floor_at(avg) if c.enabled and avg is not None else None
        if (floor is not None) != self._active and (c.enabled or self._active):
            events.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO,
                type="POWER_FLOOR_ON" if floor is not None else "POWER_FLOOR_OFF",
                message=(f"Krzywa minimum mocy: {floor:.0f}% (na dworze średnio {avg:.1f}°C)" if floor is not None
                         else "Krzywa minimum mocy: wyłączona albo brak temperatury zewnętrznej - tylko stała minimalna moc"),
                data={"min": floor, "outside_avg": avg},
            ))
        self._active = floor is not None
        system_state.runtime["power_floor"] = {
            "min": floor,                                   # None: the PID uses its fixed minimum only
            "release_above": float(c.release_above_C),
            "outside_avg": None if avg is None else round(avg, 1),
            "avg_h": c.avg_h,
            "enabled": bool(c.enabled),
            "ts": now_ctrl,                                 # the PID ignores a floor that stopped updating
        }
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

    def _merge(self, rows) -> None:
        """Outside temperatures of the last avg_h hours from the history (read in the background), one a minute."""
        if not rows:
            return
        first_live = self._outside[0][0] if self._outside else float("inf")
        old: List[tuple] = []
        for t, _, v in rows:
            if t < first_live - SAMPLE_S / 2 and (not old or t - old[-1][0] >= SAMPLE_S):
                old.append((t, v))
        self._outside = deque(old + list(self._outside))

    # ---------- settings ----------

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
        for name in FLOAT_KEYS:
            if name in values:
                setattr(self._config, name, float(values[name]))
