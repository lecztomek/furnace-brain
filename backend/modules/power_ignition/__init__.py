from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # pip install pyyaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    BoilerMode,
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
    PartialOutputs,
)


# ---------- KONFIGURACJA RUNTIME ----------


@dataclass
class IgnitionPowerConfig:
    """
    Moduł mocy dla trybu IGNITION (rozpalanie).

    boiler_set_temp          – zadana temperatura kotła [°C]

    Ograniczenia:
      min_power, max_power   – ograniczenia mocy [%] (globalne dla tego modułu)
      max_slew_rate_percent_per_min
                             – maksymalna zmiana mocy [pkt%] na minutę
                               (np. 5.0 -> max ~5%/min, ok. 0.083%/s)

    IGNITION – hybryda dwóch algorytmów:

      1) Moc z odległości od zadanej (ΔT = T_set - T_boiler):
         ignition_high_power_percent      – moc przy dużym ΔT [%]
         ignition_min_power_percent       – najmniejsza moc w IGNITION [%]
         ignition_full_power_delta_degC   – od ilu °C poniżej zadanej
                                            ma być pełna moc (high_power)
         ignition_min_power_delta_degC    – do ilu °C poniżej zadanej
                                            schodzimy z mocą do min_power
                                            (poniżej tego progu trzymamy min_power)

      2) Moc z tempa wzrostu temperatury (dT/dt):
         ignition_target_rate_k_per_min   – docelowy przyrost T [°C/min]
         ignition_rate_band_k_per_min     – tolerancja wokół celu [°C/min];
                                            w paśmie moc z dT/dt jest
                                            liniowo pomiędzy high/min

      W trybie IGNITION liczymy:
        power_delta = f(ΔT)
        power_rate  = f(dT/dt)
        raw_power   = max(power_delta, power_rate)
        power_ign   = raw_power ograniczone:
                      - do [min_power, max_power]
                      - oraz tempem max_slew_rate_percent_per_min
    """

    boiler_set_temp: float = 54.0

    # ograniczenia dla mocy z tego modułu
    min_power: float = 10.0
    max_power: float = 100.0

    # limit szybkości zmian mocy
    max_slew_rate_percent_per_min: float = 5.0  # max zmiana 5 pkt% na minutę

    # część ΔT
    ignition_high_power_percent: float = 100.0      # pełna moc przy dużym ΔT
    ignition_min_power_percent: float = 30.0        # najmniejsza moc w IGNITION
    ignition_full_power_delta_degC: float = 15.0    # ΔT >= 15°C -> high_power
    ignition_min_power_delta_degC: float = 3.0      # ΔT <= 3°C  -> min_power

    # część dT/dt
    ignition_target_rate_k_per_min: float = 0.8     # docelowy przyrost [°C/min]
    ignition_rate_band_k_per_min: float = 0.3       # tolerancja wokół celu [°C/min]


class IgnitionPowerModule(ModuleInterface):
    """
    Moduł wyliczający "power" (moc kotła) w % w trybie IGNITION.

    - Działa TYLKO gdy SystemState.mode == BoilerMode.IGNITION.
    - W innych trybach NIE ustawia outputs.power_percent.

    Algorytm:
      - liczymy moc z ΔT (odległość od zadanej),
      - liczymy moc z dT/dt (tempo nagrzewania, wygładzone EMA),
      - bierzemy raw_power = max(power_delta, power_rate),
      - ograniczamy:
          * do [min_power, max_power]
          * tempem max_slew_rate_percent_per_min (max ~5 pkt%/min).
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[IgnitionPowerConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or IgnitionPowerConfig()
        self._load_config_from_file()

        self._power: float = 0.0
        self._last_mode_ignition: bool = False

        # stan dla dT/dt (CZAS MONOTONICZNY)
        self._ign_last_temp: Optional[float] = None
        self._ign_last_ts: Optional[float] = None
        self._ign_rate_ema: Optional[float] = None

        # stan dla limitu zmian mocy (CZAS MONOTONICZNY)
        self._last_power_ts: Optional[float] = None

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "power_ignition"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()

        # czas sterujący (odporny na DST/NTP); eventy/logi nadal na wall time (now)
        now_ctrl = system_state.ts_mono

        boiler_temp = sensors.boiler_temp
        mode_enum = system_state.mode
        in_ignition = (mode_enum == BoilerMode.IGNITION)

        prev_power = self._power
        prev_in_ignition = self._last_mode_ignition

        # Zdarzenia trybu
        if prev_in_ignition != in_ignition:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="IGNITION_POWER_MODE_CHANGED",
                    message=f"power_ignition: {'ENTER' if in_ignition else 'LEAVE'} IGNITION",
                    data={"in_ignition": in_ignition},
                )
            )

            # przy wejściu / wyjściu z IGNITION resetujemy stan dT/dt i limiter
            if in_ignition:
                self._ign_last_ts = None
                self._ign_last_temp = None
                self._ign_rate_ema = None
                self._last_power_ts = None

        if not in_ignition:
            # W innych trybach ten moduł NIC nie robi z power_percent.
            self._last_mode_ignition = in_ignition

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(
                partial_outputs=outputs,
                events=events,
                status=status,
            )

        # --- Tryb IGNITION – liczymy moc "surową" ---

        power_delta = self._ignition_power_from_delta(boiler_temp)
        power_rate = self._ignition_power_from_rate(now_ctrl, boiler_temp)

        raw_power = max(power_delta, power_rate)

        # globalne ograniczenia dla modułu
        raw_power = max(self._config.min_power, min(raw_power, self._config.max_power))

        # --- OGRANICZENIE SZYBKOŚCI ZMIAN MOCY (SLEW RATE) ---

        limited_power = raw_power

        if self._last_power_ts is not None and prev_in_ignition:
            dt = now_ctrl - self._last_power_ts
            if dt > 0:
                max_slew_per_min = max(self._config.max_slew_rate_percent_per_min, 0.0)
                max_delta = max_slew_per_min * dt / 60.0  # pkt% dozwolone w tym kroku

                delta = raw_power - prev_power
                if delta > max_delta:
                    limited_power = prev_power + max_delta
                elif delta < -max_delta:
                    limited_power = prev_power - max_delta
                else:
                    limited_power = raw_power
        else:
            # pierwszy krok po wejściu w IGNITION – bez limitu,
            # żeby kocioł mógł od razu wskoczyć na sensowną moc
            limited_power = raw_power

        # jeszcze raz upewniamy się, że w zakresie min/max
        limited_power = max(self._config.min_power, min(limited_power, self._config.max_power))

        self._power = limited_power
        self._last_power_ts = now_ctrl

        if abs(self._power - prev_power) >= 5.0:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="IGNITION_POWER_LEVEL_CHANGED",
                    message=(
                        f"power_ignition: {prev_power:.1f}% → {self._power:.1f}% "
                        f"(T_kotła={boiler_temp:.1f}°C, zadana={self._config.boiler_set_temp:.1f}°C)"
                        if boiler_temp is not None
                        else f"power_ignition: {prev_power:.1f}% → {self._power:.1f}% (brak T_kotła)"
                    ),
                    data={
                        "prev_power": prev_power,
                        "power": self._power,
                        "boiler_temp": boiler_temp,
                        "boiler_set_temp": self._config.boiler_set_temp,
                        "power_delta": power_delta,
                        "power_rate": power_rate,
                        "raw_power": raw_power,
                    },
                )
            )

        # ustawiamy wyjście TYLKO w IGNITION
        outputs.power_percent = self._power  # type: ignore[attr-defined]

        self._last_mode_ignition = in_ignition

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- LOGIKA POMOCNICZA ----------

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

    def _ignition_power_from_rate(self, now_ctrl: float, boiler_temp: Optional[float]) -> float:
        """
        Część dT/dt – osobna moc:

        - liczymy tempo nagrzewania [°C/min] z prostą EMA (wygładzenie),
        - jeśli rate <= (target - band)  -> za wolno, zwracamy high_p,
        - jeśli rate >= (target + band)  -> bardzo szybko, zwracamy min_ign,
        - w środku: płynna interpolacja high_p -> min_ign.

        Później bierzemy max(power_delta, power_rate), więc dT/dt nigdy
        nie obniża mocy poniżej tego, co wynika z ΔT.
        """

        high_p = self._config.ignition_high_power_percent
        min_ign = self._config.ignition_min_power_percent

        if boiler_temp is None:
            self._ign_last_ts = None
            self._ign_last_temp = None
            self._ign_rate_ema = None
            return 0.0  # brak sensownej informacji

        # brak historii -> inicjalizacja, jeszcze nie liczymy mocy z dT/dt
        if self._ign_last_ts is None or self._ign_last_temp is None:
            self._ign_last_ts = now_ctrl
            self._ign_last_temp = boiler_temp
            self._ign_rate_ema = None
            return 0.0

        dt = now_ctrl - self._ign_last_ts
        if dt <= 0:
            dt = 1.0  # awaryjnie

        inst_rate = (boiler_temp - self._ign_last_temp) / dt * 60.0  # °C/min

        self._ign_last_ts = now_ctrl
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

        low_rate = target - band
        high_rate = target + band

        # band == 0 – proste: poniżej target -> high, powyżej -> min
        if band <= 0.0:
            return high_p if rate <= target else min_ign

        if rate <= low_rate:
            # za wolno -> wysoka moc
            return high_p
        elif rate >= high_rate:
            # bardzo szybko -> minimalna moc (z punktu widzenia dT/dt)
            return min_ign
        else:
            # interpolacja liniowa:
            # rate = low_rate  -> high_p
            # rate = high_rate -> min_ign
            alpha = (high_rate - rate) / (high_rate - low_rate)  # 1..0
            power = min_ign + alpha * (high_p - min_ign)
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

        if "min_power" in values:
            self._config.min_power = float(values["min_power"])
        if "max_power" in values:
            self._config.max_power = float(values["max_power"])

        if "max_slew_rate_percent_per_min" in values:
            self._config.max_slew_rate_percent_per_min = float(
                values["max_slew_rate_percent_per_min"]
            )

        if "ignition_high_power_percent" in values:
            self._config.ignition_high_power_percent = float(values["ignition_high_power_percent"])
        if "ignition_min_power_percent" in values:
            self._config.ignition_min_power_percent = float(values["ignition_min_power_percent"])
        if "ignition_full_power_delta_degC" in values:
            self._config.ignition_full_power_delta_degC = float(
                values["ignition_full_power_delta_degC"]
            )
        if "ignition_min_power_delta_degC" in values:
            self._config.ignition_min_power_delta_degC = float(
                values["ignition_min_power_delta_degC"]
            )

        if "ignition_target_rate_k_per_min" in values:
            self._config.ignition_target_rate_k_per_min = float(
                values["ignition_target_rate_k_per_min"]
            )
        if "ignition_rate_band_k_per_min" in values:
            self._config.ignition_rate_band_k_per_min = float(
                values["ignition_rate_band_k_per_min"]
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
            "min_power",
            "max_power",
            "max_slew_rate_percent_per_min",
            "ignition_high_power_percent",
            "ignition_min_power_percent",
            "ignition_full_power_delta_degC",
            "ignition_min_power_delta_degC",
            "ignition_target_rate_k_per_min",
            "ignition_rate_band_k_per_min",
        ):
            if field in data:
                setattr(self._config, field, float(data[field]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

