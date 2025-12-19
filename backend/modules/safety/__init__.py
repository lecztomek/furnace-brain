from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List

import yaml  # pip install pyyaml

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


# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class SafetyConfig:
    enabled: bool = True

    # co ile sekund ponawiać event "wciąż brakuje" (poza eventem na zboczu)
    repeat_warning_s: float = 60.0

    # Reakcja na brak boiler_temp
    boiler_missing_force_fan_off: bool = True
    boiler_missing_force_pumps_on: bool = True


# ---------- MODUŁ ----------

class SafetyModule(ModuleInterface):
    """
    safety:
    Reakcje bezpieczeństwa na brak odczytów (None):
    - boiler_temp None:
        feeder OFF, (opcjonalnie) fan OFF, pompy CO+CWU ON
    - radiators_temp None:
        blokada ruchu mieszacza (mixer_open=False, mixer_close=False)
    - hopper_temp None:
        brak wymuszeń (tylko event)
    - flue_gas_temp None:
        brak wymuszeń (tylko event)

    Ten moduł powinien być uruchamiany na końcu (po modułach sterujących),
    bo może nadpisywać ich decyzje.
    """

    def __init__(self, base_path: Path | None = None, config: SafetyConfig | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or SafetyConfig()
        self._load_config_from_file()

        # pamięć stanu braków, żeby robić eventy na zboczu
        self._missing_prev: Dict[str, bool] = {
            "boiler_temp": False,
            "radiators_temp": False,
            "hopper_temp": False,
            "flue_gas_temp": False,
        }

        # rate-limit per typ
        self._last_repeat_ts: Dict[str, float] = {
            "boiler_temp": 0.0,
            "radiators_temp": 0.0,
            "hopper_temp": 0.0,
            "flue_gas_temp": 0.0,
        }

    @property
    def id(self) -> str:
        return "safety"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        if not bool(self._config.enabled):
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        missing = {
            "boiler_temp": sensors.boiler_temp is None,
            "radiators_temp": sensors.radiators_temp is None,
            "hopper_temp": sensors.hopper_temp is None,
            "flue_gas_temp": sensors.flue_gas_temp is None,
        }

        # --- Eventy na zboczu (pojawił się / zniknął brak) ---
        for key, is_missing in missing.items():
            prev = self._missing_prev.get(key, False)
            if is_missing != prev:
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.WARNING if is_missing else EventLevel.INFO,
                        type=f"SENSOR_{key.upper()}_MISSING_{'ON' if is_missing else 'OFF'}",
                        message=(
                            f"Brak odczytu {key} – aktywne ograniczenia bezpieczeństwa."
                            if is_missing
                            else f"Odczyt {key} wrócił – ograniczenia bezpieczeństwa wyłączone."
                        ),
                        data={"sensor": key, "missing": is_missing},
                    )
                )
                # reset repeat timera przy zmianie
                self._last_repeat_ts[key] = now

        # --- Eventy okresowe (wciąż brakuje) ---
        repeat_s = max(5.0, float(self._config.repeat_warning_s))
        for key, is_missing in missing.items():
            if not is_missing:
                continue
            if now - self._last_repeat_ts.get(key, 0.0) >= repeat_s:
                self._last_repeat_ts[key] = now
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.WARNING,
                        type=f"SENSOR_{key.upper()}_MISSING",
                        message=f"Wciąż brak odczytu {key}.",
                        data={"sensor": key},
                    )
                )

        # --- Wymuszenia bezpieczeństwa zgodnie z zasadami ---

        # 1) boiler_temp None -> feeder OFF, (opcjonalnie) fan OFF, pompki ON
        if missing["boiler_temp"]:
            # nadpisanie MANUAL
            if system_state.mode == BoilerMode.MANUAL:
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.WARNING,
                        type="SAFETY_OVERRIDE_MANUAL",
                        message="Brak boiler_temp – safety nadpisuje sterowanie MANUAL.",
                        data={},
                    )
                )

            outputs.feeder_on = False

            if bool(self._config.boiler_missing_force_fan_off):
                outputs.fan_power = 0

            if bool(self._config.boiler_missing_force_pumps_on):
                outputs.pump_co_on = True
                outputs.pump_cwu_on = True

        # 2) radiators_temp None -> nie kręcimy mieszaczem
        if missing["radiators_temp"]:
            outputs.mixer_open_on = False
            outputs.mixer_close_on = False

        # 3) hopper_temp None -> nic nie wymuszamy (tylko eventy powyżej)

        # 4) flue_gas_temp None -> nic nie wymuszamy (tylko eventy powyżej)

        # zapamiętanie stanu na następny tick
        self._missing_prev = missing

        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self._config.enabled),
            "repeat_warning_s": float(self._config.repeat_warning_s),
            "boiler_missing_force_fan_off": bool(self._config.boiler_missing_force_fan_off),
            "boiler_missing_force_pumps_on": bool(self._config.boiler_missing_force_pumps_on),
        }

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "enabled" in values:
            self._config.enabled = bool(values["enabled"])
        if "repeat_warning_s" in values:
            self._config.repeat_warning_s = float(values["repeat_warning_s"])
        if "boiler_missing_force_fan_off" in values:
            self._config.boiler_missing_force_fan_off = bool(values["boiler_missing_force_fan_off"])
        if "boiler_missing_force_pumps_on" in values:
            self._config.boiler_missing_force_pumps_on = bool(values["boiler_missing_force_pumps_on"])

        if persist:
            self._save_config_to_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "enabled" in data:
            self._config.enabled = bool(data["enabled"])
        if "repeat_warning_s" in data:
            self._config.repeat_warning_s = float(data["repeat_warning_s"])
        if "boiler_missing_force_fan_off" in data:
            self._config.boiler_missing_force_fan_off = bool(data["boiler_missing_force_fan_off"])
        if "boiler_missing_force_pumps_on" in data:
            self._config.boiler_missing_force_pumps_on = bool(data["boiler_missing_force_pumps_on"])

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
