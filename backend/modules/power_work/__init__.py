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
class WorkPowerConfig:
    """
    Moduł regulatora mocy kotła dla trybu WORK (normalna praca).

    boiler_set_temp          – zadana temperatura kotła [°C]

    PID:
      kp, ki, kd             – parametry PID (błąd w °C -> wynik w % mocy)
      integral_window_s      – efektywne "okno czasowe" całki [s].
                               Im mniejsze, tym szybciej "zapominane"
                               są stare błędy (mniejszy windup).

    Ograniczenia:
      min_power, max_power   – ograniczenia mocy [%]

    Korekta przegrzania:
      overtemp_start_degC    – o ile °C powyżej zadanej zaczynamy
                               dodatkowe obniżanie mocy.
      overtemp_kp            – ile punktów procentowych mocy odejmujemy
                               za każdy °C powyżej progu przegrzania.

    Działa TYLKO gdy SystemState.mode == BoilerMode.WORK,
    tzn. TYLKO wtedy ustawia outputs.power_percent.

    W innych trybach:
      - PID i całka dalej się liczą (na podstawie T_kotła),
      - ale moduł NIE nadpisuje outputs.power_percent.
    """

    boiler_set_temp: float = 65.0

    kp: float = 2.0
    ki: float = 0.01
    kd: float = 0.0

    integral_window_s: float = 300.0

    min_power: float = 10.0
    max_power: float = 100.0

    overtemp_start_degC: float = 3.0
    overtemp_kp: float = 10.0


class WorkPowerModule(ModuleInterface):
    """
    Moduł wyliczający "power" (moc kotła) w % w trybie WORK (praca).

    - PID i całka liczą się cały czas (jeśli jest T_kotła),
      niezależnie od trybu.
    - outputs.power_percent jest ustawiane TYLKO gdy
      SystemState.mode == BoilerMode.WORK.
    - W innych trybach moduł nie nadpisuje outputs.power_percent
      (np. w IGNITION robi to osobny moduł).

    Algorytm w trybie WORK:
      - PID(T_set, T_boiler) z oknem całki integral_window_s,
      - korekta przy przegrzaniu (powyżej T_set + overtemp_start_degC),
      - ograniczenie min/max.
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[WorkPowerConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or WorkPowerConfig()
        self._load_config_from_file()

        # Stan PID-a
        self._integral: float = 0.0
        self._last_error: Optional[float] = None
        self._last_tick_ts: Optional[float] = None

        # Stan mocy (ostatnia moc w TRYBIE WORK)
        self._power: float = 0.0
        self._last_in_work: bool = False

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "power_work"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = Outputs()

        boiler_temp = sensors.boiler_temp
        mode_enum = system_state.mode
        in_work = (mode_enum == BoilerMode.WORK)

        prev_power = self._power
        prev_in_work = self._last_in_work

        # Zdarzenia zmiany trybu
        if prev_in_work != in_work:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_POWER_MODE_CHANGED",
                    message=f"power_work: {'ENTER' if in_work else 'LEAVE'} WORK",
                    data={"in_work": in_work},
                )
            )
            # Nie resetujemy PID przy wyjściu z WORK – całka ma się liczyć cały czas.
            # self._reset_pid()

        # --- PID LICZY SIĘ ZAWSZE (jeśli jest T_kotła) ---

        if boiler_temp is not None:
            # Aktualizacja stanu PID (P/I/D, dt, okno całki)
            base_power = self._pid_step(now, boiler_temp)
        else:
            # Brak pomiaru – użyj ostatniej znanej mocy z WORK jako "bazowej"
            base_power = self._power

        # --- Tryby inne niż WORK: nie nadpisujemy outputs.power_percent ---

        if not in_work:
            # W innych trybach ten moduł NIC nie robi z power_percent.
            # Stan PID-a jest już zaktualizowany powyżej.
            self._last_in_work = in_work

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(
                partial_outputs=outputs,
                events=events,
                status=status,
            )

        # --- Tryb WORK – PID + przegrzanie + ograniczenia ---

        power = base_power

        # Korekta przegrzania
        if boiler_temp is not None:
            t_set = self._config.boiler_set_temp
            start = max(self._config.overtemp_start_degC, 0.0)

            if boiler_temp > t_set + start:
                over = boiler_temp - (t_set + start)
                penalty = over * max(self._config.overtemp_kp, 0.0)
                power -= penalty

        # Ograniczenia min/max
        power = max(self._config.min_power, min(power, self._config.max_power))
        self._power = power

        # Logowanie większych zmian mocy
        if abs(self._power - prev_power) >= 5.0:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_POWER_LEVEL_CHANGED",
                    message=(
                        f"power_work: {prev_power:.1f}% → {self._power:.1f}% "
                        f"(T_kotła={boiler_temp:.1f}°C, zadana={self._config.boiler_set_temp:.1f}°C)"
                        if boiler_temp is not None
                        else f"power_work: {prev_power:.1f}% → {self._power:.1f}% (brak T_kotła)"
                    ),
                    data={
                        "prev_power": prev_power,
                        "power": self._power,
                        "boiler_temp": boiler_temp,
                        "boiler_set_temp": self._config.boiler_set_temp,
                    },
                )
            )

        outputs.power_percent = self._power  # type: ignore[attr-defined]
        self._last_in_work = in_work

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- LOGIKA POMOCNICZA ----------

    def _reset_pid(self) -> None:
        """
        Pomocniczy reset stanu PID – zostawiony na przyszłość (np. do ręcznego resetu).
        Nie jest używany automatycznie przy zmianie trybu, żeby całka mogła
        liczyć się nieprzerwanie także poza WORK.
        """
        self._integral = 0.0
        self._last_error = None
        self._last_tick_ts = None

    def _pid_step(self, now: float, boiler_temp: float) -> float:
        """
        Jeden krok PID-a – z oknem całki integral_window_s.

        Wywoływany przy każdym ticku, gdy jest dostępna T_kotła,
        niezależnie od trybu (WORK / IGNITION / inne).
        """
        error = self._config.boiler_set_temp - boiler_temp

        if self._last_tick_ts is None:
            dt = None
        else:
            dt = now - self._last_tick_ts
            if dt <= 0:
                dt = None

        # Część I z "oknem czasowym" – leaky integrator
        if dt is not None:
            window = max(self._config.integral_window_s, 1.0)
            decay = 1.0 - dt / window
            if decay < 0.0:
                decay = 0.0
            elif decay > 1.0:
                decay = 1.0

            self._integral *= decay
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

        if "integral_window_s" in values:
            self._config.integral_window_s = float(values["integral_window_s"])

        if "min_power" in values:
            self._config.min_power = float(values["min_power"])
        if "max_power" in values:
            self._config.max_power = float(values["max_power"])

        if "overtemp_start_degC" in values:
            self._config.overtemp_start_degC = float(values["overtemp_start_degC"])
        if "overtemp_kp" in values:
            self._config.overtemp_kp = float(values["overtemp_kp"])

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()

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
            "integral_window_s",
            "min_power",
            "max_power",
            "overtemp_start_degC",
            "overtemp_kp",
        ):
            if field in data:
                setattr(self._config, field, float(data[field]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
