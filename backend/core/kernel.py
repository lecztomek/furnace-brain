# backend/core/kernel.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from typing_extensions import Protocol
import time
import logging
import traceback

from backend.core.state import (
    Event,
    EventLevel,
    ModuleHealth,
    ModuleStatus,
    Outputs,
    PartialOutputs,
    Sensors,
    SystemState,
)

from backend.core.module_interface import ModuleInterface

from backend.hw.interface import HardwareInterface
from backend.core.state_store import StateStore


logger = logging.getLogger(__name__)

class Kernel:
    def __init__(
        self,
        hardware: HardwareInterface,
        modules: List[ModuleInterface],
		store: StateStore,   
        safety_module: ModuleInterface | None = None,
    ) -> None:
        self._hardware = hardware
        self._modules: Dict[str, ModuleInterface] = {m.id: m for m in modules}
        self._safety_module = safety_module

        self._store = store
        self._last_tick_ts: float = time.time()

        with self._store.locked() as st:
            for mid in self._modules:
                st.modules[mid] = ModuleStatus(id=mid)

            if safety_module is not None and safety_module.id not in self._modules:
                st.modules[safety_module.id] = ModuleStatus(id=safety_module.id)

    @property
    def state(self) -> SystemState:
        return self._store.snapshot()

    def _copy_outputs(self, src: Outputs) -> Outputs:
        # ważne: nie używamy referencji do self._state.outputs
        return Outputs(
            fan_power=src.fan_power,
            feeder_on=src.feeder_on,
            pump_co_on=src.pump_co_on,
            pump_cwu_on=src.pump_cwu_on,
            pump_circ_on=src.pump_circ_on,
            mixer_open_on=src.mixer_open_on,
            mixer_close_on=src.mixer_close_on,
            alarm_buzzer_on=src.alarm_buzzer_on,
            alarm_relay_on=src.alarm_relay_on,
            power_percent=src.power_percent,
        )

    def _merge_outputs(self, base: Outputs, delta: PartialOutputs) -> Outputs:
        """
        Merge:
        - iterujemy po polach PartialOutputs
        - None => nie ruszaj
        - wartość => ustaw (nawet False/0)

        UWAGA: zakładamy, że nazwy pól PartialOutputs są podzbiorem pól Outputs.
        """
        merged = self._copy_outputs(base)

        # tylko pola, które moduł może ustawić
        for field_name in delta.__dataclass_fields__.keys():
            new_value = getattr(delta, field_name)
            if new_value is not None:
                setattr(merged, field_name, new_value)

        return merged

    def _apply_safety(
        self,
        sensors: Sensors,
        preliminary_outputs: Outputs,
        events: List[Event],
    ) -> Tuple[Outputs, List[Event]]:
        # placeholder
        return preliminary_outputs, events
    
    def reload_module_config_from_file(self, module_id: str) -> None:
        now = time.time()
        module = self._modules.get(module_id)
    
        if module is None:
            ev = Event(
                ts=now,
                source="kernel",
                level=EventLevel.WARNING,
                type="CONFIG_RELOAD_UNSUPPORTED",
                message=f"Module '{module_id}' not found in kernel – cannot reload config.",
                data={"module": module_id},
            )
            # 1) trwałe: do ring-bufora
            self._store.publish_events([ev])
            # 2) "od razu widoczne" w /state (do czasu kolejnego ticka)
            with self._store.locked() as st:
                st.recent_events = list(st.recent_events) + [ev]
    
            logger.warning("Config reload requested for unknown module '%s'", module_id)
            return
    
        try:
            module.reload_config_from_file()  # type: ignore[call-arg]
        except Exception as exc:
            logger.exception("Error reloading config for module %s", module_id)
            ev = Event(
                ts=now,
                source="kernel",
                level=EventLevel.ERROR,
                type="CONFIG_RELOAD_ERROR",
                message=f"Error reloading config for module '{module_id}': {exc}",
                data={"module": module_id, "error": str(exc)},
            )
            self._store.publish_events([ev])
            with self._store.locked() as st:
                st.recent_events = list(st.recent_events) + [ev]
        else:
            ev = Event(
                ts=now,
                source="kernel",
                level=EventLevel.INFO,
                type="CONFIG_RELOADED",
                message=f"Config for module '{module_id}' reloaded from file.",
                data={"module": module_id},
            )
            self._store.publish_events([ev])
            with self._store.locked() as st:
                st.recent_events = list(st.recent_events) + [ev]
    

    def step(self) -> None:
        now = time.time()
        self._last_tick_ts = now
    
        sensors = self._hardware.read_sensors()
    
        all_events: List[Event] = []
    
        with self._store.locked() as st:
            st.sensors = sensors
            st.ts = now
    
            combined_outputs: Outputs = self._copy_outputs(st.outputs)
    
            for mid, module in self._modules.items():
                start = time.time()
                status = st.modules.get(mid) or ModuleStatus(id=mid)
    
                try:
                    result = module.tick(now=now, sensors=sensors, system_state=st)
                    duration = time.time() - start
    
                    if not isinstance(result.partial_outputs, PartialOutputs):
                        raise TypeError(...)
    
                    status.health = ModuleHealth.OK
                    status.last_error = None
                    status.last_tick_duration = duration
                    status.last_updated = now
    
                    combined_outputs = self._merge_outputs(combined_outputs, result.partial_outputs)
                    all_events.extend(result.events)
    
                except Exception as exc:
                    duration = time.time() - start
                    status.health = ModuleHealth.ERROR
                    status.last_error = f"{type(exc).__name__}: {exc}"
                    status.last_tick_duration = duration
                    status.last_updated = now
    
                    traceback.print_exc()
                    logger.exception("Module %s raised an exception", mid)
    
                    all_events.append(
                        Event(
                            ts=now,
                            source="kernel",
                            level=EventLevel.ERROR,
                            type="MODULE_ERROR",
                            message=f"Module {mid} raised an exception",
                            data={"module": mid, "error": str(exc)},
                        )
                    )
    
                st.modules[mid] = status
    
            final_outputs, all_events = self._apply_safety(
                sensors=sensors,
                preliminary_outputs=combined_outputs,
                events=all_events,
            )
    
            self._hardware.apply_outputs(final_outputs)
            st.outputs = final_outputs
    
            # dotychczasowe zachowanie: eventy z ostatniego ticka
            st.recent_events = all_events
    
            st.alarm_active = any(e.level == EventLevel.ALARM for e in all_events)
            st.alarm_message = next((e.message for e in all_events if e.level == EventLevel.ALARM), None)
    
        # poza lockiem (albo w środku — store ma RLock, ale tak jest czytelniej)
        self._store.publish_events(all_events)
    
