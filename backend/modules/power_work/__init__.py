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
	PartialOutputs
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

    Ograniczenie szybkości zmian mocy:
      max_slew_rate_percent_per_min
                             – maksymalna zmiana mocy [pkt%] na minutę
                               (0.0 wyłącza ograniczenie).

    Działa TYLKO gdy SystemState.mode == BoilerMode.WORK,
    tzn. TYLKO wtedy ustawia outputs.power_percent.

    W innych trybach:
      - PID i całka dalej się liczą / dopasowują (tracking),
      - ale moduł NIE nadpisuje outputs.power_percent.

    Dodatkowo:
      - w trybach innych niż WORK moduł robi "tracking" – dopasowuje
        stan całki tak, żeby wyjście PID odpowiadało aktualnej mocy
        kotła (SystemState.outputs.power_percent). Dzięki temu przy
        przejściu do WORK nie ma skoku mocy (bumpless transfer).
    """

    boiler_set_temp: float = 55.0

    kp: float = 2.0
    ki: float = 0.01
    kd: float = 0.0

    integral_window_s: float = 300.0

    min_power: float = 10.0
    max_power: float = 100.0

    overtemp_start_degC: float = 3.0
    overtemp_kp: float = 10.0

    # nowy parametr – limiter szybkości zmian mocy w WORK
    max_slew_rate_percent_per_min: float = 0.0  # 0.0 = wyłączony


class WorkPowerModule(ModuleInterface):
    """
    Moduł wyliczający "power" (moc kotła) w % w trybie WORK (praca).

    - W trybie WORK: klasyczny PID(T_set, T_boiler) + korekta przegrzania,
      wynik ograniczony do [min_power, max_power], dodatkowo ograniczony
      tempem zmian (max_slew_rate_percent_per_min), wynik trafia do
      outputs.power_percent.

    - W trybach innych niż WORK (IGNITION / OFF / MANUAL):
      moduł NIE nadpisuje outputs.power_percent, ale:
        * jeśli jest pomiar T_kotła, pobiera aktualną moc kotła
          z system_state.outputs.power_percent,
        * dopasowuje stan całki PID tak, aby wyjście PID było
          równe tej mocy (tracking / bumpless transfer).

      Dzięki temu przy przejściu IGNITION -> WORK nie ma
      gwałtownego skoku mocy: PID w WORK startuje z poziomu
      zbliżonego do tego, który dawał IGNITION.
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

        # Stan mocy (ostatnia moc w TRYBIE WORK / tracking)
        self._power: float = 0.0
        self._last_in_work: bool = False

        # Stan dla limitu zmian mocy (slew rate)
        self._last_power_ts: Optional[float] = None

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
        outputs = PartialOutputs()

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

        # Przy wejściu w WORK – pierwszy krok bez limitu slew rate,
        # więc zerujemy znacznik czasu dla mocy.
        if not prev_in_work and in_work:
            self._last_power_ts = None

        # --- AKTUALIZACJA STANU PID / TRACKING ---

        if boiler_temp is not None:
            if in_work:
                # Normalna praca PID – regulujemy do zadanej temperatury.
                base_power = self._pid_step(now, boiler_temp)
            else:
                # Poza WORK: NIE trackuj do outputs.power_percent w OFF/MANUAL,
                # bo OFF zwykle ustawia power_percent=0 i to "zeruje" całkę.
                if system_state.mode == BoilerMode.IGNITION:
                    actual_power = system_state.outputs.power_percent
                    self._track_to_power(now, boiler_temp, actual_power)
                else:
                    # OFF/MANUAL: tylko licz PID żeby stan się aktualizował, ale nic nie wymuszaj
                    self._pid_step(now, boiler_temp)
        
                base_power = self._power
        else:
            # Brak pomiaru – nie mamy sensownych danych do PID/trackingu,
            # trzymaj się ostatniej znanej mocy.
            base_power = self._power


        # --- Tryby inne niż WORK: nie nadpisujemy outputs.power_percent ---

        if not in_work:
            self._last_in_work = in_work

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(
                partial_outputs=outputs,
                events=events,
                status=status,
            )

        # --- Tryb WORK – PID + przegrzanie + ograniczenia + limiter zmian mocy ---

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

        # --- OGRANICZENIE SZYBKOŚCI ZMIANY MOCY (SLEW RATE) W TRYBIE WORK ---

        limited_power = power
        max_slew_per_min = max(self._config.max_slew_rate_percent_per_min, 0.0)

        if (
            max_slew_per_min > 0.0
            and self._last_power_ts is not None
            and prev_in_work
        ):
            dt = now - self._last_power_ts
            if dt > 0:
                max_delta = max_slew_per_min * dt / 60.0  # pkt% dozwolone w tym kroku
                delta = power - prev_power

                if delta > max_delta:
                    limited_power = prev_power + max_delta
                elif delta < -max_delta:
                    limited_power = prev_power - max_delta
                else:
                    limited_power = power
        else:
            # Pierwszy krok po wejściu w WORK (albo limiter wyłączony) – bez ograniczenia.
            limited_power = power

        # Jeszcze raz upewniamy się, że w zakresie min/max
        limited_power = max(self._config.min_power, min(limited_power, self._config.max_power))

        self._power = limited_power
        self._last_power_ts = now

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

        # W TRYBIE WORK nadpisujemy sygnał mocy kotła
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
        Nie jest używany automatycznie przy zmianie trybu, bo dzięki trackingowi
        całka dopasowuje się do aktualnej mocy także poza WORK.
        """
        self._integral = 0.0
        self._last_error = None
        self._last_tick_ts = None

    def _pid_step(self, now: float, boiler_temp: float) -> float:
        """
        Jeden krok PID-a – z oknem całki integral_window_s.

        Wywoływany przy każdym ticku, gdy jest dostępna T_kotła
        i jesteśmy w trybie WORK.
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

    def _track_to_power(self, now: float, boiler_temp: float, actual_power: float) -> None:
        """
        Tryb śledzenia (bumpless transfer):

        W trybach innych niż WORK nie sterujemy kotłem, ale dopasowujemy
        stan całki PID-a tak, aby wyjście PID (P+I+D) było równe
        aktualnej mocy kotła (actual_power), która pochodzi np. z
        modułu IGNITION i jest widoczna w system_state.outputs.power_percent.

        Dzięki temu przy przejściu do WORK wyjście PID już jest
        zsynchronizowane z realną mocą i nie ma skoku (kopniaka).
        """
        error = self._config.boiler_set_temp - boiler_temp

        # Aktualizujemy czas i błąd, żeby _pid_step miał później sensowne dt
        self._last_tick_ts = now
        self._last_error = error

        if self._config.ki <= 0.0:
            # Bez członu I nie mamy czego dopasować – tracking działa tylko przez I.
            self._power = actual_power
            return

        p_term = self._config.kp * error
        # U Ciebie kd zwykle jest 0.0, więc d_term = 0; jeśli kiedyś
        # użyjesz D, można dodać prostą aproksymację.
        d_term = 0.0

        # Liczymy taką całkę, żeby P + I + D ~= actual_power
        integral = (actual_power - p_term - d_term) / self._config.ki

        # Proste anti-windup dla bezpieczeństwa
        max_int = 10000.0
        if integral > max_int:
            integral = max_int
        elif integral < -max_int:
            integral = -max_int

        self._integral = integral
        self._power = actual_power

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

        if "max_slew_rate_percent_per_min" in values:
            self._config.max_slew_rate_percent_per_min = float(
                values["max_slew_rate_percent_per_min"]
            )

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
            "max_slew_rate_percent_per_min",
        ):
            if field in data:
                setattr(self._config, field, float(data[field]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
