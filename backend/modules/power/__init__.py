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
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
)


# ---------- KONFIGURACJA RUNTIME ----------


@dataclass
class PowerConfig:
    """
    Konfiguracja modułu regulatora mocy kotła.

    boiler_set_temp               – zadana temperatura kotła [°C]
    kp, ki, kd                     – parametry PID (błąd w °C -> wynik w % mocy)
    min_power, max_power           – ograniczenia mocy [%]

    ignition_power_bonus_percent   – maksymalny bonus mocy w trybie ignition [%]
                                     (np. 30 -> przy dużym odchyleniu
                                     power = base_power * 1.3)
    ignition_bonus_margin_degC     – o ile °C poniżej zadanej zaczynamy
                                     wygaszanie bonusa. Poniżej (T_set - margin)
                                     bonus = pełny, przy T_set bonus = 0.

    mode                           – lokalny tryb, używany TYLKO gdy z jakiegoś
                                     powodu SystemState.mode jest nieznany.
                                     "auto", "ignition", "off"
    """

    boiler_set_temp: float = 65.0

    kp: float = 2.0
    ki: float = 0.01
    kd: float = 0.0

    min_power: float = 0.0
    max_power: float = 100.0

    ignition_power_bonus_percent: float = 30.0
    ignition_bonus_margin_degC: float = 10.0

    mode: str = "auto"  # fallback: "auto"/"ignition"/"off"


class PowerModule(ModuleInterface):
    """
    Moduł wyliczający "power" (moc kotła) w % na podstawie temperatury kotła.

    Tryby (na podstawie SystemState.mode = BoilerMode):

    - OFF:
        power = 0%, PID wyzerowany.

    - MANUAL:
        power = 0% – PowerModule nie steruje mocą, w tym trybie zakładasz
        ręczne sterowanie innymi modułami (feeder/blower itp.).

    - WORK:
        base_power = PID(T_set, T_boiler)
        power = base_power (w zakresie [min_power, max_power])

    - IGNITION:
        też liczymy base_power = PID(...),
        ale wprowadzamy dodatkowy mnożnik zależny od tego,
        jak bardzo T_boiler jest poniżej zadanej:

            jeśli T_boiler <= T_set - margin:
                k = 1.0   (pełny bonus)
            jeśli T_boiler >= T_set:
                k = 0.0   (brak bonusa)
            inaczej:
                k = (T_set - T_boiler) / margin   (wygładzone 1 → 0)

            factor = 1 + k * (bonus_percent / 100)
            power = base_power * factor

        W praktyce:
        - przy zimnym kotle w IGNITION moc jest "dopompowana",
        - w miarę dojazdu do zadanej bonus się wygasza,
        - po automatycznym przełączeniu ModeModule na WORK jedziemy
          już na czystym PID-zie.
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[PowerConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or PowerConfig()
        self._load_config_from_file()

        # Stan PID-a
        self._integral: float = 0.0
        self._last_error: Optional[float] = None
        self._last_tick_ts: Optional[float] = None

        # Stan mocy
        self._power: float = 0.0
        self._last_effective_mode: Optional[str] = None

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "power"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = Outputs()

        boiler_temp = sensors.boiler_temp

        # 1) Tryb efektywny na podstawie SystemState.mode (Enum BoilerMode)
        effective_mode = self._get_effective_mode(system_state)

        prev_power = self._power
        prev_mode = self._last_effective_mode

        # Reset PID przy zmianie trybu (żeby nie ciągnąć starej całki)
        if prev_mode is not None and prev_mode != effective_mode:
            self._reset_pid()

        # 2) Liczenie power w zależności od trybu
        if effective_mode == "off":
            self._power = 0.0

        else:
            if boiler_temp is not None:
                base_power = self._pid_step(now, boiler_temp)
            else:
                # brak pomiaru kotła – zostaw poprzednią moc
                base_power = self._power

            if effective_mode == "ignition" and boiler_temp is not None:
                # Bonus z wygładzaniem w zależności od tego,
                # o ile T_boiler jest poniżej zadanej
                margin = max(self._config.ignition_bonus_margin_degC, 0.1)
                t_set = self._config.boiler_set_temp

                if boiler_temp <= t_set - margin:
                    k = 1.0
                elif boiler_temp >= t_set:
                    k = 0.0
                else:
                    # liniowe przejście 1 -> 0 w przedziale (T_set - margin, T_set)
                    k = (t_set - boiler_temp) / margin

                bonus_factor = 1.0 + k * (self._config.ignition_power_bonus_percent / 100.0)
                power = base_power * bonus_factor
            else:
                # auto/work
                power = base_power

            self._power = power

        # 3) Ograniczenie do min/max
        self._power = max(self._config.min_power, min(self._power, self._config.max_power))

        # 4) Eventy (opcjonalne – do debugowania / historii)
        if prev_mode != effective_mode:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="POWER_MODE_CHANGED",
                    message=f"power: zmiana trybu na '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                )
            )

        if abs(self._power - prev_power) >= 5.0:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.DEBUG,
                    type="POWER_LEVEL_CHANGED",
                    message=(
                        f"power: {prev_power:.1f}% → {self._power:.1f}% "
                        f"(T_kotła={boiler_temp:.1f}°C, zadana={self._config.boiler_set_temp:.1f}°C)"
                        if boiler_temp is not None
                        else f"power: {prev_power:.1f}% → {self._power:.1f}% (brak T_kotła)"
                    ),
                    data={
                        "prev_power": prev_power,
                        "power": self._power,
                        "boiler_temp": boiler_temp,
                        "boiler_set_temp": self._config.boiler_set_temp,
                    },
                )
            )

        self._last_effective_mode = effective_mode

        # 5) Wyjście
        # UWAGA: upewnij się, że w Outputs masz dodane pole:
        #   power_percent: float = 0.0
        outputs.power_percent = self._power  # type: ignore[attr-defined]

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- LOGIKA POMOCNICZA ----------

    def _get_effective_mode(self, system_state: SystemState) -> str:
        """
        Mapuje SystemState.mode (Enum BoilerMode) na wewnętrzne stringi:

        - "ignition"  – tryb rozpalania (PID + bonus)
        - "auto"      – normalna praca z PID (WORK)
        - "off"       – wyłączony / manual (power = 0)
        """
        mode_enum = system_state.mode

        if mode_enum == BoilerMode.IGNITION:
            return "ignition"
        if mode_enum == BoilerMode.WORK:
            return "auto"
        if mode_enum in (BoilerMode.OFF, BoilerMode.MANUAL):
            # w OFF i MANUAL PowerModule nie powinien sterować mocą
            return "off"

        # fallback (teoretycznie nie powinien się zdarzyć)
        return self._config.mode.lower()

    def _reset_pid(self) -> None:
        self._integral = 0.0
        self._last_error = None
        self._last_tick_ts = None

    def _pid_step(self, now: float, boiler_temp: float) -> float:
        """
        Jeden krok PID-a: zwraca "surową" moc (base_power) bez bonusa ignition.
        """

        error = self._config.boiler_set_temp - boiler_temp

        if self._last_tick_ts is None:
            dt = None
        else:
            dt = now - self._last_tick_ts
            if dt <= 0:
                dt = None

        if dt is not None:
            self._integral += error * dt

        p_term = self._config.kp * error
        i_term = self._config.ki * self._integral

        if dt is not None and self._last_error is not None:
            d_term = self._config.kd * (error - self._last_error) / dt
        else:
            d_term = 0.0

        power = p_term + i_term + d_term

        self._last_error = error
        self._last_tick_ts = now

        return power

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "boiler_set_temp" in values:
            self._config.boiler_set_temp = float(values["boiler_set_temp"])

        if "kp" in values:
            self._config.kp = float(values["kp"])
        if "ki" in values:
            self._config.ki = float(values["ki"])
        if "kd" in values:
            self._config.kd = float(values["kd"])

        if "min_power" in values:
            self._config.min_power = float(values["min_power"])
        if "max_power" in values:
            self._config.max_power = float(values["max_power"])

        if "ignition_power_bonus_percent" in values:
            self._config.ignition_power_bonus_percent = float(values["ignition_power_bonus_percent"])
        if "ignition_bonus_margin_degC" in values:
            self._config.ignition_bonus_margin_degC = float(values["ignition_bonus_margin_degC"])

        if "mode" in values:
            self._config.mode = str(values["mode"])

        if persist:
            self._save_config_to_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for field in (
            "boiler_set_temp",
            "kp",
            "ki",
            "kd",
            "min_power",
            "max_power",
            "ignition_power_bonus_percent",
            "ignition_bonus_margin_degC",
        ):
            if field in data:
                setattr(self._config, field, float(data[field]))

        if "mode" in data:
            self._config.mode = str(data["mode"])

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

