"""
Trend: how fast the boiler temperature is going up or down. A sample every sample_s,
the slope of the last window_min minutes (least squares - the DS18B20 steps by 0.06 °C)
published as runtime["trend"]["boiler_per_10m"] in °C per 10 min. Nothing else.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import ModuleStatus, PartialOutputs, Sensors, SystemState

DEFAULTS = {"window_min": 10.0, "sample_s": 30.0}
MIN_COVER = 0.6     # less of the window covered by readings (start, a lost sensor): no trend


def slope_per_10m(samples) -> Optional[float]:
    """°C per 10 min from (ts, temp) samples, None with fewer than 3."""
    if len(samples) < 3:
        return None
    n = len(samples)
    mt = sum(t for t, _ in samples) / n
    mv = sum(v for _, v in samples) / n
    den = sum((t - mt) ** 2 for t, _ in samples)
    if den <= 0:
        return None
    return sum((t - mt) * (v - mv) for t, v in samples) / den * 600.0


class TrendModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, data_root: Optional[Path] = None) -> None:
        # data_root: nothing is written, the trend lives in memory
        self._base_path = base_path or Path(__file__).resolve().parent
        self._values: Dict[str, Any] = dict(DEFAULTS)
        self.reload_config_from_file()
        self._samples: Deque[Tuple[float, float]] = deque()
        self._last_sample: Optional[float] = None

    @property
    def id(self) -> str:
        return "trend"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        window = max(2.0, float(self._values.get("window_min", 10.0))) * 60.0
        step = max(2.0, float(self._values.get("sample_s", 30.0)))
        if self._last_sample is not None and now < self._last_sample:
            self._samples.clear()           # the clock went back - start over
            self._last_sample = None
        if self._last_sample is None or now - self._last_sample >= step:
            self._last_sample = now
            if sensors.boiler_temp is not None:
                self._samples.append((now, float(sensors.boiler_temp)))
        while self._samples and self._samples[0][0] < now - window:
            self._samples.popleft()

        per_10m = None
        if self._samples and now - self._samples[0][0] >= window * MIN_COVER \
                and len(self._samples) >= window * MIN_COVER / step:
            per_10m = slope_per_10m(list(self._samples))
        runtime = {
            "boiler_per_10m": None if per_10m is None else round(per_10m, 2),
            "window_min": round(window / 60.0),
        }
        # aux module: published by the runner as runtime["trend"]
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=[], status=status, runtime=runtime)

    def get_config_schema(self) -> Dict[str, Any]:
        with (self._base_path / "schema.yaml").open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return dict(self._values)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        self._values.update(values)
        if persist:
            with (self._base_path / "values.yaml").open("w", encoding="utf-8") as f:
                yaml.safe_dump(self._values, f, allow_unicode=True)

    def reload_config_from_file(self) -> None:
        path = self._base_path / "values.yaml"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                self._values = {**DEFAULTS, **(yaml.safe_load(f) or {})}
