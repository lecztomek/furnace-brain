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

    boiler_set_temp          – zadana temperatura kotła [°C]

    PID (tylko w trybie pracy / WORK):
      kp, ki, kd             – parametry PID (błąd w °C -> wynik w % mocy)
      integral_window_s      – efektywne "okno czasowe" całki [s].
                               Im mniejsze, tym szybciej "zapominane"
                               są stare błędy (mniejszy windup).
      overtemp_start_degC    – o ile °C powyżej zadanej zaczynamy
                               dodatkowe obniżanie mocy.
      overtemp_kp            – ile punktów procentowych mocy odejmujemy
                               za każdy °C powyżej progu przegrzania.

    Ograniczenia:
      min_power, max_power   – ograniczenia mocy [%]

    IGNITION – osobny algorytm (hybryda):
      1) z odległości od zadanej (ΔT):
         ignition_high_power_percent      – moc przy dużym ΔT [%]
         ignition_min_power_percent       – najmniejsza moc w IGNITION [%]
         ignition_full_power_delta_degC   – od ilu °C poniżej zadanej
                                            ma być pełna moc (high_power)
         ignition_min_power_delta_degC    – do ilu °C poniżej zadanej
                                            schodzimy z mocą do min_power
                                            (poniżej tego progu trzymamy min_power)

      2) z tempa wzrostu temperatury (dT/dt) – tylko jako BOOST w górę:
         ignition_target_rate_k_per_min          – docelowy przyrost T [°C/min]
         ignition_rate_band_k_per_min            – tolerancja wokół celu [°C/min]
         ignition_rate_gain_percent_per_k_per_min– ile % mocy dodać za
                                                   każdy 1°C/min brakujący
                                                   do dolnej granicy pasma
         ignition_rate_max_boost_percent         – maksymalny boost z dT/dt [%]

      Moc IGNITION = max(moc_z_ΔT, moc_z_dTdt)

    mode                     – lokalny tryb, używany TYLKO gdy z jakiegoś
                               powodu SystemState.mode jest nieznany.
                               "auto", "ignition", "off"
    """

    boiler_set_temp: float = 65.0

    # PID (tylko dla WORK)
    kp: float = 2.0
    ki: float = 0.01
    kd: float = 0.0

    integral_window_s: float = 300.0       # ~5 minut historii całki
    overtemp_start_degC: float = 3.0       # od zadanej + 3°C zaczynamy ciąć
    overtemp_kp: float = 10.0              # 10 pkt% mocy mniej za każdy °C przegrzania

    # Ograniczenia globalne
    min_power: float = 0.0
    max_power: float = 100.0

    # IGNITION – część ΔT
    ignition_high_power_percent: float = 100.0      # pełna moc przy dużym ΔT
    ignition_min_power_percent: float = 30.0        # najmniejsza moc w IGNITION
    ignition_full_power_delta_degC: float = 15.0    # ΔT >= 15°C -> high_power
    ignition_min_power_delta_degC: float = 3.0      # ΔT <= 3°C  -> min_power

    # IGNITION – część dT/dt (tylko boost w górę)
    ignition_target_rate_k_per_min: float = 0.8          # docelowy przyrost [°C/min]
    ignition_rate_band_k_per_min: float = 0.3            # tolerancja wokół celu [°C/min]
    ignition_rate_gain_percent_per_k_per_min: float = 10.0  # %/ (°C/min)
    ignition_rate_max_boost_percent: float = 30.0        # maksymalny boost [%]

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

    - IGNITION:
        OSOBNY ALGORYTM (BEZ PID), hybryda:
        - bazowa moc z ΔT (odległość od zadanej),
        - ewentualny dodatkowy boost z dT/dt, jeśli kocioł nagrzewa się zbyt wolno.
        Końcowo: power_ign = max(power_delta, power_rate).

    - WORK (auto):
        PID z oknem całki + dodatkowe obniżanie mocy przy przegrzaniu.
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

        # Stan PID-a (używany tylko w WORK)
        self._integral: float = 0.0
        self._last_error: Optional[float] = None
        self._last_tick_ts: Optional[float] = None

        # Stan mocy
        self._power: float = 0.0
        self._last_effective_mode: Optional[str] = None

        # Stan dla algorytmu IGNITION opartego o dT/dt
        self._ign_last_temp: Optional[float] = None
        self._ign_last_ts: Optional[float] = None
        self._ign_rate_ema: Optional[float] = None

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

        # Reset PID-a tylko przy "dużych" zmianach trybu (np. AUTO <-> OFF)
        if prev_mode is not None and prev_mode != effective_mode:
            ignition_auto_set = {"ignition", "auto"}
            if not ({prev_mode, effective_mode} <= ignition_auto_set):
                self._reset_pid()

            # przy wejściu w IGNITION resetujemy historię dT/dt
            if effective_mode == "ignition":
                self._ign_last_ts = None
                self._ign_last_temp = None
                self._ign_rate_ema = None

        # Bumpless transfer: przejście IGNITION -> AUTO (WORK)
        if (
            prev_mode == "ignition"
            and effective_mode == "auto"
            and boiler_temp is not None
            and self._config.ki > 0.0
        ):
            # dopasuj całkę tak, aby PID startował z obecną mocą
            error = self._config.boiler_set_temp - boiler_temp
            p_term = self._config.kp * error
            d_term = 0.0  # dla prostoty ignorujemy D przy starcie

            target_power = self._power  # moc z algorytmu IGNITION
            i_term = target_power - p_term - d_term
            self._integral = i_term / self._config.ki

            self._last_error = error
            self._last_tick_ts = now

        # 2) Liczenie power w zależności od trybu
        if effective_mode == "off":
            self._power = 0.0

        elif effective_mode == "ignition":
            # OSOBNY ALGORYTM ROZPALANIA – hybryda ΔT + dT/dt
            self._power = self._compute_ignition_power(now, boiler_temp)

        else:
            # AUTO / WORK – tutaj działa PID
            if boiler_temp is not None:
                base_power = self._pid_step(now, boiler_temp)
            else:
                # brak pomiaru kotła – zostaw poprzednią moc
                base_power = self._power

            power = base_power

            # --- DODATKOWE OBNIŻANIE MOCY PRZY PRZEGRZANIU ---
            if boiler_temp is not None:
                t_set = self._config.boiler_set_temp
                start = max(self._config.overtemp_start_degC, 0.0)

                # zaczynamy ciąć powyżej T_set + start
                if boiler_temp > t_set + start:
                    over = boiler_temp - (t_set + start)  # tylko "nadmiar"
                    penalty = over * max(self._config.overtemp_kp, 0.0)
                    power -= penalty

            self._power = power

        # 3) Ograniczenie do min/max (globalne)
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
                    level=EventLevel.INFO,
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

        - "ignition"  – tryb rozpalania (osobny algorytm)
        - "auto"      – normalna praca z PID (WORK)
        - "off"       – wyłączony / manual (power = 0)
        """
        mode_enum = system_state.mode

        if mode_enum == BoilerMode.IGNITION:
            return "ignition"
        if mode_enum == BoilerMode.WORK:
            return "auto"
        if mode_enum in (BoilerMode.OFF, BoilerMode.MANUAL):
            return "off"

        # fallback
        return self._config.mode.lower()

    def _reset_pid(self) -> None:
        self._integral = 0.0
        self._last_error = None
        self._last_tick_ts = None

    # --- IGNITION: HYBRYDA ΔT + dT/dt ---

    def _compute_ignition_power(self, now: float, boiler_temp: Optional[float]) -> float:
        """
        Hybryda:

          power_delta = funkcja liniowa ΔT (odległość od zadanej)
          power_rate  = boost w górę na podstawie dT/dt (jeśli za wolno grzeje)

          power_ign = max(power_delta, power_rate)
        """

        power_delta = self._ignition_power_from_delta(boiler_temp)
        power_rate = self._ignition_power_from_rate(now, boiler_temp, power_delta)
        return max(power_delta, power_rate)

    def _ignition_power_from_delta(self, boiler_temp: Optional[float]) -> float:
        """
        Część bazowa: moc z ΔT = T_set - T_boiler.
        """

        high_p = self._config.ignition_high_power_percent
        min_ign = self._config.ignition_min_power_percent
        full_delta = max(self._config.ignition_full_power_delta_degC, 0.1)
        min_delta = max(self._config.ignition_min_power_delta_degC, 0.0)

        # brak pomiaru -> pełna moc ignition
        if boiler_temp is None:
            return high_p

        t_set = self._config.boiler_set_temp
        delta = t_set - boiler_temp  # dodatnie: poniżej zadanej

        if delta >= full_delta:
            power = high_p
        elif delta <= min_delta:
            power = min_ign
        else:
            # liniowa interpolacja: delta pełne -> high_p, delta minimalne -> min_ign
            alpha = (delta - min_delta) / (full_delta - min_delta)  # 0..1
            power = min_ign + alpha * (high_p - min_ign)

        return power

    def _ignition_power_from_rate(
        self,
        now: float,
        boiler_temp: Optional[float],
        base_power: float,
    ) -> float:
        """
        Część dT/dt:

        - liczymy tempo nagrzewania [°C/min] z prostą EMA (wygładzenie),
        - jeśli tempo >= (target - band) -> grzeje wystarczająco szybko,
          NIE zmieniamy mocy (zwracamy base_power),
        - jeśli tempo < (target - band) -> za wolno:
              deficit = (target - band) - rate
              extra = deficit * gain  [pkt %]
              extra <= ignition_rate_max_boost_percent
          i zwracamy base_power + extra.

        UWAGA: tutaj NIGDY nie zmniejszamy mocy, tylko ewentualnie dodajemy
        boost w górę. Cięcie mocy robi część ΔT + potem PID w WORK.
        """

        if boiler_temp is None:
            # bez sensu liczyć tempo, zostaw bazę
            self._ign_last_ts = None
            self._ign_last_temp = None
            self._ign_rate_ema = None
            return base_power

        # brak historii -> inicjalizacja, jeszcze nie boostujemy
        if self._ign_last_ts is None or self._ign_last_temp is None:
            self._ign_last_ts = now
            self._ign_last_temp = boiler_temp
            self._ign_rate_ema = None
            return base_power

        dt = now - self._ign_last_ts
        if dt <= 0:
            dt = 1.0  # awaryjnie, żeby uniknąć dzielenia przez zero

        inst_rate = (boiler_temp - self._ign_last_temp) / dt * 60.0  # °C/min

        self._ign_last_ts = now
        self._ign_last_temp = boiler_temp

        # prosta EMA dla wygładzenia (tau ~ 30 s)
        if self._ign_rate_ema is None:
            rate = inst_rate
        else:
            tau = 30.0
            alpha = max(0.0, min(1.0, dt / (tau + dt)))
            rate = self._ign_rate_ema + alpha * (inst_rate - self._ign_rate_ema)

        self._ign_rate_ema = rate

        target = self._config.ignition_target_rate_k_per_min
        band = self._config.ignition_rate_band_k_per_min
        gain = self._config.ignition_rate_gain_percent_per_k_per_min
        max_boost = self._config.ignition_rate_max_boost_percent

        low = target - band  # poniżej tego -> za wolno

        if rate >= low:
            # nagrzewa się wystarczająco szybko – nie dodajemy boosta
            return base_power

        deficit = low - rate  # dodatnie, jeśli za wolno
        extra = deficit * gain
        if extra > max_boost:
            extra = max_boost
        if extra < 0.0:
            extra = 0.0

        return base_power + extra

    # --- PID (WORK) ---

    def _pid_step(self, now: float, boiler_temp: float) -> float:
        """
        Jeden krok PID-a: zwraca "surową" moc (base_power) w trybie WORK.

        Całka ma "okno czasowe" integral_window_s – stare błędy są stopniowo
        wygaszane, co ogranicza windup.
        """

        error = self._config.boiler_set_temp - boiler_temp

        if self._last_tick_ts is None:
            dt = None
        else:
            dt = now - self._last_tick_ts
            if dt <= 0:
                dt = None

        if dt is not None:
            # --- OKNO CZASOWE DLA CAŁKI ---
            window = max(self._config.integral_window_s, 1.0)  # min 1 s
            decay = 1.0 - dt / window
            if decay < 0.0:
                decay = 0.0
            elif decay > 1.0:
                decay = 1.0

            self._integral *= decay
            self._integral += error * dt

        # --- P, I, D ---
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
        if "overtemp_start_degC" in values:
            self._config.overtemp_start_degC = float(values["overtemp_start_degC"])
        if "overtemp_kp" in values:
            self._config.overtemp_kp = float(values["overtemp_kp"])

        if "min_power" in values:
            self._config.min_power = float(values["min_power"])
        if "max_power" in values:
            self._config.max_power = float(values["max_power"])

        if "ignition_high_power_percent" in values:
            self._config.ignition_high_power_percent = float(values["ignition_high_power_percent"])
        if "ignition_min_power_percent" in values:
            self._config.ignition_min_power_percent = float(values["ignition_min_power_percent"])
        if "ignition_full_power_delta_degC" in values:
            self._config.ignition_full_power_delta_degC = float(values["ignition_full_power_delta_degC"])
        if "ignition_min_power_delta_degC" in values:
            self._config.ignition_min_power_delta_degC = float(values["ignition_min_power_delta_degC"])

        if "ignition_target_rate_k_per_min" in values:
            self._config.ignition_target_rate_k_per_min = float(
                values["ignition_target_rate_k_per_min"]
            )
        if "ignition_rate_band_k_per_min" in values:
            self._config.ignition_rate_band_k_per_min = float(
                values["ignition_rate_band_k_per_min"]
            )
        if "ignition_rate_gain_percent_per_k_per_min" in values:
            self._config.ignition_rate_gain_percent_per_k_per_min = float(
                values["ignition_rate_gain_percent_per_k_per_min"]
            )
        if "ignition_rate_max_boost_percent" in values:
            self._config.ignition_rate_max_boost_percent = float(
                values["ignition_rate_max_boost_percent"]
            )

        if "mode" in values:
            self._config.mode = str(values["mode"])

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

        for field in (
            "boiler_set_temp",
            "kp",
            "ki",
            "kd",
            "integral_window_s",
            "overtemp_start_degC",
            "overtemp_kp",
            "min_power",
            "max_power",
            "ignition_high_power_percent",
            "ignition_min_power_percent",
            "ignition_full_power_delta_degC",
            "ignition_min_power_delta_degC",
            "ignition_target_rate_k_per_min",
            "ignition_rate_band_k_per_min",
            "ignition_rate_gain_percent_per_k_per_min",
            "ignition_rate_max_boost_percent",
        ):
            if field in data:
                setattr(self._config, field, float(data[field]))

        if "mode" in data:
            self._config.mode = str(data["mode"])

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
