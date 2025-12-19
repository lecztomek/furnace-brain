from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    BoilerMode,
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
	PartialOutputs
)


# ---------- KONFIGURACJA RUNTIME ----------


@dataclass
class BlowerConfig:
    """
    Konfiguracja modułu dmuchawy w stylu Argo PID Boleckiego.

    Użytkownik ustawia STAŁE obroty dmuchawy (base_fan_percent),
    a moduł z sygnału power [0–100%] wylicza duty (czas PRACA/POSTÓJ)
    w cyklu o długości cycle_time_s.

    base_fan_percent      – stałe obroty dmuchawy, gdy jest WŁĄCZONA [%]

    min_power_to_blow     – poniżej tej mocy dmuchawa całkowicie WYŁĄCZONA

    cycle_time_s          – czas pełnego cyklu dmuchawy [s]
                             (np. 30 s: przy 50% power → 15 s ON, 15 s OFF)

    Korekta z temperatury spalin:

      flue_control_enabled    – czy w ogóle używać korekcji z Tspalin
      flue_ignition_max_temp  – maksymalna Tspalin w IGNITION [°C]
                                 (powyżej tej wartości zmniejszamy duty)
      flue_opt_temp           – docelowa Tspalin w WORK [°C]
      flue_kp                 – czułość korekty duty na błąd Tspalin
                                 [ (°C różnicy) * flue_kp = % punktów duty ]
      flue_correction_max     – maksymalna korekta duty w górę/dół [pkt %]

    """

    base_fan_percent: float = 45.0

    min_power_to_blow: float = 3.0

    cycle_time_s: float = 30.0

    flue_control_enabled: bool = True
    flue_ignition_max_temp: float = 200.0  # max Tspalin w IGNITION
    flue_opt_temp: float = 150.0           # docelowa Tspalin w WORK
    flue_kp: float = 0.1
    flue_correction_max: float = 20.0


class BlowerModule(ModuleInterface):
    """
    Moduł dmuchawy w stylu Argo PID Boleckiego:

    - Użytkownik ustawia STAŁE obroty dmuchawy (base_fan_percent).
    - Moduł z sygnału power [0–100%] liczy duty = czas PRACA / POSTÓJ
      w cyklu długości cycle_time_s.
    - Duty jest bazowo zależne TYLKO od power (duty_base = power/100),
      a korekta z Tspalin delikatnie podbija/obcina duty.

    Mody:

      OFF / MANUAL:
        fan = 0

      IGNITION:
        - duty bazowo = power/100 (bez specjalnego boosta)
        - jeśli flue_control_enabled:
            jeśli Tspalin > flue_ignition_max_temp → zmniejszamy duty

      WORK:
        - duty bazowo = power/100
        - jeśli flue_control_enabled:
            dążymy do flue_opt_temp (korekta ± na duty)

    Wynik zapisujemy do Outputs.fan_power (int 0–100):
      - 0              – dmuchawa OFF (faza przerwy)
      - base_fan_percent – dmuchawa ON (faza pracy)
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[BlowerConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or BlowerConfig()
        self._load_config_from_file()

        self._fan_power: float = 0.0
        self._last_mode: Optional[str] = None

        # stan cyklu duty
        self._cycle_start: Optional[float] = None

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "blower"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()

        mode_enum = system_state.mode
        power = float(system_state.outputs.power_percent)  # 0–100
        flue_temp = sensors.flue_gas_temp

        prev_fan = self._fan_power
        prev_mode = self._last_mode

        # Mapujemy enum na prosty string
        if mode_enum == BoilerMode.IGNITION:
            effective_mode = "ignition"
        elif mode_enum == BoilerMode.WORK:
            effective_mode = "work"
        elif mode_enum in (BoilerMode.OFF, BoilerMode.MANUAL):
            effective_mode = "off"
        else:
            effective_mode = "off"

        # Inicjalizacja początku cyklu przy starcie lub zmianie trybu
        if self._cycle_start is None or prev_mode != effective_mode:
            self._cycle_start = now

        # 1) OFF / MANUAL albo bardzo mały power → dmuchawa wyłączona
        if effective_mode == "off" or power <= self._config.min_power_to_blow:
            self._fan_power = 0.0
            self._cycle_start = now

        else:
            base_fan = self._config.base_fan_percent

            # --- duty bazowe z power (niezależne od trybu) ---
            duty = max(0.0, min(1.0, power / 100.0))  # 0..1

            # --- korekta duty z temperatury spalin ---
            if (
                self._config.flue_control_enabled
                and flue_temp is not None
                and duty > 0.0
            ):
                corr_percent = 0.0  # korekta w punktach procentowych duty

                if effective_mode == "ignition":
                    # w IGNITION pilnujemy MAKSYMALNEJ Tspalin
                    setpoint = self._config.flue_ignition_max_temp
                    error = setpoint - flue_temp  # dodatni: za chłodno, ujemny: za gorąco
                    if error < 0.0:
                        # tylko jak za gorąco – zmniejszamy duty
                        corr_percent = self._config.flue_kp * error  # będzie ujemne
                    else:
                        corr_percent = 0.0

                elif effective_mode == "work":
                    # w WORK dążymy do optymalnej Tspalin (w górę i w dół)
                    setpoint = self._config.flue_opt_temp
                    error = setpoint - flue_temp
                    corr_percent = self._config.flue_kp * error

                # ograniczamy korektę
                max_corr = self._config.flue_correction_max
                if corr_percent > max_corr:
                    corr_percent = max_corr
                elif corr_percent < -max_corr:
                    corr_percent = -max_corr

                duty += corr_percent / 100.0
                duty = max(0.0, min(1.0, duty))

            # --- przeliczamy duty na PRACA/POSTÓJ w cyklu ---
            if duty <= 0.0:
                fan = 0.0
                self._cycle_start = now  # zaczynamy nowy cykl od zera
            elif duty >= 0.999:
                fan = base_fan  # pełna ciągła praca
            else:
                cycle_T = max(1.0, float(self._config.cycle_time_s))  # zabezpieczenie
                if self._cycle_start is None:
                    self._cycle_start = now
                phase = (now - self._cycle_start) % cycle_T

                on_time = duty * cycle_T

                if phase < on_time:
                    # faza PRACY dmuchawy
                    fan = base_fan
                else:
                    # faza PRZERWY dmuchawy
                    fan = 0.0

            # Ograniczenie do zakresu
            self._fan_power = fan

        # Eventy – zmiana trybu dmuchawy
        if prev_mode != effective_mode:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="BLOWER_MODE_CHANGED",
                    message=f"dmuchawa: tryb '{prev_mode}' → '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                )
            )

        # Eventy – duża zmiana mocy
        if abs(self._fan_power - prev_fan) >= 5.0:
            msg = (
                f"dmuchawa: {prev_fan:.1f}% → {self._fan_power:.1f}% "
                f"(power={power:.1f}%, "
            )
            if flue_temp is not None:
                msg += f"Tspalin={flue_temp:.1f}°C)"
            else:
                msg += "brak Tspalin)"

            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="BLOWER_POWER_CHANGED",
                    message=msg,
                    data={
                        "prev_fan": prev_fan,
                        "fan": self._fan_power,
                        "power_percent": power,
                        "flue_gas_temp": flue_temp,
                    },
                )
            )

        self._last_mode = effective_mode

        # Zapisujemy do Outputs.fan_power jako int 0–100
        outputs.fan_power = int(round(self._fan_power))

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "base_fan_percent" in values:
            self._config.base_fan_percent = float(values["base_fan_percent"])

        if "min_power_to_blow" in values:
            self._config.min_power_to_blow = float(values["min_power_to_blow"])

        if "cycle_time_s" in values:
            self._config.cycle_time_s = float(values["cycle_time_s"])

        if "flue_control_enabled" in values:
            self._config.flue_control_enabled = bool(values["flue_control_enabled"])
        if "flue_ignition_max_temp" in values:
            self._config.flue_ignition_max_temp = float(values["flue_ignition_max_temp"])
        if "flue_opt_temp" in values:
            self._config.flue_opt_temp = float(values["flue_opt_temp"])
        if "flue_kp" in values:
            self._config.flue_kp = float(values["flue_kp"])
        if "flue_correction_max" in values:
            self._config.flue_correction_max = float(values["flue_correction_max"])

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

        if "base_fan_percent" in data:
            self._config.base_fan_percent = float(data["base_fan_percent"])

        if "min_power_to_blow" in data:
            self._config.min_power_to_blow = float(data["min_power_to_blow"])

        if "cycle_time_s" in data:
            self._config.cycle_time_s = float(data["cycle_time_s"])

        if "flue_control_enabled" in data:
            self._config.flue_control_enabled = bool(data["flue_control_enabled"])
        if "flue_ignition_max_temp" in data:
            self._config.flue_ignition_max_temp = float(data["flue_ignition_max_temp"])
        if "flue_opt_temp" in data:
            self._config.flue_opt_temp = float(data["flue_opt_temp"])
        if "flue_kp" in data:
            self._config.flue_kp = float(data["flue_kp"])
        if "flue_correction_max" in data:
            self._config.flue_correction_max = float(data["flue_correction_max"])

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
