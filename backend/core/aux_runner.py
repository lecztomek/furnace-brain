# backend/core/aux_runner.py
from __future__ import annotations

import time
from typing import Dict, List

from backend.core.module_interface import ModuleInterface
from backend.core.state_store import StateStore

from .state import (
    Event,
    EventLevel,
    ModuleHealth,
    ModuleStatus,
    SystemState,
)

import logging
logger = logging.getLogger(__name__)


class AuxRunner:
    """
    Pętla dla modułów NIEkrytycznych (historia, statystyki, itp.).
    - czyta snapshot SystemState ze store,
    - nie dotyka hardware,
    - błędy aux-modułów NIE zatrzymują sterowania kotłem.
    """

    def __init__(self, store: StateStore, modules: List[ModuleInterface]) -> None:
        self._store = store
        self._modules: Dict[str, ModuleInterface] = {m.id: m for m in modules}
        self._last_ts: float = time.time()

        # cursor do inkrementalnego czytania eventów z ring-bufora
        self._last_event_seq: int = 0

        # Upewniamy się, że w SystemState są wpisy ModuleStatus dla aux-modułów:
        with self._store.locked() as st:
            for mid in self._modules:
                if mid not in st.modules:
                    st.modules[mid] = ModuleStatus(id=mid)

    def step(self) -> None:
        """
        Jeden krok pętli pomocniczej.
        Wywołujesz ją w osobnym wątku, co np. 1–5 sekund.
        """
        state: SystemState = self._store.snapshot()
        now = float(state.ts)
        self._last_ts = now

        # Snapshot stanu ze store (bez ryzyka race z kernelem):
        sensors = state.sensors

        # Eventy z kernela inkrementalnie (nie gubimy między tickami):
        new_events, newest_seq, overflow = self._store.events_since(self._last_event_seq)
        self._last_event_seq = newest_seq

        # To jest paczka, którą widzą aux-moduły:
        base_events: List[Event] = list(new_events)

        # (opcjonalnie) jeśli chcesz, żeby aux-moduły widziały też "ostatni tick kernela"
        # bez dublowania, możesz dodać:
        # base_events.extend(list(state.recent_events))

        # aux-moduły dostają events jako recent_events w SNAPSHOT-cie
        state.recent_events = base_events

        # Zbieramy statusy żeby później zapisać je do store
        updated_statuses: Dict[str, ModuleStatus] = {}

        # Zbieramy eventy błędów aux (żeby je opublikować w store)
        aux_error_events: List[Event] = []

        for mid, module in self._modules.items():
            start = time.time()

            # status bierzemy ze snapshotu (fallback), ale zapis będzie do store
            status = state.modules.get(mid) or ModuleStatus(id=mid)

            try:
                # Aux-moduły dostają ten sam kontrakt tick(),
                # ale ich partial_outputs i events są TU ignorowane.
                module.tick(now=now, sensors=sensors, system_state=state)
                duration = time.time() - start

                status.health = ModuleHealth.OK
                status.last_error = None
                status.last_tick_duration = duration
                status.last_updated = now

            except Exception as exc:  # pylint: disable=broad-except
                duration = time.time() - start
                status.health = ModuleHealth.ERROR
                status.last_error = f"{type(exc).__name__}: {exc}"
                status.last_tick_duration = duration
                status.last_updated = now

                aux_error_events.append(
                    Event(
                        ts=now,
                        source="aux_runner",
                        level=EventLevel.ERROR,
                        type="AUX_MODULE_ERROR",
                        message=f"Aux module {mid} raised an exception",
                        data={"module": mid, "error": str(exc)},
                    )
                )

            updated_statuses[mid] = status

        # Zapis statusów do wspólnego stanu (store), pod lockiem:
        with self._store.locked() as st:
            for mid, status in updated_statuses.items():
                st.modules[mid] = status

            # (opcjonalnie) jeśli chcesz, żeby /api/state widziało od razu błędy AUX,
            # dopisz je do recent_events (kernel i tak nadpisze przy kolejnym ticku,
            # ale eventy NIE zginą, bo są też w ring-buforze):
            if aux_error_events:
                st.recent_events = list(st.recent_events) + aux_error_events

        # Publikujemy aux error eventy do ring-bufora (żeby nie ginęły):
        if aux_error_events:
            self._store.publish_events(aux_error_events)
			
    def reload_module_config_from_file(self, module_id: str) -> None:
        now = time.time()
        module = self._modules.get(module_id)

        if module is None:
            ev = Event(
                ts=now,
                source="aux_runner",
                level=EventLevel.WARNING,
                type="CONFIG_RELOAD_UNSUPPORTED",
                message=f"Aux module '{module_id}' not found – cannot reload config.",
                data={"module": module_id},
            )
            # do event bus
            self._store.publish_events([ev])
            # opcjonalnie: pokaż od razu w state.recent_events
            with self._store.locked() as st:
                st.recent_events = list(st.recent_events) + [ev]
            logger.warning("Config reload requested for unknown aux module '%s'", module_id)
            return

        try:
            module.reload_config_from_file()  # type: ignore[call-arg]
        except Exception as exc:
            logger.exception("Error reloading config for aux module %s", module_id)
            ev = Event(
                ts=now,
                source="aux_runner",
                level=EventLevel.ERROR,
                type="CONFIG_RELOAD_ERROR",
                message=f"Error reloading config for aux module '{module_id}': {exc}",
                data={"module": module_id, "error": str(exc)},
            )
            self._store.publish_events([ev])
            with self._store.locked() as st:
                st.recent_events = list(st.recent_events) + [ev]
        else:
            ev = Event(
                ts=now,
                source="aux_runner",
                level=EventLevel.INFO,
                type="CONFIG_RELOADED",
                message=f"Config for aux module '{module_id}' reloaded from file.",
                data={"module": module_id},
            )
            self._store.publish_events([ev])
            with self._store.locked() as st:
                st.recent_events = list(st.recent_events) + [ev]
