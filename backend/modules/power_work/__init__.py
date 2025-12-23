from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import time

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

    NOWE (restart + czas):
      - moduł używa monotonicznego czasu system_state.ts_mono do liczenia dt,
        więc zmiana czasu/NTP nie rozwali PID-a.
      - stan PID (całka + kilka pól) zapisuje na dysk i przy starcie próbuje
        przywrócić, ale tylko jeśli plik jest "świeży" i temp. kotła pasuje.
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

    # limiter szybkości zmian mocy w WORK
    max_slew_rate_percent_per_min: float = 0.0  # 0.0 = wyłączony

    # --- PERSIST STANU (jak history) ---
    state_dir: str = "data"  # względnie do katalogu modułu
    state_file: str = "power_work_state.yaml"
    state_save_interval_s: float = 30.0  # co ile sekund zapisywać stan
    state_max_age_s: float = 15 * 60.0  # ignoruj plik stanu starszy niż X sekund (0 = nie sprawdzaj)
    state_max_temp_delta_C: float = 5.0  # ignoruj restore, jeśli ΔT_kotła za duże


class WorkPowerModule(ModuleInterface):
    """
    Moduł wyliczający "power" (moc kotła) w % w trybie WORK (praca).

    ZMIANY:
      - dt liczone z system_state.ts_mono (monotonic)
      - zapis/restore stanu PID na dysk (bez psucia logiki, jak brak/za stare => działa jak teraz)
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
        # UWAGA: teraz trzymamy czas "kontrolny" (monotonic)
        self._last_tick_ts: Optional[float] = None

        # Stan mocy (ostatnia moc w TRYBIE WORK / tracking)
        self._power: float = 0.0
        self._last_in_work: bool = False

        # Stan dla limitu zmian mocy (slew rate) – też w czasie monotonic
        self._last_power_ts: Optional[float] = None

        # Persist stanu
        self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file
        self._last_state_save_wall_ts: Optional[float] = None  # do interwału zapisu (wall time OK)

        # Po restore trzymamy meta do walidacji (dopiero gdy mamy temp. kotła)
        self._restored_state_meta: Optional[Dict[str, Any]] = None
        self._try_restore_state_from_disk()

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "power_work"

    def tick(
        self,
        now: float,  # wall time z kernela (zostaje jak było)
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

        # CZAS KONTROLNY: monotonic z SystemState (nie zależy od zmiany czasu/NTP)
        now_ctrl = float(getattr(system_state, "ts_mono", now))

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

        # Jeśli przywróciliśmy stan z dysku, walidujemy go dopiero jak mamy temp.
        if boiler_temp is not None and self._restored_state_meta is not None:
            if not self._validate_restored_state(boiler_temp, now, events):
                # jeśli odrzucamy restore -> działamy jak wcześniej (czysty start PID-a)
                self._integral = 0.0
                self._last_error = None
                self._last_tick_ts = None
                self._last_power_ts = None
            self._restored_state_meta = None

        # --- AKTUALIZACJA STANU PID / TRACKING ---

        if boiler_temp is not None:
            if in_work:
                # Normalna praca PID – regulujemy do zadanej temperatury.
                base_power = self._pid_step(now_ctrl, boiler_temp)
            else:
                # Poza WORK: NIE trackuj do outputs.power_percent w OFF/MANUAL,
                # bo OFF zwykle ustawia power_percent=0 i to "zeruje" całkę.
                if system_state.mode == BoilerMode.IGNITION:
                    actual_power = system_state.outputs.power_percent
                    self._track_to_power(now_ctrl, boiler_temp, actual_power)
                else:
                    # OFF/MANUAL: tylko licz PID żeby stan się aktualizował, ale nic nie wymuszaj
                    self._pid_step(now_ctrl, boiler_temp)

                base_power = self._power
        else:
            # Brak pomiaru – trzymaj się ostatniej znanej mocy.
            base_power = self._power

        # --- Tryby inne niż WORK: nie nadpisujemy outputs.power_percent ---

        if not in_work:
            self._last_in_work = in_work

            # zapis stanu też ma sens poza WORK (żeby nie tracić całki po restarcie)
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)

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
            dt = now_ctrl - self._last_power_ts
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
        self._last_power_ts = now_ctrl

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

        # persist stanu
        self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)

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
        """
        self._integral = 0.0
        self._last_error = None
        self._last_tick_ts = None
        self._last_power_ts = None

    def _pid_step(self, now_ctrl: float, boiler_temp: float) -> float:
        """
        Jeden krok PID-a – z oknem całki integral_window_s.
        UWAGA: now_ctrl = czas monotoniczny (SystemState.ts_mono).
        """
        error = self._config.boiler_set_temp - boiler_temp

        if self._last_tick_ts is None:
            dt = None
        else:
            dt = now_ctrl - self._last_tick_ts
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
        self._last_tick_ts = now_ctrl

        return power

    def _track_to_power(self, now_ctrl: float, boiler_temp: float, actual_power: float) -> None:
        """
        Tryb śledzenia (bumpless transfer) – czas monotoniczny.
        """
        error = self._config.boiler_set_temp - boiler_temp

        # Aktualizujemy czas i błąd, żeby _pid_step miał później sensowne dt
        self._last_tick_ts = now_ctrl
        self._last_error = error

        if self._config.ki <= 0.0:
            self._power = actual_power
            return

        p_term = self._config.kp * error
        d_term = 0.0

        integral = (actual_power - p_term - d_term) / self._config.ki

        max_int = 10000.0
        if integral > max_int:
            integral = max_int
        elif integral < -max_int:
            integral = -max_int

        self._integral = integral
        self._power = actual_power

    # ---------- PERSIST STANU (restart) ----------

    def _try_restore_state_from_disk(self) -> None:
        """
        Przy starcie modułu: próbujemy wczytać stan PID z dysku.
        Jeśli brak pliku / błąd / za stare => ignorujemy i działamy jak teraz.
        Walidację temperatury robimy dopiero w tick(), gdy mamy boiler_temp.
        """
        self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        if not self._state_path.exists():
            return

        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return

        saved_wall_ts = data.get("saved_wall_ts")
        if not isinstance(saved_wall_ts, (int, float)):
            return

        # sprawdzamy wiek po wall-time (bo monotonic nie da się odtworzyć po restarcie)
        max_age = float(self._config.state_max_age_s)
        if max_age > 0:
            age = time.time() - float(saved_wall_ts)
            if age < 0:
                return
            if age > max_age:
                return

        integral = data.get("integral")
        last_error = data.get("last_error")
        power = data.get("power")
        saved_ki = data.get("ki")

        if not isinstance(integral, (int, float)):
            return
        if last_error is not None and not isinstance(last_error, (int, float)):
            return
        if not isinstance(power, (int, float)):
            return

        # Reskalowanie całki jeśli ki się zmieniło (utrzymujemy i_term ~ const)
        if isinstance(saved_ki, (int, float)) and float(saved_ki) > 0 and self._config.ki > 0:
            integral = float(integral) * (float(saved_ki) / float(self._config.ki))

        self._integral = float(integral)
        self._last_error = float(last_error) if last_error is not None else None
        self._power = float(power)

        # Start "jak teraz": dt dopiero od pierwszego ticka, slew bez pamięci czasu
        self._last_tick_ts = None
        self._last_power_ts = None

        self._restored_state_meta = {
            "saved_wall_ts": float(saved_wall_ts),
            "saved_boiler_temp": data.get("boiler_temp"),
        }

    def _validate_restored_state(self, current_boiler_temp: float, now_wall: float, events: List[Event]) -> bool:
        """
        Jeśli w pliku była boiler_temp, sprawdzamy czy nie ma skoku warunków.
        Jeśli ΔT za duże => ignorujemy restore.
        """
        meta = self._restored_state_meta or {}
        saved_temp = meta.get("saved_boiler_temp")

        if saved_temp is None or not isinstance(saved_temp, (int, float)):
            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_POWER_STATE_RESTORED",
                    message="power_work: przywrócono stan PID z dysku (bez walidacji temp)",
                    data={},
                )
            )
            return True

        delta = abs(float(current_boiler_temp) - float(saved_temp))
        if delta > float(self._config.state_max_temp_delta_C):
            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_POWER_STATE_RESTORE_SKIPPED",
                    message=(
                        f"power_work: pominięto restore stanu PID "
                        f"(ΔT={delta:.1f}°C > {self._config.state_max_temp_delta_C:.1f}°C)"
                    ),
                    data={
                        "delta_temp": delta,
                        "saved_temp": float(saved_temp),
                        "current_temp": float(current_boiler_temp),
                    },
                )
            )
            return False

        events.append(
            Event(
                ts=now_wall,
                source=self.id,
                level=EventLevel.INFO,
                type="WORK_POWER_STATE_RESTORED",
                message="power_work: przywrócono stan PID z dysku",
                data={
                    "saved_temp": float(saved_temp),
                    "current_temp": float(current_boiler_temp),
                },
            )
        )
        return True

    def _maybe_persist_state(self, now_wall: float, boiler_temp: Optional[float], events: List[Event]) -> None:
        interval = float(self._config.state_save_interval_s)
        if interval <= 0:
            return

        if self._last_state_save_wall_ts is not None and (now_wall - self._last_state_save_wall_ts) < interval:
            return

        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)

            data = {
                "saved_wall_ts": float(now_wall),
                "boiler_temp": float(boiler_temp) if boiler_temp is not None else None,
                "integral": float(self._integral),
                "last_error": float(self._last_error) if self._last_error is not None else None,
                "power": float(self._power),
                # zapisujemy też PID żeby móc reskalować przy zmianie Ki
                "kp": float(self._config.kp),
                "ki": float(self._config.ki),
                "kd": float(self._config.kd),
                "integral_window_s": float(self._config.integral_window_s),
            }

            tmp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

            tmp_path.replace(self._state_path)
            self._last_state_save_wall_ts = now_wall

        except Exception as exc:
            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.WARNING,
                    type="WORK_POWER_STATE_SAVE_ERROR",
                    message=f"power_work: błąd zapisu stanu PID: {exc}",
                    data={"error": str(exc)},
                )
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
            self._config.max_slew_rate_percent_per_min = float(values["max_slew_rate_percent_per_min"])

        # persist: katalog/pliki/limity
        if "state_dir" in values:
            self._config.state_dir = str(values["state_dir"])
            self._state_dir = (self._base_path / self._config.state_dir).resolve()
            self._state_path = self._state_dir / self._config.state_file

        if "state_file" in values:
            self._config.state_file = str(values["state_file"])
            self._state_path = self._state_dir / self._config.state_file

        if "state_save_interval_s" in values:
            self._config.state_save_interval_s = float(values["state_save_interval_s"])

        if "state_max_age_s" in values:
            self._config.state_max_age_s = float(values["state_max_age_s"])

        if "state_max_temp_delta_C" in values:
            self._config.state_max_temp_delta_C = float(values["state_max_temp_delta_C"])

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()
        # odśwież ścieżki stanu (jeśli ktoś zmienił state_dir/state_file)
        self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # floaty
        for field_name in (
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
            "state_save_interval_s",
            "state_max_age_s",
            "state_max_temp_delta_C",
        ):
            if field_name in data:
                setattr(self._config, field_name, float(data[field_name]))

        # stringi
        for field_name in ("state_dir", "state_file"):
            if field_name in data:
                setattr(self._config, field_name, str(data[field_name]))

        # update ścieżek
        self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

