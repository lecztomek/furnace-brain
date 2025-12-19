# backend/modules/stats/__init__.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import deque
from datetime import datetime

import yaml  # pip install pyyaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    ModuleStatus,
    Sensors,
    SystemState,
    PartialOutputs,
)

SECONDS_5M = 300.0
MJ_TO_KWH = 1.0 / 3.6  # 1 kWh = 3.6 MJ

# rolling windows (liczone z zamkniętych bucketów 5m)
BUCKETS_1H = 12                 # 12 * 5m
BUCKETS_4H = 48                 # 48 * 5m
BUCKETS_24H = 288               # 288 * 5m
BUCKETS_7D = 2016               # 2016 * 5m (7 dni)

# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class StatsConfig:
    enabled: bool = True
    feeder_kg_per_hour: float = 10.0
    calorific_mj_per_kg: float = 0.0


# ---------- STRUKTURY WEWNĘTRZNE ----------

@dataclass
class _Bucket:
    seconds: float = 0.0
    coal_kg: float = 0.0
    energy_kwh: float = 0.0


@dataclass
class _Agg:
    seconds: float
    coal_kg: float
    energy_kwh: float

    burn_kgph_avg: float
    burn_kgph_min: float
    burn_kgph_max: float

    power_kw_avg: float
    power_kw_min: float
    power_kw_max: float


# ---------- MODUŁ ----------

class StatsModule(ModuleInterface):
    """
    Moduł statystyk spalania i mocy.

    Opcja B (rolling):
    - bazą są ZAMKNIĘTE buckety 5m
    - okna 1h/4h/24h/7d liczone są jako agregacja ostatnich N bucketów 5m
      (rolling po czasie, niezależne od częstotliwości tick)
    - publikuje wyniki w system_state.runtime["stats"]
    """

    def __init__(self, base_path: Optional[Path] = None, config: Optional[StatsConfig] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or StatsConfig()
        self._load_config_from_file()

        # czas
        self._last_ts: Optional[float] = None

        # aktualny bucket 5m (niezamknięty)
        self._bucket_start_ts: Optional[float] = None
        self._cur = _Bucket()

        # historia ZAMKNIĘTYCH bucketów 5m (7 dni)
        self._b5m: deque[_Agg] = deque(maxlen=BUCKETS_7D)

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "stats"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        # init runtime slot (defensywnie)
        if not hasattr(system_state, "runtime"):
            system_state.runtime = {}

        if not self._config.enabled:
            self._publish(now, system_state, enabled=False)
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

        # pierwszy tick - inicjalizacja czasu
        if self._last_ts is None:
            self._last_ts = now
            self._bucket_start_ts = now
            self._cur = _Bucket()
            self._publish(now, system_state, enabled=True)
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

        dt_total = now - self._last_ts
        if dt_total <= 0:
            self._last_ts = now
            self._publish(now, system_state, enabled=True)
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

        feeder_on = bool(system_state.outputs.feeder_on)
        t = self._last_ts

        # integracja po czasie z podziałem na granice bucketów 5m
        while t < now:
            if self._bucket_start_ts is None:
                self._bucket_start_ts = t

            bucket_end = self._bucket_start_ts + SECONDS_5M
            step = min(now - t, bucket_end - t)

            self._cur.seconds += step

            if feeder_on and self._config.feeder_kg_per_hour > 0:
                kg = self._config.feeder_kg_per_hour * (step / 3600.0)
                self._cur.coal_kg += kg

                if self._config.calorific_mj_per_kg > 0:
                    kwh_per_kg = self._config.calorific_mj_per_kg * MJ_TO_KWH
                    self._cur.energy_kwh += kg * kwh_per_kg

            t += step

            # domykamy bucket 5m
            if t >= bucket_end - 1e-9:
                self._finalize_5m_bucket()
                self._bucket_start_ts = bucket_end
                self._cur = _Bucket()

        self._last_ts = now
        self._publish(now, system_state, enabled=True)

        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

    # ---------- LICZENIE / AGREGACJE ----------

    def _rate_kgph(self, seconds: float, coal_kg: float) -> float:
        if seconds <= 0:
            return 0.0
        return (coal_kg * 3600.0) / seconds

    def _rate_kw(self, seconds: float, energy_kwh: float) -> float:
        if seconds <= 0:
            return 0.0
        return (energy_kwh * 3600.0) / seconds

    def _finalize_5m_bucket(self) -> None:
        s = self._cur.seconds
        kg = self._cur.coal_kg
        en = self._cur.energy_kwh

        burn = self._rate_kgph(s, kg)
        power = self._rate_kw(s, en)

        a5 = _Agg(
            seconds=s,
            coal_kg=kg,
            energy_kwh=en,
            burn_kgph_avg=burn,
            burn_kgph_min=burn,
            burn_kgph_max=burn,
            power_kw_avg=power,
            power_kw_min=power,
            power_kw_max=power,
        )
        self._b5m.append(a5)

    def _aggregate_from_children(self, children: List[_Agg]) -> _Agg:
        sec = sum(c.seconds for c in children)
        kg = sum(c.coal_kg for c in children)
        en = sum(c.energy_kwh for c in children)

        burn_avg = self._rate_kgph(sec, kg)
        power_avg = self._rate_kw(sec, en)

        burn_min = min((c.burn_kgph_min for c in children), default=0.0)
        burn_max = max((c.burn_kgph_max for c in children), default=0.0)

        power_min = min((c.power_kw_min for c in children), default=0.0)
        power_max = max((c.power_kw_max for c in children), default=0.0)

        return _Agg(
            seconds=sec,
            coal_kg=kg,
            energy_kwh=en,
            burn_kgph_avg=burn_avg,
            burn_kgph_min=burn_min,
            burn_kgph_max=burn_max,
            power_kw_avg=power_avg,
            power_kw_min=power_min,
            power_kw_max=power_max,
        )

    def _window_from_5m(self, n: int) -> Optional[_Agg]:
        if len(self._b5m) < n:
            return None
        return self._aggregate_from_children(list(self._b5m)[-n:])

    # ---------- PUBLIKACJA DO runtime ----------

    def _publish(self, now: float, system_state: SystemState, enabled: bool) -> None:
        ts_iso = datetime.fromtimestamp(now).isoformat(timespec="seconds")

        # 5m: ostatni zamknięty bucket, a jeśli go brak, to aktualny (częściowy)
        a5: Optional[_Agg] = self._b5m[-1] if len(self._b5m) >= 1 else None
        if a5 is None and self._cur.seconds > 0:
            burn = self._rate_kgph(self._cur.seconds, self._cur.coal_kg)
            power = self._rate_kw(self._cur.seconds, self._cur.energy_kwh)
            a5 = _Agg(
                seconds=self._cur.seconds,
                coal_kg=self._cur.coal_kg,
                energy_kwh=self._cur.energy_kwh,
                burn_kgph_avg=burn, burn_kgph_min=burn, burn_kgph_max=burn,
                power_kw_avg=power, power_kw_min=power, power_kw_max=power,
            )

        a1 = self._window_from_5m(BUCKETS_1H)
        a4 = self._window_from_5m(BUCKETS_4H)
        a24 = self._window_from_5m(BUCKETS_24H)
        a7 = self._window_from_5m(BUCKETS_7D)

        def pack(prefix: str, a: Optional[_Agg], out: Dict[str, Any]) -> None:
            if a is None:
                out[f"burn_kgph_{prefix}"] = None
                out[f"burn_kgph_min_{prefix}"] = None
                out[f"burn_kgph_max_{prefix}"] = None
                out[f"coal_kg_{prefix}"] = None
                out[f"power_kw_{prefix}"] = None
                out[f"power_kw_min_{prefix}"] = None
                out[f"power_kw_max_{prefix}"] = None
                out[f"energy_kwh_{prefix}"] = None
                out[f"seconds_{prefix}"] = None
                return

            out[f"burn_kgph_{prefix}"] = a.burn_kgph_avg
            out[f"burn_kgph_min_{prefix}"] = a.burn_kgph_min
            out[f"burn_kgph_max_{prefix}"] = a.burn_kgph_max

            out[f"coal_kg_{prefix}"] = a.coal_kg

            out[f"power_kw_{prefix}"] = a.power_kw_avg
            out[f"power_kw_min_{prefix}"] = a.power_kw_min
            out[f"power_kw_max_{prefix}"] = a.power_kw_max

            out[f"energy_kwh_{prefix}"] = a.energy_kwh
            out[f"seconds_{prefix}"] = a.seconds

        payload: Dict[str, Any] = {
            "enabled": bool(enabled),
            "ts_unix": float(now),
            "ts_iso": ts_iso,
            "feeder_kg_per_hour": float(self._config.feeder_kg_per_hour),
            "calorific_mj_per_kg": float(self._config.calorific_mj_per_kg),
        }

        pack("5m", a5, payload)
        pack("1h", a1, payload)
        pack("4h", a4, payload)
        pack("24h", a24, payload)
        pack("7d", a7, payload)

        system_state.runtime["stats"] = payload

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
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(self._config), f, sort_keys=True, allow_unicode=True)
