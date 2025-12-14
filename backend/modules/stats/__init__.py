from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from collections import deque

import yaml  # pip install pyyaml

from backend.core.kernel import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    EventLevel,
    ModuleStatus,
    Sensors,
    SystemState,
    PartialOutputs,
)


# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class StatsConfig:
    enabled: bool = True

    # Ile kg węgla podaje ślimak w 1h, gdy feeder_on=True przez całą godzinę.
    feeder_kg_per_hour: float = 10.0

    # Kaloryczność paliwa [MJ/kg]. Jeśli 0 -> moc = None.
    calorific_mj_per_kg: float = 0.0


# ---------- MODUŁ ----------

class StatsModule(ModuleInterface):
    """
    stats:
    - bazuje na system_state.outputs.feeder_on (czyli w praktyce na pracy modułu ślimaka)
    - integruje masę paliwa w czasie i zapisuje do bucketów:
        5-min -> 1h -> 24h -> 7d
    - trzyma stałe ring-buffery, bez wycieku pamięci.

    Statystyki:
      spalanie (kg/h) i moc (kW) dla:
        - 5m  (ostatni zakończony bucket 5-min)
        - 1h  (ostatnia zakończona godzina)
        - 4h  (ostatnie 4 zakończone godziny)
        - 24h (ostatnia zakończona doba)
        - 7d  (ostatnie 7 zakończonych dób)

    Uwaga: okna są "bucketowe" (wyrównane do czasu epoki),
    a nie idealnie kroczące — zgodnie z Twoim wymaganiem hierarchii i mniejszej ilości danych.
    """

    # stałe rozmiary bucketów
    BUCKET_5M_S = 300
    BUCKET_1H_S = 3600
    BUCKET_24H_S = 86400

    def __init__(self, base_path: Path | None = None, config: StatsConfig | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or StatsConfig()
        self._load_config_from_file()

        # ring buffers: (bucket_start_ts, mass_kg, energy_kwh)
        # 5m: trzymamy ~2h, żeby pewnie składać godziny nawet przy opóźnieniach ticków
        self._b5: Deque[Tuple[int, float, float]] = deque(maxlen=24)     # 24 * 5m = 2h
        self._b1h: Deque[Tuple[int, float, float]] = deque(maxlen=30)    # ~30h
        self._b24h: Deque[Tuple[int, float, float]] = deque(maxlen=8)    # ~8 dni

        self._last_ts: Optional[float] = None

        # żeby nie tworzyć duplikatów bucketów
        self._last_closed_5m_start: Optional[int] = None
        self._last_closed_1h_start: Optional[int] = None
        self._last_closed_24h_start: Optional[int] = None

        # cache pod GUI/API
        self._last_stats: Dict[str, Any] = {}

        # opcjonalnie: eventy o problemach konfiga (rate limit)
        self._bad_cfg_last_event_ts: float = 0.0

    @property
    def id(self) -> str:
        return "stats"

    def get_runtime_stats(self) -> Dict[str, Any]:
        return dict(self._last_stats)

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        if not self._config.enabled:
            self._last_ts = now
            self._b5.clear()
            self._b1h.clear()
            self._b24h.clear()
            self._last_stats = {"enabled": False}
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # podstawowe sanity-check configu
        if self._config.feeder_kg_per_hour < 0:
            self._config.feeder_kg_per_hour = 0.0
            if now - self._bad_cfg_last_event_ts >= 60.0:
                self._bad_cfg_last_event_ts = now
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.WARNING,
                        type="STATS_BAD_CONFIG",
                        message="feeder_kg_per_hour < 0; skorygowano do 0.",
                        data={},
                    )
                )

        if self._last_ts is None:
            self._last_ts = now
            self._recompute_stats_cache(now)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        dt = now - self._last_ts
        if dt <= 0:
            self._recompute_stats_cache(now)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        feeder_on = bool(system_state.outputs.feeder_on)

        kg_per_h = float(self._config.feeder_kg_per_hour)
        kg_per_s = kg_per_h / 3600.0

        mj_per_kg = max(0.0, float(self._config.calorific_mj_per_kg))
        kwh_per_kg = (mj_per_kg / 3.6) if mj_per_kg > 0 else 0.0

        mass_kg = (kg_per_s * dt) if feeder_on else 0.0
        energy_kwh = (mass_kg * kwh_per_kg) if kwh_per_kg > 0 else 0.0

        # rozkładamy integral na buckety 5-min
        self._accumulate_into_buckets(
            t0=self._last_ts,
            t1=now,
            bucket_s=self.BUCKET_5M_S,
            total_mass=mass_kg,
            total_energy=energy_kwh,
            target=self._b5,
        )

        # domykamy i roll-upujemy (5m -> 1h -> 24h)
        self._rollup(now)

        self._last_ts = now

        # cache statystyk
        self._recompute_stats_cache(now)
        system_state.runtime[self.id] = dict(self._last_stats)

        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # ---------- BUCKETING + ROLLUP ----------

    def _accumulate_into_buckets(
        self,
        t0: float,
        t1: float,
        bucket_s: int,
        total_mass: float,
        total_energy: float,
        target: Deque[Tuple[int, float, float]],
    ) -> None:
        """
        Rozkłada (total_mass, total_energy) proporcjonalnie po bucketach o długości bucket_s
        pomiędzy t0..t1. Wpisy do target są sumowane po bucket_start.
        """
        total_dt = t1 - t0
        if total_dt <= 0:
            return

        cur = t0
        while cur < t1:
            b_start = int(cur // bucket_s) * bucket_s
            b_end = b_start + bucket_s
            seg_end = min(t1, b_end)
            seg_dt = seg_end - cur

            frac = seg_dt / total_dt
            seg_mass = total_mass * frac
            seg_energy = total_energy * frac

            self._add_or_sum(target, b_start, seg_mass, seg_energy)
            cur = seg_end

    def _add_or_sum(self, target: Deque[Tuple[int, float, float]], b_start: int, mass: float, energy: float) -> None:
        if not target:
            target.append((b_start, mass, energy))
            return

        last_start, last_m, last_e = target[-1]
        if last_start == b_start:
            target[-1] = (last_start, last_m + mass, last_e + energy)
            return

        # jeśli b_start jest "w środku" (rzadkie przy normalnym ticku), dopisz nowy
        target.append((b_start, mass, energy))

    def _rollup(self, now: float) -> None:
        """
        - tworzy godzinne buckety z 12 szt. 5-min, ale tylko gdy domknęliśmy godzinę,
        - tworzy dobowe buckety z 24 szt. 1h, ale tylko gdy domknęliśmy dobę.
        """
        # 1) roll-up 5m -> 1h
        # sprawdzamy ostatni DOMKNIĘTY 5m bucket (tzn. jego start < aktualny start)
        current_5m_start = int(now // self.BUCKET_5M_S) * self.BUCKET_5M_S
        last_closed_5m_start = current_5m_start - self.BUCKET_5M_S

        if self._last_closed_5m_start is None or last_closed_5m_start > self._last_closed_5m_start:
            self._last_closed_5m_start = last_closed_5m_start

            # jeśli domknięty 5m bucket kończy godzinę (np. 12-ty w godzinie),
            # to (last_closed_5m_start + 300) % 3600 == 0
            if ((last_closed_5m_start + self.BUCKET_5M_S) % self.BUCKET_1H_S) == 0:
                hour_end = last_closed_5m_start + self.BUCKET_5M_S
                hour_start = hour_end - self.BUCKET_1H_S
                hour = self._sum_exact_sequence(self._b5, hour_start, self.BUCKET_5M_S, 12)
                if hour is not None:
                    mass, energy = hour
                    # unikaj duplikatów
                    if self._last_closed_1h_start is None or hour_start > self._last_closed_1h_start:
                        self._last_closed_1h_start = hour_start
                        self._add_or_sum(self._b1h, hour_start, mass, energy)

                        # 2) roll-up 1h -> 24h (doba domknięta?)
                        # jeśli hour_end kończy dobę: hour_end % 86400 == 0
                        if (hour_end % self.BUCKET_24H_S) == 0:
                            day_end = hour_end
                            day_start = day_end - self.BUCKET_24H_S
                            day = self._sum_exact_sequence(self._b1h, day_start, self.BUCKET_1H_S, 24)
                            if day is not None:
                                d_mass, d_energy = day
                                if self._last_closed_24h_start is None or day_start > self._last_closed_24h_start:
                                    self._last_closed_24h_start = day_start
                                    self._add_or_sum(self._b24h, day_start, d_mass, d_energy)

    def _sum_exact_sequence(
        self,
        source: Deque[Tuple[int, float, float]],
        start_ts: int,
        step_s: int,
        count: int,
    ) -> Optional[Tuple[float, float]]:
        """
        Sumuje dokładnie sekwencję bucketów: start_ts, start_ts+step, ... (count szt)
        Zwraca None jeśli brakuje któregokolwiek elementu.
        """
        # mało danych -> prosta mapa w locie
        mp: Dict[int, Tuple[float, float]] = {ts: (m, e) for ts, m, e in source}
        mass = 0.0
        energy = 0.0
        for i in range(count):
            ts = start_ts + i * step_s
            if ts not in mp:
                return None
            m, e = mp[ts]
            mass += m
            energy += e
        return mass, energy

    # ---------- STATYSTYKI (cache) ----------

    def _latest_bucket(self, source: Deque[Tuple[int, float, float]]) -> Optional[Tuple[int, float, float]]:
        if not source:
            return None
        return source[-1]

    def _sum_last_n(self, source: Deque[Tuple[int, float, float]], n: int) -> Optional[Tuple[float, float]]:
        if len(source) < n:
            return None
        mass = 0.0
        energy = 0.0
        for ts, m, e in list(source)[-n:]:
            mass += m
            energy += e
        return mass, energy

    def _recompute_stats_cache(self, now: float) -> None:
        mj_per_kg = max(0.0, float(self._config.calorific_mj_per_kg))

        def to_kw(energy_kwh: float, hours: float) -> Optional[float]:
            if mj_per_kg <= 0:
                return None
            if hours <= 0:
                return None
            return energy_kwh / hours

        # --- 5m ---
        b5 = self._latest_bucket(self._b5)
        if b5 is None:
            mass_5m = None
            energy_5m = None
        else:
            _, mass_5m, energy_5m = b5

        # --- 1h (ostatnia pełna godzina) ---
        b1h = self._latest_bucket(self._b1h)
        if b1h is None:
            mass_1h = None
            energy_1h = None
        else:
            _, mass_1h, energy_1h = b1h

        # --- 4h (4 pełne godziny) ---
        sum4 = self._sum_last_n(self._b1h, 4)
        if sum4 is None:
            mass_4h = None
            energy_4h = None
        else:
            mass_4h, energy_4h = sum4

        # --- 24h (ostatnia pełna doba) ---
        b24 = self._latest_bucket(self._b24h)
        if b24 is None:
            mass_24h = None
            energy_24h = None
        else:
            _, mass_24h, energy_24h = b24

        # --- 7d (7 pełnych dób) ---
        sum7 = self._sum_last_n(self._b24h, 7)
        if sum7 is None:
            mass_7d = None
            energy_7d = None
        else:
            mass_7d, energy_7d = sum7

        # spalanie (kg/h) i moc (kW)
        def rate_kgph(mass: Optional[float], hours: float) -> Optional[float]:
            if mass is None:
                return None
            if hours <= 0:
                return None
            return mass / hours

        self._last_stats = {
            "enabled": True,
            "feeder_kg_per_hour": float(self._config.feeder_kg_per_hour),
            "calorific_mj_per_kg": float(self._config.calorific_mj_per_kg),

            "burn_kgph_5m": rate_kgph(mass_5m, 5.0 / 60.0),
            "burn_kgph_1h": rate_kgph(mass_1h, 1.0),
            "burn_kgph_4h": rate_kgph(mass_4h, 4.0),
            "burn_kgph_24h": rate_kgph(mass_24h, 24.0),
            "burn_kgph_7d": rate_kgph(mass_7d, 7.0 * 24.0),

            "power_kw_5m": to_kw(energy_5m, 5.0 / 60.0) if energy_5m is not None else None,
            "power_kw_1h": to_kw(energy_1h, 1.0) if energy_1h is not None else None,
            "power_kw_4h": to_kw(energy_4h, 4.0) if energy_4h is not None else None,
            "power_kw_24h": to_kw(energy_24h, 24.0) if energy_24h is not None else None,
            "power_kw_7d": to_kw(energy_7d, 7.0 * 24.0) if energy_7d is not None else None,

            # dodatkowo: sumy masy/energii (mogą się przydać w GUI)
            "coal_kg_5m": mass_5m,
            "coal_kg_1h": mass_1h,
            "coal_kg_4h": mass_4h,
            "coal_kg_24h": mass_24h,
            "coal_kg_7d": mass_7d,

            "energy_kwh_5m": energy_5m,
            "energy_kwh_1h": energy_1h,
            "energy_kwh_4h": energy_4h,
            "energy_kwh_24h": energy_24h,
            "energy_kwh_7d": energy_7d,

            # info o dostępności okien (żeby GUI wiedziało czy już "nabiło")
            "available_5m": mass_5m is not None,
            "available_1h": mass_1h is not None,
            "available_4h": mass_4h is not None,
            "available_24h": mass_24h is not None,
            "available_7d": mass_7d is not None,
        }

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "enabled" in values:
            self._config.enabled = bool(values["enabled"])
        if "feeder_kg_per_hour" in values:
            self._config.feeder_kg_per_hour = float(values["feeder_kg_per_hour"])
        if "calorific_mj_per_kg" in values:
            self._config.calorific_mj_per_kg = float(values["calorific_mj_per_kg"])

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "enabled" in data:
            self._config.enabled = bool(data["enabled"])
        if "feeder_kg_per_hour" in data:
            self._config.feeder_kg_per_hour = float(data["feeder_kg_per_hour"])
        if "calorific_mj_per_kg" in data:
            self._config.calorific_mj_per_kg = float(data["calorific_mj_per_kg"])

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
