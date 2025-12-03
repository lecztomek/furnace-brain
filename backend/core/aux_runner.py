# backend/core/aux_runner.py
from __future__ import annotations

import time
from typing import Dict, List

from .kernel import ModuleInterface
from .state import (
    Event,
    EventLevel,
    ModuleHealth,
    ModuleStatus,
    SystemState,
)


class AuxRunner:
    """
    Pętla dla modułów NIEkrytycznych (historia, statystyki, itp.).
    - czyta tylko kernel.state,
    - nie dotyka hardware,
    - błędy aux-modułów NIE zatrzymują sterowania kotłem.
    """

    def __init__(self, kernel, modules: List[ModuleInterface]) -> None:
        from .kernel import Kernel  # lokalny import, żeby uniknąć pętli

        self._kernel: Kernel = kernel
        self._modules: Dict[str, ModuleInterface] = {m.id: m for m in modules}
        self._last_ts: float = time.time()

        # Upewniamy się, że w SystemState są wpisy ModuleStatus dla aux-modułów:
        for mid in self._modules:
            if mid not in self._kernel.state.modules:
                self._kernel.state.modules[mid] = ModuleStatus(id=mid)

    def step(self) -> None:
        """
        Jeden krok pętli pomocniczej.
        Wywołujesz ją w osobnym wątku, co np. 1–5 sekund.
        """
        now = time.time()
        self._last_ts = now

        # Snapshot stanu z kernela:
        state: SystemState = self._kernel.state
        sensors = state.sensors

        # Eventy z modułów krytycznych z ostatniego ticka:
        # (aux-moduły mogą je czytać z state.recent_events, jeśli chcą)
        base_events: List[Event] = list(state.recent_events)

        for mid, module in self._modules.items():
            start = time.time()
            status = state.modules.get(mid) or ModuleStatus(id=mid)

            try:
                # Aux-moduły dostają ten sam kontrakt tick(),
                # ale ich partial_outputs i events są TU ignorowane.
                result = module.tick(now=now, sensors=sensors, system_state=state)
                duration = time.time() - start

                status.health = ModuleHealth.OK
                status.last_error = None
                status.last_tick_duration = duration
                status.last_updated = now

                # Jeśli kiedyś będziesz chciał, możesz np. appendować
                # result.events do osobnego bufora aux-eventów.

            except Exception as exc:  # pylint: disable=broad-except
                duration = time.time() - start
                status.health = ModuleHealth.ERROR
                status.last_error = f"{type(exc).__name__}: {exc}"
                status.last_tick_duration = duration
                status.last_updated = now

                # Wrzuć event o błędzie aux-modułu do recent_events:
                base_events.append(
                    Event(
                        ts=now,
                        source="aux_runner",
                        level=EventLevel.ERROR,
                        type="AUX_MODULE_ERROR",
                        message=f"Aux module {mid} raised an exception",
                        data={"module": mid, "error": str(exc)},
                    )
                )

            state.modules[mid] = status

        # Aktualizujemy recent_events w SystemState – zawierają teraz
        # zarówno eventy z pętli krytycznej, jak i ewentualne błędy aux-modułów:
        state.recent_events = base_events
