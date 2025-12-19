from __future__ import annotations

from pathlib import Path
from typing import List

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    BoilerMode,
    Event,
    EventLevel,
    ModuleStatus,
    Sensors,
    SystemState,
    PartialOutputs,
)


class ManualModule(ModuleInterface):
    """
    Najwyższy priorytet w trybie MANUAL.

    - MANUAL: wymusza wyjścia wg SystemState.manual (TYLKO te pola).
    - poza MANUAL: nie ingeruje (pusty PartialOutputs).
    """

    def __init__(self, base_path: Path | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent

    @property
    def id(self) -> str:
        return "manual"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        # Poza MANUAL: nic nie ruszamy
        if system_state.mode != BoilerMode.MANUAL:
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

        req = system_state.manual

        # Konflikt mieszacza: nie pozwalamy na dwa kierunki naraz
        mixer_open = bool(req.mixer_open_on)
        mixer_close = bool(req.mixer_close_on)

        if mixer_open and mixer_close:
            mixer_open = False
            mixer_close = False
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.WARNING,
                    type="MANUAL_MIXER_CONFLICT",
                    message="Konflikt: mixer_open_on i mixer_close_on były True jednocześnie; wymuszono oba False.",
                    data={},
                )
            )

        outputs = PartialOutputs(
            fan_power=int(max(0, min(100, req.fan_power))),
            feeder_on=bool(req.feeder_on),
            pump_co_on=bool(req.pump_co_on),
            pump_cwu_on=bool(req.pump_cwu_on),
            mixer_open_on=mixer_open,
            mixer_close_on=mixer_close,
        )

        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

