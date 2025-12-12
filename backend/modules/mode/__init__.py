from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # pip install pyyaml

from backend.core.kernel import ModuleInterface, ModuleTickResult
from backend.core.state import (
    BoilerMode,
    Event,
    EventLevel,
    ModuleHealth,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
	PartialOutputs
)


# ---------- KONFIGURACJA RUNTIME ----------


@dataclass
class ModeConfig:
    """
    Konfiguracja modułu trybów pracy kotła.

    auto_switch_ignition_to_work  – czy automatycznie przełączać IGNITION -> WORK
    switch_temp                   – temperatura kotła [°C], przy której uznajemy,
                                    że rozpalanie się zakończyło i można przejść
                                    na WORK (typowo = zadana kotła)
    min_ignition_time_s           – minimalny czas IGNITION [s], żeby nie
                                    przełączać się zbyt szybko (np. przy pikach)
    """

    auto_switch_ignition_to_work: bool = True
    switch_temp: float = 65.0
    min_ignition_time_s: float = 300.0  # 5 minut


class ModeModule(ModuleInterface):
    """
    Moduł odpowiedzialny za logikę trybów pracy kotła.

    Założenia:

    - SystemState.mode jest ustawiany głównie przez użytkownika (GUI/API):
        OFF, MANUAL, IGNITION, WORK.
    - Ten moduł tylko:
        * loguje zmiany trybu (dla historii),
        * pilnuje automatycznego przełączenia IGNITION -> WORK,
          gdy spełnione są warunki: czas + temperatura.

    WAŻNE:
    - Kernel pozostaje prosty – nie reaguje na eventy.
    - To TEN moduł bezpośrednio zmienia system_state.mode z IGNITION na WORK,
      jeśli auto_switch_ignition_to_work == True i warunki są spełnione.
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[ModeConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or ModeConfig()
        self._load_config_from_file()

        # Stan wewnętrzny: kiedy weszliśmy w IGNITION
        self._ignition_started_at: Optional[float] = None
        self._last_mode: Optional[BoilerMode] = None

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "mode"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()  # moduł nie steruje sprzętem

        current_mode = system_state.mode
        boiler_temp = sensors.boiler_temp

        # 1) Wykrywanie zmiany trybu (np. użytkownik kliknął w GUI)
        if self._last_mode is None or self._last_mode != current_mode:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="MODE_CHANGED",
                    message=(
                        f"Tryb pracy kotła: "
                        f"{self._mode_to_str(self._last_mode)} -> {self._mode_to_str(current_mode)}"
                    ),
                    data={
                        "prev_mode": self._mode_to_str(self._last_mode),
                        "mode": self._mode_to_str(current_mode),
                    },
                )
            )

            # Zarządzanie timestampem IGNITION
            if current_mode == BoilerMode.IGNITION:
                self._ignition_started_at = now
            else:
                self._ignition_started_at = None

            self._last_mode = current_mode

        # 2) Logika automatycznego przejścia IGNITION -> WORK
        if (
            self._config.auto_switch_ignition_to_work
            and system_state.mode == BoilerMode.IGNITION
            and self._ignition_started_at is not None
            and boiler_temp is not None
        ):
            ignition_duration = now - self._ignition_started_at

            if (
                ignition_duration >= self._config.min_ignition_time_s
                and boiler_temp >= self._config.switch_temp
            ):
                # Warunki spełnione – BEZPOŚREDNIO zmieniamy SystemState.mode
                prev_mode = system_state.mode
                system_state.mode = BoilerMode.WORK
                self._ignition_started_at = None
                self._last_mode = system_state.mode

                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="MODE_AUTO_SWITCH",
                        message=(
                            f"Automatyczna zmiana trybu {self._mode_to_str(prev_mode)} -> WORK "
                            f"(T_kotła={boiler_temp:.1f}°C, próg={self._config.switch_temp:.1f}°C, "
                            f"czas_ignition={ignition_duration:.0f}s)"
                        ),
                        data={
                            "from": self._mode_to_str(prev_mode),
                            "to": "WORK",
                            "boiler_temp": boiler_temp,
                            "switch_temp": self._config.switch_temp,
                            "ignition_duration_s": ignition_duration,
                        },
                    )
                )

        # Status modułu
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id, health=ModuleHealth.OK)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- POMOCNICZE ----------

    @staticmethod
    def _mode_to_str(mode: Optional[BoilerMode]) -> str:
        if mode is None:
            return "UNKNOWN"
        if mode == BoilerMode.OFF:
            return "OFF"
        if mode == BoilerMode.MANUAL:
            return "MANUAL"
        if mode == BoilerMode.IGNITION:
            return "IGNITION"
        if mode == BoilerMode.WORK:
            return "WORK"
        return "UNKNOWN"

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "auto_switch_ignition_to_work" in values:
            self._config.auto_switch_ignition_to_work = bool(values["auto_switch_ignition_to_work"])
        if "switch_temp" in values:
            self._config.switch_temp = float(values["switch_temp"])
        if "min_ignition_time_s" in values:
            self._config.min_ignition_time_s = float(values["min_ignition_time_s"])

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        """
        Publiczne API wymagane przez Kernel.
        """
        self._load_config_from_file()
			
    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "auto_switch_ignition_to_work" in data:
            self._config.auto_switch_ignition_to_work = bool(data["auto_switch_ignition_to_work"])
        if "switch_temp" in data:
            self._config.switch_temp = float(data["switch_temp"])
        if "min_ignition_time_s" in data:
            self._config.min_ignition_time_s = float(data["min_ignition_time_s"])

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
