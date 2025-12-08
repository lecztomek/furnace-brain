# backend/modules/pump_cwu/__init__.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List

import yaml  # pip install pyyaml

from backend.core.kernel import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
)


# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class CwuPumpConfig:
    """
    Konfiguracja modułu pompy CWU.

    boiler_on_temp  – od jakiej temperatury kotła (°C) włączamy pompę CWU
    hysteresis      – o ile musi spaść temperatura poniżej progu, żeby pompę wyłączyć
                      (czyli OFF przy T_kotła <= boiler_on_temp - hysteresis)
    """
    boiler_on_temp: float = 60.0
    hysteresis: float = 5.0


# ---------- MODUŁ ----------

class CwuPumpModule(ModuleInterface):
    """
    Moduł sterujący pompą CWU w oparciu o temperaturę kotła.

    Zasada działania:
    - jeśli pompa jest wyłączona i T_kotła >= boiler_on_temp -> włączamy pompę,
    - jeśli pompa jest włączona i T_kotła <= boiler_on_temp - hysteresis -> wyłączamy pompę.

    Stan pompki zwracamy przez Outputs.pump_cwu_on,
    a zmiany stanu raportujemy jako eventy PUMP_CWU_ON / PUMP_CWU_OFF.

    Dodatkowo:
    - trzyma przy sobie schema.yaml (opis pól konfiga),
    - trzyma values.yaml (aktualne wartości),
    - udostępnia metody get_config_schema(), get_config_values(), set_config_values().
    """

    def __init__(self, base_path: Path | None = None, config: CwuPumpConfig | None = None) -> None:
        # Ścieżka do folderu modułu (tam leżą schema.yaml i values.yaml)
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        # Konfiguracja runtime (CwuPumpConfig)
        self._config = config or CwuPumpConfig()
        # Wczytujemy nadpisanie z pliku, jeśli istnieje:
        self._load_config_from_file()

        # Stan wewnętrzny
        self._pump_on: bool = False
        self._last_boiler_temp: float | None = None

    # --- wymagane przez ModuleInterface ---

    @property
    def id(self) -> str:
        return "pump_cwu"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        """
        Jeden krok logiki pompy CWU.
        """
        events: List[Event] = []
        outputs = Outputs()  # domyślnie nic nie zmieniamy

        boiler_temp = sensors.boiler_temp
        prev_pump_on = self._pump_on

        if boiler_temp is None:
            # Brak temperatury kotła -> na razie nie zmieniamy stanu pompy.
            pump_on = prev_pump_on
        else:
            # Prosta histereza ON/OFF
            if not prev_pump_on:
                # Pompa była OFF – sprawdzamy, czy włączyć
                if boiler_temp >= self._config.boiler_on_temp:
                    pump_on = True
                else:
                    pump_on = False
            else:
                # Pompa była ON – sprawdzamy, czy wyłączyć
                off_threshold = self._config.boiler_on_temp - self._config.hysteresis
                if boiler_temp <= off_threshold:
                    pump_on = False
                else:
                    pump_on = True

        # Wykrywanie zmiany stanu i generowanie eventów
        if pump_on != prev_pump_on:
            if pump_on:
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="PUMP_CWU_ON",
                        message=(
                            f"Pompa CWU: ON "
                            f"(T_kotła={boiler_temp:.1f}°C, próg={self._config.boiler_on_temp:.1f}°C)"
                            if boiler_temp is not None
                            else "Pompa CWU: ON"
                        ),
                        data={
                            "boiler_temp": boiler_temp,
                            "boiler_on_temp": self._config.boiler_on_temp,
                            "hysteresis": self._config.hysteresis,
                        },
                    )
                )
            else:
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="PUMP_CWU_OFF",
                        message=(
                            f"Pompa CWU: OFF "
                            f"(T_kotła={boiler_temp:.1f}°C, próg_wyłączenia="
                            f"{self._config.boiler_on_temp - self._config.hysteresis:.1f}°C)"
                            if boiler_temp is not None
                            else "Pompa CWU: OFF"
                        ),
                        data={
                            "boiler_temp": boiler_temp,
                            "boiler_on_temp": self._config.boiler_on_temp,
                            "hysteresis": self._config.hysteresis,
                        },
                    )
                )

        # Zapamiętujemy stan na następny tick
        self._pump_on = pump_on
        self._last_boiler_temp = boiler_temp

        # Ustawiamy tylko pole, za które odpowiadamy – pump_cwu_on
        outputs.pump_cwu_on = pump_on

        # Status modułu – kernel i tak to nadpisze, ale musimy zwrócić instancję
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- OBSŁUGA CONFIGA (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        """
        Zwraca schemat konfiguracji z pliku schema.yaml jako dict.
        GUI może na tym zbudować formularz.
        """
        if not self._schema_path.exists():
            return {}

        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        """
        Zwraca aktualne wartości konfiguracji jako dict (do /api/config/values).
        """
        return {
            "boiler_on_temp": self._config.boiler_on_temp,
            "hysteresis": self._config.hysteresis,
        }

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        """
        Aktualizuje konfigurację modułu na podstawie dict (np. z GUI).
        Opcjonalnie zapisuje do values.yaml.
        """
        if "boiler_on_temp" in values:
            self._config.boiler_on_temp = float(values["boiler_on_temp"])
        if "hysteresis" in values:
            self._config.hysteresis = float(values["hysteresis"])

        if persist:
            self._save_config_to_file()
			
    def reload_config_from_file(self) -> None:
        """
        Publiczne API wymagane przez Kernel.
        """
        self._load_config_from_file()

    # ---------- PLIK values.yaml (ładowanie/zapis) ----------

    def _load_config_from_file(self) -> None:
        """
        Ładuje values.yaml (jeśli istnieje) i nadpisuje domyślne wartości.
        """
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "boiler_on_temp" in data:
            self._config.boiler_on_temp = float(data["boiler_on_temp"])
        if "hysteresis" in data:
            self._config.hysteresis = float(data["hysteresis"])

    def _save_config_to_file(self) -> None:
        """
        Zapisuje aktualną konfigurację do values.yaml.
        (Na razie prosto; później możesz przenieść to do wspólnego systemu configów.)
        """
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
