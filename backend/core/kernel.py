# backend/core/kernel.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Tuple
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
from backend.hw.interface import HardwareInterface

logger = logging.getLogger(__name__)


@dataclass
class ModuleTickResult:
    """
    Wynik pojedynczego wywołania modułu w jednym kroku pętli.
    """
    partial_outputs: PartialOutputs   # None = nie ruszaj, wartość = ustaw (nawet False/0)
    events: List[Event]
    status: ModuleStatus


class ModuleInterface(Protocol):
    """
    Wspólny interfejs dla wszystkich modułów logiki.
    Kernel zakłada, że każdy moduł implementuje ten kontrakt.
    """

    @property
    def id(self) -> str:
        ...

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        ...

    def get_config_schema(self) -> Dict[str, Any]:
        ...

    def get_config_values(self) -> Dict[str, Any]:
        ...

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        ...

    def reload_config_from_file(self) -> None:
        ...


class Kernel:
    """
    Główny “mózg” sterownika.
    - trzyma SystemState,
    - odpala w pętli wszystkie moduły,
    - zbiera ich partial outputs,
    - przepuszcza przez safety,
    - wysyła finalne wyjścia do hardware,
    - zbiera eventy dla historii / API.
    """

    def __init__(
        self,
        hardware: HardwareInterface,
        modules: List[ModuleInterface],
        safety_module: ModuleInterface | None = None,
    ) -> None:
        self._hardware = hardware
        # zachowuje kolejność listy `modules` (dict zachowuje insertion order)
        self._modules: Dict[str, ModuleInterface] = {m.id: m for m in modules}
        self._safety_module = safety_module

        self._state = SystemState()
        self._last_tick_ts: float = time.time()

        for mid in self._modules:
            self._state.modules[mid] = ModuleStatus(id=mid)

        if safety_module is not None and safety_module.id not in self._modules:
            self._state.modules[safety_module.id] = ModuleStatus(id=safety_module.id)

    @property
    def state(self) -> SystemState:
        return self._state

    def _merge_outputs(self, base: Outputs, delta: PartialOutputs) -> Outputs:
        """
        Uniwersalny merge:
        - delta.<pole> == None  -> nie ruszaj pola (zostaje z base)
        - delta.<pole> != None  -> ustaw pole (nawet jeśli False/0)
        """
        merged = Outputs(
            fan_power=base.fan_power,
            feeder_on=base.feeder_on,
            pump_co_on=base.pump_co_on,
            pump_cwu_on=base.pump_cwu_on,
            pump_circ_on=base.pump_circ_on,
            mixer_open_on=base.mixer_open_on,
            mixer_close_on=base.mixer_close_on,
            alarm_buzzer_on=base.alarm_buzzer_on,
            alarm_relay_on=base.alarm_relay_on,
            power_percent=base.power_percent,
        )

        for field_name in merged.__dataclass_fields__.keys():
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
        """
        Ostatnia linia obrony – placeholder (na razie nic nie zmienia).
        """
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
            self._state.recent_events.append(ev)
            logger.warning("Config reload requested for unknown module '%s'", module_id)
            return

        try:
            module.reload_config_from_file()  # type: ignore[call-arg]
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Error reloading config for module %s", module_id)
            ev = Event(
                ts=now,
                source="kernel",
                level=EventLevel.ERROR,
                type="CONFIG_RELOAD_ERROR",
                message=f"Error reloading config for module '{module_id}': {exc}",
                data={"module": module_id, "error": str(exc)},
            )
            self._state.recent_events.append(ev)
        else:
            ev = Event(
                ts=now,
                source="kernel",
                level=EventLevel.INFO,
                type="CONFIG_RELOADED",
                message=f"Config for module '{module_id}' reloaded from file.",
                data={"module": module_id},
            )
            self._state.recent_events.append(ev)

    def step(self) -> None:
        """
        Jeden krok pętli sterującej.
        """
        now = time.time()
        self._last_tick_ts = now

        # 1) Odczyt czujników
        sensors = self._hardware.read_sensors()
        self._state.sensors = sensors
        self._state.ts = now

        # 2) Tick modułów
        all_events: List[Event] = []

        # KLUCZ: startujemy od poprzednich outputs (persist),
        # bo PartialOutputs(None) oznacza "nie ruszaj".
        combined_outputs: Outputs = self._state.outputs

        for mid, module in self._modules.items():
            start = time.time()
            status = self._state.modules.get(mid) or ModuleStatus(id=mid)

            try:
                result = module.tick(now=now, sensors=sensors, system_state=self._state)
                duration = time.time() - start

                status.health = ModuleHealth.OK
                status.last_error = None
                status.last_tick_duration = duration
                status.last_updated = now

                combined_outputs = self._merge_outputs(combined_outputs, result.partial_outputs)
                all_events.extend(result.events)

            except Exception as exc:  # pylint: disable=broad-except
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

            self._state.modules[mid] = status

        # 3) Safety
        final_outputs, all_events = self._apply_safety(
            sensors=sensors,
            preliminary_outputs=combined_outputs,
            events=all_events,
        )

        # 4) Apply + snapshot
        self._hardware.apply_outputs(final_outputs)
        self._state.outputs = final_outputs

        # 5) Eventy
        self._state.recent_events = all_events

        # 6) Alarm
        self._state.alarm_active = any(e.level == EventLevel.ALARM for e in all_events)
        self._state.alarm_message = next(
            (e.message for e in all_events if e.level == EventLevel.ALARM),
            None,
        )
