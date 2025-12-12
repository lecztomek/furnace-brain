from __future__ import annotations

from pathlib import Path
from typing import List

from backend.core.kernel import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
    BoilerMode,
	PartialOutputs
)


class ManualModule(ModuleInterface):
    """
    Moduł najwyższego priorytetu dla trybu MANUAL.

    W trybie MANUAL: wystawia pełne Outputs na podstawie SystemState.manual.
    Poza MANUAL: nie ingeruje (nie zmienia wyjść).
    """

    def __init__(self, base_path: Path | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent

    @property
    def id(self) -> str:
        return "manual"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        # Poza MANUAL: nie dotykamy wyjść.
        # (Jeśli kernel "składa" wyniki jako pełne outputs, to przepuszczamy aktualne outputs.)
        if system_state.mode != BoilerMode.MANUAL:
            return ModuleTickResult(partial_outputs=system_state.outputs, events=events, status=status)

        req = system_state.manual

        # Bezpieczeństwo: nie pozwól na dwa kierunki naraz
        mixer_open_on, mixer_close_on = req.normalized_mixer()
        if req.mixer_open_on and req.mixer_close_on:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.WARNING,
                    type="MANUAL_MIXER_CONFLICT",
                    message="Konflikt ręcznego sterowania: mixer_open_on i mixer_close_on były True jednocześnie; wymuszono oba False.",
                    data={},
                )
            )

        outputs = PartialOutputs(
            fan_power=int(max(0, min(100, req.fan_power))),
            feeder_on=bool(req.feeder_on),
            pump_co_on=bool(req.pump_co_on),
            pump_cwu_on=bool(req.pump_cwu_on),
            pump_circ_on=bool(req.pump_circ_on),
            mixer_open_on=bool(mixer_open_on),
            mixer_close_on=bool(mixer_close_on),
            alarm_buzzer_on=bool(req.alarm_buzzer_on),
            alarm_relay_on=bool(req.alarm_relay_on),
            power_percent=float(req.power_percent),
        )

        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)
