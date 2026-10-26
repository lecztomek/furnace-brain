"""
Harness for furnace scenario tests: the real Kernel + critical modules from
modules.yaml + the MockHardware simulator, on a manually advanced clock
(an hour of furnace operation runs in a few seconds).

Each simulation gets its own settings directory (schema.yaml + values.yaml.sample,
the rest filled with schema defaults), so local values.yaml files don't affect
the results.
"""
from __future__ import annotations

import inspect
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from backend.config.modules_loader import _load_module_class, load_module_descriptors
from backend.core.clock import SimClock
from backend.core.config_store import ConfigStore
from backend.core.kernel import Kernel
from backend.core.state import BoilerMode, Event, EventLevel, ModuleHealth, Sensors
from backend.core.state_store import StateStore
from backend.hw.mock import MockHardware

MODULES_DIR = Path(__file__).resolve().parents[1] / "backend" / "modules"


@dataclass
class Sample:
    t: float  # seconds since simulation start
    mode: BoilerMode
    boiler_temp: Optional[float]
    flue_gas_temp: Optional[float]
    radiators_temp: Optional[float]
    hopper_temp: Optional[float]
    power: float
    fan_power: int
    feeder_on: bool
    pump_co_on: bool
    pump_cwu_on: bool


class FurnaceSim:
    def __init__(
        self,
        tmp_path: Path,
        values: Optional[Dict[str, Dict[str, Any]]] = None,
        seed: int = 1,
        tick_s: float = 1.0,
        use_samples: bool = True,
    ) -> None:
        self.tick_s = tick_s
        self.clock = SimClock(auto=False, start_ts=1_766_000_000.0)
        self.hw = MockHardware(clock=self.clock, seed=seed)
        self.store = StateStore(event_buffer_size=100_000)
        self.sensor_override: Optional[Callable[[Sensors], Sensors]] = None

        modules_root = tmp_path / "modules"
        data_root = tmp_path / "data"
        descriptors = [d for d in load_module_descriptors() if d.enabled and d.critical]

        for d in descriptors:
            mdir = modules_root / d.id
            mdir.mkdir(parents=True)
            shutil.copy(MODULES_DIR / d.id / "schema.yaml", mdir / "schema.yaml")
            module_values: Dict[str, Any] = {}
            sample = MODULES_DIR / d.id / "values.yaml.sample"
            if use_samples and sample.exists():
                module_values.update(yaml.safe_load(sample.read_text(encoding="utf-8")) or {})
            if values and d.id in values:
                module_values.update(values[d.id])
            if module_values:
                (mdir / "values.yaml").write_text(yaml.safe_dump(module_values), encoding="utf-8")
        ConfigStore(modules_root, [d.id for d in descriptors]).fill_missing_defaults()

        modules = []
        for d in descriptors:
            cls = _load_module_class(d.path)
            kwargs: Dict[str, Any] = {"base_path": modules_root / d.id}
            if "data_root" in inspect.signature(cls.__init__).parameters:
                kwargs["data_root"] = data_root
            modules.append(cls(**kwargs))

        read_sensors = self.hw.read_sensors

        def read_with_override() -> Sensors:
            s = read_sensors()
            return self.sensor_override(s) if self.sensor_override else s

        self.hw.read_sensors = read_with_override  # type: ignore[method-assign]

        self.kernel = Kernel(hardware=self.hw, modules=modules, store=self.store, clock=self.clock)
        self.t = 0.0
        self.samples: List[Sample] = []
        self.events: List[Event] = []
        self.module_errors: List[str] = []
        self._last_seq = 0

    # --- control ---

    def set_mode(self, mode: BoilerMode) -> None:
        with self.store.locked() as st:  # same as POST /api/state/mode
            st.mode = mode

    @property
    def mode(self) -> BoilerMode:
        return self.store.snapshot().mode

    def run(self, seconds: float, sample_every_s: float = 10.0) -> None:
        end = self.t + seconds
        next_sample = self.t
        while self.t < end:
            self.clock.advance(self.tick_s)
            self.t += self.tick_s
            self.kernel.step()
            self._collect_events()
            if self.t >= next_sample:
                self._sample()
                next_sample += sample_every_s

    def run_until(self, cond: Callable[["FurnaceSim"], bool], max_seconds: float) -> bool:
        end = self.t + max_seconds
        while self.t < end:
            self.run(10.0)
            if cond(self):
                return True
        return False

    # --- readout ---

    @property
    def state(self):
        return self.store.snapshot()

    def samples_between(self, t_from: float, t_to: float) -> List[Sample]:
        return [s for s in self.samples if t_from <= s.t <= t_to]

    def events_of(self, type_: str) -> List[Event]:
        return [e for e in self.events if e.type == type_]

    @property
    def alarms(self) -> List[Event]:
        return [e for e in self.events if e.level == EventLevel.ALARM]

    def _collect_events(self) -> None:
        evs, self._last_seq, _ = self.store.events_since(self._last_seq)
        self.events.extend(evs)
        with self.store.locked() as st:
            for mid, status in st.modules.items():
                if status.health == ModuleHealth.ERROR:
                    self.module_errors.append(f"t={self.t:.0f}s {mid}: {status.last_error}")

    def _sample(self) -> None:
        st = self.store.snapshot()
        s, o = st.sensors, st.outputs
        self.samples.append(
            Sample(
                t=self.t,
                mode=st.mode,
                boiler_temp=s.boiler_temp,
                flue_gas_temp=s.flue_gas_temp,
                radiators_temp=s.radiators_temp,
                hopper_temp=s.hopper_temp,
                power=o.power_percent,
                fan_power=o.fan_power,
                feeder_on=o.feeder_on,
                pump_co_on=o.pump_co_on,
                pump_cwu_on=o.pump_cwu_on,
            )
        )
