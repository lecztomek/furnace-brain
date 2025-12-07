# backend/core/kernel.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Protocol, Tuple
import time

from backend.core.state import (
    Event,
    EventLevel,
    ModuleHealth,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
)
from backend.hw.interface import HardwareInterface


@dataclass
class ModuleTickResult:
    """
    Wynik pojedynczego wywołania modułu w jednym kroku pętli.
    """
    partial_outputs: Outputs           # tylko to, czym ten moduł REALNIE steruje
    events: List[Event]               # logi / zdarzenia z modułu
    status: ModuleStatus              # zaktualizowany status modułu


class ModuleInterface(Protocol):
    """
    Wspólny interfejs dla wszystkich modułów logiki (dmuchawa, ślimak, pompy, mixer, historia...).
    Kernel zakłada, że każdy moduł implementuje ten kontrakt.
    """

    @property
    def id(self) -> str:
        """
        Unikalny identyfikator modułu, np. "blower", "feeder", "pumps".
        """
        ...

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        """
        Jeden krok logiki modułu.
        - dt: ile sekund minęło od poprzedniego kroku kernela,
        - sensors: snapshot odczytów z hardware,
        - system_state: snapshot globalnego stanu (do odczytu).

        Moduł nie powinien grzebać w system_state; zwraca:
        - partial_outputs – tylko zmiany dotyczące własnych wyjść,
        - events – listę zdarzeń wygenerowanych w tym kroku,
        - status – swój zaktualizowany status (OK/WARNING/ERROR).
        """
        ...

    # W przyszłości możesz dodać tu metody do konfiguracji:
    # get_schema(), get_config(), set_config(...), itd.


class Kernel:
    """
    Główny “mózg” sterownika.
    - trzyma SystemState,
    - odpala w pętli wszystkie moduły,
    - zbiera ich outputs,
    - przepuszcza wszystko przez safety,
    - wysyła finalne wyjścia do warstwy sprzętowej,
    - zbiera eventy dla modułu historii / API.
    """

    def __init__(
        self,
        hardware: HardwareInterface,
        modules: List[ModuleInterface],
        safety_module: ModuleInterface | None = None,
    ) -> None:
        self._hardware = hardware
        self._modules: Dict[str, ModuleInterface] = {m.id: m for m in modules}
        self._safety_module = safety_module

        self._state = SystemState()
        self._last_tick_ts: float = time.time()

        # Zainicjuj ModuleStatus dla wszystkich modułów:
        for mid in self._modules:
            self._state.modules[mid] = ModuleStatus(id=mid)

        if safety_module is not None and safety_module.id not in self._modules:
            # safety też traktujemy jak moduł, jeśli chcemy w statusach
            self._state.modules[safety_module.id] = ModuleStatus(id=safety_module.id)

    @property
    def state(self) -> SystemState:
        """Aktualny snapshot SystemState (np. dla API)."""
        return self._state

    def _merge_outputs(self, base: Outputs, delta: Outputs) -> Outputs:
        """
        Scala outputs z modułów.
        Na razie strategia jest prosta (“ostatni wygrywa”),
        ale później możesz dodać bardziej zaawansowany arbiter.
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

        # Dla prostoty: wartości z delta nadpisują base,
        # ale możesz później zrobić bardziej inteligentną logikę.
        for field_name in merged.__dataclass_fields__.keys():
            new_value = getattr(delta, field_name)
            if new_value != getattr(Outputs(), field_name):  # różne od domyślnego?
                setattr(merged, field_name, new_value)

        return merged

    def _apply_safety(
        self,
        sensors: Sensors,
        preliminary_outputs: Outputs,
        events: List[Event],
    ) -> Tuple[Outputs, List[Event]]:
        """
        Ostatnia linia obrony – tutaj możesz zaimplementować:
        - reakcję na przegrzanie,
        - STB,
        - tryb “fail-safe”.

        Na razie tylko placeholder, który nic nie zmienia.
        Jeśli kiedyś safety będzie osobnym modułem, można go wołać tutaj.
        """
        # TODO: tu dodasz logikę safety.
        # Przykład (pojęciowo):
        # if sensors.boiler_temp is not None and sensors.boiler_temp > 90.0:
        #     preliminary_outputs.fan_power = 0
        #     preliminary_outputs.feeder_on = False
        #     preliminary_outputs.pump_co_on = True
        #     events.append(...)
        return preliminary_outputs, events

    def step(self) -> None:
        """
        Jeden krok pętli sterującej.
        Wywołujesz go cyklicznie (np. z zewnętrznej pętli while True w main.py).
        """
        now = time.time()
        self._last_tick_ts = now

        # 1. Odczyt czujników ze sprzętu:
        sensors = self._hardware.read_sensors()
        self._state.sensors = sensors
        self._state.ts = now

        # 2. Wywołanie modułów:
        all_events: List[Event] = []
        combined_outputs = Outputs()  # zaczynamy od "pustych" wyjść

        for mid, module in self._modules.items():
            start = time.time()
            status = self._state.modules.get(mid) or ModuleStatus(id=mid)

            try:
                result = module.tick(now=now, sensors=sensors, system_state=self._state)
                duration = time.time() - start

                # zaktualizuj status modułu:
                status.health = ModuleHealth.OK
                status.last_error = None
                status.last_tick_duration = duration
                status.last_updated = now

                # scal outputs:
                combined_outputs = self._merge_outputs(combined_outputs, result.partial_outputs)

                # dołącz eventy:
                all_events.extend(result.events)

            except Exception as exc:  # pylint: disable=broad-except
                duration = time.time() - start
                status.health = ModuleHealth.ERROR
                status.last_error = f"{type(exc).__name__}: {exc}"
                status.last_tick_duration = duration
                status.last_updated = now

                # 1) pełny traceback na konsolę
                traceback.print_exc()

                # 2) opcjonalnie przez logging
                logger.exception("Module %s raised an exception", mid)

                # 3) event dla UI / historii
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

            # zapisz status z powrotem do SystemState:
            self._state.modules[mid] = status

        # 3. Safety / arbiter globalny:
        final_outputs, all_events = self._apply_safety(
            sensors=sensors,
            preliminary_outputs=combined_outputs,
            events=all_events,
        )

        # 4. Zastosuj wyjścia na sprzęcie:
        self._hardware.apply_outputs(final_outputs)
        self._state.outputs = final_outputs

        # 5. Zapisz eventy w stanie – moduł historii może je potem odebrać:
        self._state.recent_events = all_events

        # 6. Ustaw globalny alarm_active, jeśli były eventy typu ALARM:
        self._state.alarm_active = any(e.level == EventLevel.ALARM for e in all_events)
        self._state.alarm_message = next(
            (e.message for e in all_events if e.level == EventLevel.ALARM),
            None,
        )
