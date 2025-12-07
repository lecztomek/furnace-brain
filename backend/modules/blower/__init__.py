from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.kernel import ModuleInterface, ModuleTickResult
from backend.core.state import (
    BoilerMode,
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
)


# ---------- KONFIGURACJA RUNTIME ----------


@dataclass
class BlowerConfig:
    """
    Konfiguracja modułu dmuchawy.

    base_fan_percent          – nominalna moc dmuchawy [%] przy 100% power
                                (odpowiednik "PWN dmuchawy" w sterownikach)
    min_fan_percent           – minimalna moc PWM [%]
    max_fan_percent           – maksymalna moc PWM [%]

    min_power_to_blow         – poniżej tej mocy kotła [%] dmuchawa wyłączona
                                (np. w podtrzymaniu, gdy power → 0%)

    ignition_fan_boost_percent – dodatkowy boost dmuchawy w IGNITION [% bazowej]
                                 (np. 20 → w ign zwiększamy bazę x1.2)
    ignition_min_fan_percent   – minimalna moc dmuchawy w IGNITION (żeby nie
                                 przydusić płomienia nawet przy małym power)

    flue_control_enabled      – czy używać temperatury spalin w trybie WORK
    flue_opt_temp             – docelowa temperatura spalin [°C]
    flue_kp                   – czułość korekcji dmuchawy na błąd spalin
                                [ (°C różnicy) * flue_kp = % korekcji ]
    flue_correction_max       – maksymalna korekta dmuchawy w górę / dół [%]
    """

    base_fan_percent: float = 45.0  # to ustawiasz jak u Boleckiego "PWN 45"
    min_fan_percent: float = 0.0
    max_fan_percent: float = 100.0

    min_power_to_blow: float = 3.0

    ignition_fan_boost_percent: float = 20.0
    ignition_min_fan_percent: float = 25.0

    flue_control_enabled: bool = True
    flue_opt_temp: float = 150.0
    flue_kp: float = 0.1
    flue_correction_max: float = 20.0


class BlowerModule(ModuleInterface):
    """
    Moduł sterujący dmuchawą na podstawie:
    - power_percent (sygnał mocy z PowerModule),
    - trybu kotła (IGNITION / WORK / OFF / MANUAL),
    - temperatury spalin (w trybie WORK).

    Zasada:

      base_fan = base_fan_percent * (power_percent / 100)

      IGNITION:
        - olewamy temperaturę spalin,
        - dokładamy boost ignition_fan_boost_percent,
        - pilnujemy ignition_min_fan_percent.

      WORK:
        - od base_fan odejmujemy/dodajemy delikatną korektę z flue_gas_temp:
            error = flue_opt_temp - flue_gas_temp
            corr = flue_kp * error  (ograniczony do ±flue_correction_max)
            fan = base_fan + corr
        - jeśli flue_gas_temp brak albo flue_control_disabled → fan = base_fan.

      OFF / MANUAL:
        fan = 0.

    Wynik zapisujemy do Outputs.fan_power (int 0–100).
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
        outputs = Outputs()

        mode_enum = system_state.mode
        power = system_state.outputs.power_percent  # 0–100
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

        # 1) OFF / MANUAL lub bardzo mały power → dmuchawa wyłączona
        if effective_mode == "off" or power <= self._config.min_power_to_blow:
            self._fan_power = 0.0

        else:
            # 2) bazowa moc z power_percent
            base_fan = self._config.base_fan_percent * (power / 100.0)

            fan = base_fan

            if effective_mode == "ignition":
                # IGNITION: olewamy spaliny, lekko podbijamy powietrze,
                # żeby łatwiej rozpalić, plus minimalny %.
                boost_factor = 1.0 + (self._config.ignition_fan_boost_percent / 100.0)
                fan = base_fan * boost_factor
                fan = max(fan, self._config.ignition_min_fan_percent)

            elif effective_mode == "work":
                # WORK: lekka korekta z temperatury spalin
                fan = base_fan
                if self._config.flue_control_enabled and flue_temp is not None:
                    error = self._config.flue_opt_temp - flue_temp
                    corr = self._config.flue_kp * error
                    # nie przesadzamy z korektą
                    max_corr = self._config.flue_correction_max
                    if corr > max_corr:
                        corr = max_corr
                    elif corr < -max_corr:
                        corr = -max_corr
                    fan = fan + corr

            # Ograniczenie do zakresu
            fan = max(self._config.min_fan_percent, min(fan, self._config.max_fan_percent))
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
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="BLOWER_POWER_CHANGED",
                    message=(
                        f"dmuchawa: {prev_fan:.1f}% → {self._fan_power:.1f}% "
                        f"(power={power:.1f}%, Tspalin={flue_temp:.1f}°C)"
                        if flue_temp is not None
                        else f"dmuchawa: {prev_fan:.1f}% → {self._fan_power:.1f}% "
                             f"(power={power:.1f}%, brak Tspalin)"
                    ),
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
        if "min_fan_percent" in values:
            self._config.min_fan_percent = float(values["min_fan_percent"])
        if "max_fan_percent" in values:
            self._config.max_fan_percent = float(values["max_fan_percent"])

        if "min_power_to_blow" in values:
            self._config.min_power_to_blow = float(values["min_power_to_blow"])

        if "ignition_fan_boost_percent" in values:
            self._config.ignition_fan_boost_percent = float(values["ignition_fan_boost_percent"])
        if "ignition_min_fan_percent" in values:
            self._config.ignition_min_fan_percent = float(values["ignition_min_fan_percent"])

        if "flue_control_enabled" in values:
            self._config.flue_control_enabled = bool(values["flue_control_enabled"])
        if "flue_opt_temp" in values:
            self._config.flue_opt_temp = float(values["flue_opt_temp"])
        if "flue_kp" in values:
            self._config.flue_kp = float(values["flue_kp"])
        if "flue_correction_max" in values:
            self._config.flue_correction_max = float(values["flue_correction_max"])

        if persist:
            self._save_config_to_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "base_fan_percent" in data:
            self._config.base_fan_percent = float(data["base_fan_percent"])
        if "min_fan_percent" in data:
            self._config.min_fan_percent = float(data["min_fan_percent"])
        if "max_fan_percent" in data:
            self._config.max_fan_percent = float(data["max_fan_percent"])

        if "min_power_to_blow" in data:
            self._config.min_power_to_blow = float(data["min_power_to_blow"])

        if "ignition_fan_boost_percent" in data:
            self._config.ignition_fan_boost_percent = float(data["ignition_fan_boost_percent"])
        if "ignition_min_fan_percent" in data:
            self._config.ignition_min_fan_percent = float(data["ignition_min_fan_percent"])

        if "flue_control_enabled" in data:
            self._config.flue_control_enabled = bool(data["flue_control_enabled"])
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
