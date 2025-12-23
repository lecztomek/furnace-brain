from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterable
from collections import deque
from datetime import datetime, date, timedelta
import csv

import yaml  # pip install pyyaml
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python 3.7/3.8

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    EventLevel,
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
    # dotychczasowe
    enabled: bool = True
    feeder_kg_per_hour: float = 10.0
    calorific_mj_per_kg: float = 0.0

    # persistence / cache (wariant B)
    log_dir: str = "data"
    file_prefix_5m: str = "stats5m"      # pliki godzinowe: <prefix>_YYYYMMDD_HH.csv
    daily_file: str = "stats_daily.csv"  # 1 plik dzienny (cache)
    state_file: str = "stats_state.yaml" # stan bieżącego dnia

    timezone: str = "Europe/Warsaw"
    season_start_month: int = 9
    season_start_day: int = 1

    bars_days: int = 30  # ile słupków dziennych wysyłać do UI

    # porównania (Twoja semantyka: okno o stałej długości, przesunięte wstecz)
    publish_compare_bars: bool = True


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


@dataclass
class _Agg5mTimed:
    ts_end_unix: float
    ts_end_iso: str
    agg: _Agg


@dataclass
class _DayAcc:
    # sumy
    seconds_sum: float = 0.0
    coal_kg_sum: float = 0.0
    energy_kwh_sum: float = 0.0

    # aktywność (tylko gdy coal_kg_5m > 0)
    active_seconds: float = 0.0

    # peak 5m
    burn_kgph_max_5m: float = 0.0
    power_kw_max_5m: float = 0.0
    coal_kg_max_5m: float = 0.0

    # min "aktywny" 5m (None jeśli brak aktywnych bucketów)
    burn_kgph_min_active_5m: Optional[float] = None
    power_kw_min_active_5m: Optional[float] = None

    # wewnętrzne
    _has_active: bool = False


@dataclass
class _DayRecord:
    date_str: str  # YYYY-MM-DD (lokalnie)
    seconds_sum: float
    coal_kg_sum: float
    energy_kwh_sum: float

    burn_kgph_avg: float
    power_kw_avg: float

    active_seconds: float
    active_ratio: float

    burn_kgph_max_5m: float
    burn_kgph_min_active_5m: Optional[float]

    power_kw_max_5m: float
    power_kw_min_active_5m: Optional[float]

    coal_kg_max_5m: float


# ---------- MODUŁ ----------

class StatsModule(ModuleInterface):
    """
    Stats + cache dzienny (wariant B) + porównawcze słupki "sprzed X przez stały czas".

    - baza: ZAMKNIĘTE buckety 5m (monotonic) + timestamp końca (wall-clock)
    - rolling 1h/4h/24h/7d z ostatnich N bucketów 5m
    - persist:
      * buckety 5m -> CSV (1 plik na godzinę)
      * agregaty dzienne -> stats_daily.csv (1 plik)
      * stan bieżącego dnia -> stats_state.yaml (żeby "dziś" nie zerowało się po restarcie)

    Czas:
    - integracja i granice bucketów: system_state.ts_mono (monotonic)
    - ts_end_unix (wall) wyliczamy z kotwicy (now + now_mono)
    - porównania robimy na ts_end_unix i wyrównujemy do siatki 5 minut
    """

    def __init__(self, base_path: Optional[Path] = None, config: Optional[StatsConfig] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or StatsConfig()
        self._load_config_from_file()

        self._tz = ZoneInfo(self._config.timezone)

        self._log_dir = (self._base_path / self._config.log_dir).resolve()
        self._log_dir.mkdir(parents=True, exist_ok=True)

        self._daily_path = self._log_dir / self._config.daily_file
        self._state_path = self._log_dir / self._config.state_file

        # czas (MONOTONICZNY)
        self._last_ts_mono: Optional[float] = None

        # aktualny bucket 5m (niezamknięty) (MONOTONICZNY start)
        self._bucket_start_mono: Optional[float] = None
        self._cur = _Bucket()

        # historia ZAMKNIĘTYCH bucketów 5m (7 dni) + timestamp wall
        self._b5m: deque[_Agg5mTimed] = deque(maxlen=BUCKETS_7D)

        # cache dzienny w pamięci
        self._daily: Dict[str, _DayRecord] = {}  # date_str -> record

        # stan bieżącego dnia (w trakcie)
        self._day_key: Optional[str] = None
        self._day_acc = _DayAcc()

        # bootstrap z dysku
        self._bootstrap_from_disk()

    @property
    def id(self) -> str:
        return "stats"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        now_mono: float = system_state.ts_mono  # bez fallbacków

        if not self._config.enabled:
            self._publish(now, system_state, enabled=False)
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

        # pierwszy tick - inicjalizacja czasu (monotonic)
        if self._last_ts_mono is None:
            self._last_ts_mono = now_mono
            self._bucket_start_mono = now_mono
            self._cur = _Bucket()
            self._publish(now, system_state, enabled=True)
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

        dt_total = now_mono - self._last_ts_mono
        if dt_total <= 0:
            self._last_ts_mono = now_mono
            self._publish(now, system_state, enabled=True)
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status)

        feeder_on = bool(system_state.outputs.feeder_on)

        # kotwica do przeliczenia bucket_end_mono -> wall-clock
        anchor_wall = float(now)
        anchor_mono = float(now_mono)

        t = float(self._last_ts_mono)

        # integracja po czasie z podziałem na granice bucketów 5m (monotonic)
        while t < now_mono:
            if self._bucket_start_mono is None:
                self._bucket_start_mono = t

            bucket_end_mono = self._bucket_start_mono + SECONDS_5M
            step = min(now_mono - t, bucket_end_mono - t)

            self._cur.seconds += step

            if feeder_on and self._config.feeder_kg_per_hour > 0:
                kg = self._config.feeder_kg_per_hour * (step / 3600.0)
                self._cur.coal_kg += kg

                if self._config.calorific_mj_per_kg > 0:
                    kwh_per_kg = self._config.calorific_mj_per_kg * MJ_TO_KWH
                    self._cur.energy_kwh += kg * kwh_per_kg

            t += step

            # domykamy bucket 5m
            if t >= bucket_end_mono - 1e-9:
                try:
                    self._finalize_5m_bucket(
                        bucket_end_mono=bucket_end_mono,
                        anchor_wall=anchor_wall,
                        anchor_mono=anchor_mono,
                    )
                except Exception as exc:
                    events.append(
                        Event(
                            ts=now,
                            source=self.id,
                            level=EventLevel.ERROR,
                            type="STATS_PERSIST_ERROR",
                            message=f"Błąd persist stats (5m/daily/state): {exc}",
                            data={"exception": repr(exc)},
                        )
                    )

                self._bucket_start_mono = bucket_end_mono
                self._cur = _Bucket()

        self._last_ts_mono = now_mono
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

    def _mono_to_wall(self, ts_mono: float, anchor_wall: float, anchor_mono: float) -> float:
        # wall(ts) ~= anchor_wall - (anchor_mono - ts_mono)
        return anchor_wall - (anchor_mono - ts_mono)

    @staticmethod
    def _floor_to_5m(ts_unix: float) -> float:
        # wyrównanie do siatki 5 minut (300s)
        return ts_unix - (ts_unix % SECONDS_5M)

    def _finalize_5m_bucket(self, bucket_end_mono: float, anchor_wall: float, anchor_mono: float) -> None:
        s = float(self._cur.seconds)
        kg = float(self._cur.coal_kg)
        en = float(self._cur.energy_kwh)

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

        # czas wall-clock końca bucketa (do dni/miesięcy/porównań)
        bucket_end_wall = float(self._mono_to_wall(bucket_end_mono, anchor_wall, anchor_mono))
        bucket_end_dt = datetime.fromtimestamp(bucket_end_wall, tz=self._tz)
        ts_end_iso = bucket_end_dt.isoformat(timespec="seconds")
        ts_end_unix = bucket_end_dt.timestamp()

        timed = _Agg5mTimed(ts_end_unix=ts_end_unix, ts_end_iso=ts_end_iso, agg=a5)
        self._b5m.append(timed)

        # day_key lokalny
        day_key = bucket_end_dt.date().isoformat()

        # 1) log 5m
        self._append_5m_row(bucket_end_dt=bucket_end_dt, timed=timed)

        # 2) cache dzienny (wariant B)
        self._day_add_5m(day_key=day_key, timed=timed)

        # 3) state bieżącego dnia
        self._save_state()

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
        last = list(self._b5m)[-n:]
        return self._aggregate_from_children([x.agg for x in last])

    # ---------- PERSIST: 5m CSV (1 plik na godzinę) ----------

    def _append_5m_row(self, bucket_end_dt: datetime, timed: _Agg5mTimed) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{self._config.file_prefix_5m}_{bucket_end_dt.strftime('%Y%m%d_%H')}.csv"
        file_path = self._log_dir / filename
        new_file = not file_path.exists()

        a = timed.agg
        with file_path.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            if new_file:
                w.writerow(
                    [
                        "ts_end_iso",
                        "ts_end_unix",
                        "seconds",
                        "coal_kg",
                        "energy_kwh",
                        "burn_kgph",
                        "power_kw",
                    ]
                )
            w.writerow(
                [
                    timed.ts_end_iso,
                    f"{timed.ts_end_unix:.3f}",
                    f"{a.seconds:.6f}",
                    f"{a.coal_kg:.6f}",
                    f"{a.energy_kwh:.6f}",
                    f"{a.burn_kgph_avg:.6f}",
                    f"{a.power_kw_avg:.6f}",
                ]
            )

    # ---------- CACHE DZIENNY (wariant B) ----------

    def _day_add_5m(self, day_key: str, timed: _Agg5mTimed) -> None:
        a = timed.agg

        if self._day_key is None:
            self._day_key = day_key
            self._day_acc = _DayAcc()

        if day_key != self._day_key:
            self._flush_day_to_daily_csv(self._day_key, self._day_acc)
            self._day_key = day_key
            self._day_acc = _DayAcc()

        acc = self._day_acc

        acc.seconds_sum += float(a.seconds)
        acc.coal_kg_sum += float(a.coal_kg)
        acc.energy_kwh_sum += float(a.energy_kwh)

        burn_5m = float(a.burn_kgph_avg)
        power_5m = float(a.power_kw_avg)
        coal_5m = float(a.coal_kg)

        # maxy 5m (zawsze sensowne)
        acc.burn_kgph_max_5m = max(acc.burn_kgph_max_5m, burn_5m)
        acc.power_kw_max_5m = max(acc.power_kw_max_5m, power_5m)
        acc.coal_kg_max_5m = max(acc.coal_kg_max_5m, coal_5m)

        # min tylko "aktywny" (gdy było realne spalanie)
        if coal_5m > 0:
            acc.active_seconds += float(a.seconds)
            if not acc._has_active:
                acc._has_active = True
                acc.burn_kgph_min_active_5m = burn_5m
                acc.power_kw_min_active_5m = power_5m
            else:
                acc.burn_kgph_min_active_5m = min(acc.burn_kgph_min_active_5m or burn_5m, burn_5m)
                acc.power_kw_min_active_5m = min(acc.power_kw_min_active_5m or power_5m, power_5m)

    def _flush_day_to_daily_csv(self, day_key: str, acc: _DayAcc) -> None:
        burn_avg = self._rate_kgph(acc.seconds_sum, acc.coal_kg_sum)
        power_avg = self._rate_kw(acc.seconds_sum, acc.energy_kwh_sum)
        active_ratio = (acc.active_seconds / acc.seconds_sum) if acc.seconds_sum > 0 else 0.0

        rec = _DayRecord(
            date_str=day_key,
            seconds_sum=float(acc.seconds_sum),
            coal_kg_sum=float(acc.coal_kg_sum),
            energy_kwh_sum=float(acc.energy_kwh_sum),
            burn_kgph_avg=float(burn_avg),
            power_kw_avg=float(power_avg),
            active_seconds=float(acc.active_seconds),
            active_ratio=float(active_ratio),
            burn_kgph_max_5m=float(acc.burn_kgph_max_5m),
            burn_kgph_min_active_5m=acc.burn_kgph_min_active_5m if acc._has_active else None,
            power_kw_max_5m=float(acc.power_kw_max_5m),
            power_kw_min_active_5m=acc.power_kw_min_active_5m if acc._has_active else None,
            coal_kg_max_5m=float(acc.coal_kg_max_5m),
        )

        self._daily[day_key] = rec

        # upsert po dacie: jeśli data już istnieje -> rewrite, inaczej append
        if self._daily_path.exists() and self._daily_has_date(day_key):
            self._daily_rewrite_file()
        else:
            self._daily_append_row(rec)

    def _daily_has_date(self, day_key: str) -> bool:
        try:
            with self._daily_path.open("r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f, delimiter=";")
                for row in r:
                    if (row.get("date") or "").strip() == day_key:
                        return True
        except Exception:
            return False
        return False

    def _daily_append_row(self, rec: _DayRecord) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        new_file = not self._daily_path.exists()

        with self._daily_path.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            if new_file:
                w.writerow(self._daily_header())
            w.writerow(self._daily_row(rec))

    def _daily_rewrite_file(self) -> None:
        tmp = self._daily_path.with_suffix(".tmp")
        keys = sorted(self._daily.keys())

        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(self._daily_header())
            for k in keys:
                w.writerow(self._daily_row(self._daily[k]))

        tmp.replace(self._daily_path)

    @staticmethod
    def _daily_header() -> List[str]:
        return [
            "date",
            "seconds_sum",
            "coal_kg_sum",
            "energy_kwh_sum",
            "burn_kgph_avg",
            "power_kw_avg",
            "active_seconds",
            "active_ratio",
            "burn_kgph_max_5m",
            "burn_kgph_min_active_5m",
            "power_kw_max_5m",
            "power_kw_min_active_5m",
            "coal_kg_max_5m",
        ]

    @staticmethod
    def _daily_row(rec: _DayRecord) -> List[str]:
        return [
            rec.date_str,
            f"{rec.seconds_sum:.6f}",
            f"{rec.coal_kg_sum:.6f}",
            f"{rec.energy_kwh_sum:.6f}",
            f"{rec.burn_kgph_avg:.6f}",
            f"{rec.power_kw_avg:.6f}",
            f"{rec.active_seconds:.6f}",
            f"{rec.active_ratio:.6f}",
            f"{rec.burn_kgph_max_5m:.6f}",
            "" if rec.burn_kgph_min_active_5m is None else f"{rec.burn_kgph_min_active_5m:.6f}",
            f"{rec.power_kw_max_5m:.6f}",
            "" if rec.power_kw_min_active_5m is None else f"{rec.power_kw_min_active_5m:.6f}",
            f"{rec.coal_kg_max_5m:.6f}",
        ]

    # ---------- STATE (bieżący dzień) ----------

    def _save_state(self) -> None:
        data: Dict[str, Any] = {
            "day_key": self._day_key,
            "day_acc": {
                "seconds_sum": self._day_acc.seconds_sum,
                "coal_kg_sum": self._day_acc.coal_kg_sum,
                "energy_kwh_sum": self._day_acc.energy_kwh_sum,
                "active_seconds": self._day_acc.active_seconds,
                "burn_kgph_max_5m": self._day_acc.burn_kgph_max_5m,
                "power_kw_max_5m": self._day_acc.power_kw_max_5m,
                "coal_kg_max_5m": self._day_acc.coal_kg_max_5m,
                "burn_kgph_min_active_5m": self._day_acc.burn_kgph_min_active_5m,
                "power_kw_min_active_5m": self._day_acc.power_kw_min_active_5m,
                "_has_active": self._day_acc._has_active,
            },
        }
        with self._state_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        with self._state_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        self._day_key = data.get("day_key") or None
        acc = data.get("day_acc") or {}

        self._day_acc = _DayAcc(
            seconds_sum=float(acc.get("seconds_sum", 0.0)),
            coal_kg_sum=float(acc.get("coal_kg_sum", 0.0)),
            energy_kwh_sum=float(acc.get("energy_kwh_sum", 0.0)),
            active_seconds=float(acc.get("active_seconds", 0.0)),
            burn_kgph_max_5m=float(acc.get("burn_kgph_max_5m", 0.0)),
            power_kw_max_5m=float(acc.get("power_kw_max_5m", 0.0)),
            coal_kg_max_5m=float(acc.get("coal_kg_max_5m", 0.0)),
            burn_kgph_min_active_5m=acc.get("burn_kgph_min_active_5m", None),
            power_kw_min_active_5m=acc.get("power_kw_min_active_5m", None),
            _has_active=bool(acc.get("_has_active", False)),
        )

    # ---------- BOOTSTRAP (z dysku) ----------

    def _bootstrap_from_disk(self) -> None:
        self._load_daily_file()
        self._load_state()
        self._load_5m_buckets(max_buckets=BUCKETS_7D)

    def _load_daily_file(self) -> None:
        if not self._daily_path.exists():
            return
        try:
            with self._daily_path.open("r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f, delimiter=";")
                for row in r:
                    d = (row.get("date") or "").strip()
                    if not d:
                        continue
                    rec = _DayRecord(
                        date_str=d,
                        seconds_sum=float(row.get("seconds_sum") or 0.0),
                        coal_kg_sum=float(row.get("coal_kg_sum") or 0.0),
                        energy_kwh_sum=float(row.get("energy_kwh_sum") or 0.0),
                        burn_kgph_avg=float(row.get("burn_kgph_avg") or 0.0),
                        power_kw_avg=float(row.get("power_kw_avg") or 0.0),
                        active_seconds=float(row.get("active_seconds") or 0.0),
                        active_ratio=float(row.get("active_ratio") or 0.0),
                        burn_kgph_max_5m=float(row.get("burn_kgph_max_5m") or 0.0),
                        burn_kgph_min_active_5m=(None if (row.get("burn_kgph_min_active_5m") or "").strip() == "" else float(row["burn_kgph_min_active_5m"])),
                        power_kw_max_5m=float(row.get("power_kw_max_5m") or 0.0),
                        power_kw_min_active_5m=(None if (row.get("power_kw_min_active_5m") or "").strip() == "" else float(row["power_kw_min_active_5m"])),
                        coal_kg_max_5m=float(row.get("coal_kg_max_5m") or 0.0),
                    )
                    self._daily[d] = rec
        except Exception:
            self._daily = {}

    def _load_5m_buckets(self, max_buckets: int) -> None:
        if not self._log_dir.exists():
            return

        prefix = f"{self._config.file_prefix_5m}_"
        files = sorted([p for p in self._log_dir.glob(f"{prefix}*.csv") if p.is_file()])
        if not files:
            return

        items: List[_Agg5mTimed] = []

        # czytaj od najnowszych plików, ale finalnie sortujemy po ts_end_unix
        for p in reversed(files):
            try:
                with p.open("r", encoding="utf-8", newline="") as f:
                    r = csv.DictReader(f, delimiter=";")
                    for row in r:
                        ts_end_unix = float(row.get("ts_end_unix") or 0.0)
                        ts_end_iso = (row.get("ts_end_iso") or "").strip()
                        s = float(row.get("seconds") or 0.0)
                        kg = float(row.get("coal_kg") or 0.0)
                        en = float(row.get("energy_kwh") or 0.0)
                        burn = float(row.get("burn_kgph") or self._rate_kgph(s, kg))
                        power = float(row.get("power_kw") or self._rate_kw(s, en))

                        a = _Agg(
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
                        items.append(_Agg5mTimed(ts_end_unix=ts_end_unix, ts_end_iso=ts_end_iso, agg=a))
            except Exception:
                continue

            if len(items) >= max_buckets * 2:
                # bezpieczny limit – potem i tak bierzemy końcówkę
                break

        if not items:
            return

        items.sort(key=lambda x: x.ts_end_unix)
        if len(items) > max_buckets:
            items = items[-max_buckets:]

        self._b5m.clear()
        for it in items:
            self._b5m.append(it)

    # ---------- OKNA "SPRZED X PRZEZ STAŁY CZAS" (porównania) ----------

    def _aggregate_window_offset(
        self,
        now_unix: float,
        duration_sec: float,
        end_offset_sec: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Okno porównawcze:
          end = floor_to_5m(now - end_offset)
          start = end - duration
        Zwraca sumy + średnie + max_5m + min_active_5m.
        """
        if not self._b5m:
            return None

        end_unix = self._floor_to_5m(now_unix - end_offset_sec)
        start_unix = end_unix - duration_sec

        buckets = [b for b in self._b5m if (b.ts_end_unix > start_unix and b.ts_end_unix <= end_unix)]
        if not buckets:
            return {
                "ts_start_unix": start_unix,
                "ts_end_unix": end_unix,
                "ts_start_iso": datetime.fromtimestamp(start_unix, tz=self._tz).isoformat(timespec="seconds"),
                "ts_end_iso": datetime.fromtimestamp(end_unix, tz=self._tz).isoformat(timespec="seconds"),
                "seconds_sum": 0.0,
                "coal_kg_sum": 0.0,
                "energy_kwh_sum": 0.0,
                "burn_kgph_avg": 0.0,
                "power_kw_avg": 0.0,
                "active_ratio": 0.0,
                "burn_kgph_max_5m": 0.0,
                "burn_kgph_min_active_5m": None,
                "power_kw_max_5m": 0.0,
                "power_kw_min_active_5m": None,
                "coal_kg_max_5m": 0.0,
            }

        seconds_sum = sum(b.agg.seconds for b in buckets)
        coal_sum = sum(b.agg.coal_kg for b in buckets)
        energy_sum = sum(b.agg.energy_kwh for b in buckets)

        burn_avg = self._rate_kgph(seconds_sum, coal_sum)
        power_avg = self._rate_kw(seconds_sum, energy_sum)

        burn_max_5m = max(b.agg.burn_kgph_avg for b in buckets)
        power_max_5m = max(b.agg.power_kw_avg for b in buckets)
        coal_max_5m = max(b.agg.coal_kg for b in buckets)

        active_buckets = [b for b in buckets if b.agg.coal_kg > 0]
        active_seconds = sum(b.agg.seconds for b in active_buckets)
        active_ratio = (active_seconds / seconds_sum) if seconds_sum > 0 else 0.0

        burn_min_active = None
        power_min_active = None
        if active_buckets:
            burn_min_active = min(b.agg.burn_kgph_avg for b in active_buckets)
            power_min_active = min(b.agg.power_kw_avg for b in active_buckets)

        return {
            "ts_start_unix": start_unix,
            "ts_end_unix": end_unix,
            "ts_start_iso": datetime.fromtimestamp(start_unix, tz=self._tz).isoformat(timespec="seconds"),
            "ts_end_iso": datetime.fromtimestamp(end_unix, tz=self._tz).isoformat(timespec="seconds"),
            "seconds_sum": float(seconds_sum),
            "coal_kg_sum": float(coal_sum),
            "energy_kwh_sum": float(energy_sum),
            "burn_kgph_avg": float(burn_avg),
            "power_kw_avg": float(power_avg),
            "active_ratio": float(active_ratio),
            "burn_kgph_max_5m": float(burn_max_5m),
            "burn_kgph_min_active_5m": burn_min_active,
            "power_kw_max_5m": float(power_max_5m),
            "power_kw_min_active_5m": power_min_active,
            "coal_kg_max_5m": float(coal_max_5m),
        }

    def _build_compare_bars(self, now_unix: float) -> Dict[str, Any]:
        """
        Generuje gotowe słupki do UI, wg Twojej semantyki:
        -1h = godzina sprzed godziny -> [now-2h, now-1h]
        -10m = 5 minut sprzed 10 minut -> [now-15m, now-10m] (czas trwania = 5m)
        """
        # 1) GODZINY: 3 słupki po 1h, kończące się -3h, -2h, -1h
        hours_1h = []
        for end_offset_h, label in [(3, "-3h"), (2, "-2h"), (1, "-1h")]:
            agg = self._aggregate_window_offset(now_unix, duration_sec=3600.0, end_offset_sec=end_offset_h * 3600.0)
            hours_1h.append({"label": label, **(agg or {})})

        # 2) MINUTY: 3 słupki po 5m, kończące się -15m, -10m, -5m
        minutes_5m = []
        for end_offset_m, label in [(15, "-15m"), (10, "-10m"), (5, "-5m")]:
            agg = self._aggregate_window_offset(now_unix, duration_sec=300.0, end_offset_sec=end_offset_m * 60.0)
            minutes_5m.append({"label": label, **(agg or {})})

        # 3) DNI: pełne dni kalendarzowe: -3d, -2d, -1d (z cache dziennego)
        now_dt = datetime.fromtimestamp(now_unix, tz=self._tz)
        today = now_dt.date()
        days = []
        for off, label in [(3, "-3d"), (2, "-2d"), (1, "-1d")]:
            d = (today - timedelta(days=off)).isoformat()
            rec = self._daily.get(d)
            days.append({"label": label, "date": d, "record": None if rec is None else asdict(rec)})

        # 4) TYGODNIE: pełne tygodnie ISO (pn-nd), -3tyg..-1tyg
        weeks = []
        week_start = today - timedelta(days=today.isoweekday() - 1)  # poniedziałek
        for off, label in [(3, "-3tyg"), (2, "-2tyg"), (1, "-1tyg")]:
            end = week_start - timedelta(days=7 * (off - 1))       # start tygodnia "bliższego"
            start = end - timedelta(days=7)                        # start tygodnia "dalszego"
            agg = self._sum_daily_range(start, end)
            weeks.append({"label": label, "week_start": start.isoformat(), "week_end": (end - timedelta(days=1)).isoformat(), **agg})

        # 5) MIESIĄCE: pełne miesiące kalendarzowe -3msc..-1msc
        months = []
        first_this_month = date(today.year, today.month, 1)
        for off, label in [(3, "-3msc"), (2, "-2msc"), (1, "-1msc")]:
            m_end = self._add_months(first_this_month, -(off - 1))
            m_start = self._add_months(first_this_month, -off)
            agg = self._sum_daily_range(m_start, m_end)
            months.append({"label": label, "month": m_start.strftime("%Y-%m"), **agg})

        # Opcjonalnie: 12h w tej samej semantyce (3 bloki po 12h kończące się -36,-24,-12)
        hours_12h = []
        for end_offset_h, label in [(36, "-36h"), (24, "-24h"), (12, "-12h")]:
            agg = self._aggregate_window_offset(now_unix, duration_sec=43200.0, end_offset_sec=end_offset_h * 3600.0)
            hours_12h.append({"label": label, **(agg or {})})

        return {
            "hours_1h": hours_1h,
            "hours_12h": hours_12h,
            "minutes_5m": minutes_5m,
            "days": days,
            "weeks": weeks,
            "months": months,
        }

    def _sum_daily_range(self, start_inclusive: date, end_exclusive: date) -> Dict[str, Any]:
        """
        Sumuje cache dzienny w zakresie [start, end).
        Zwraca format podobny do okien (sumy+avg+max+min_active).
        """
        keys = []
        d = start_inclusive
        while d < end_exclusive:
            keys.append(d.isoformat())
            d += timedelta(days=1)

        recs = [self._daily[k] for k in keys if k in self._daily]
        seconds_sum = sum(r.seconds_sum for r in recs)
        coal_sum = sum(r.coal_kg_sum for r in recs)
        energy_sum = sum(r.energy_kwh_sum for r in recs)

        burn_avg = self._rate_kgph(seconds_sum, coal_sum)
        power_avg = self._rate_kw(seconds_sum, energy_sum)

        burn_max_5m = max((r.burn_kgph_max_5m for r in recs), default=0.0)
        power_max_5m = max((r.power_kw_max_5m for r in recs), default=0.0)
        coal_max_5m = max((r.coal_kg_max_5m for r in recs), default=0.0)

        burn_min_active = self._min_optional([r.burn_kgph_min_active_5m for r in recs])
        power_min_active = self._min_optional([r.power_kw_min_active_5m for r in recs])

        active_seconds = sum(r.active_seconds for r in recs)
        active_ratio = (active_seconds / seconds_sum) if seconds_sum > 0 else 0.0

        return {
            "ts_start_iso": datetime.combine(start_inclusive, datetime.min.time(), tzinfo=self._tz).isoformat(timespec="seconds"),
            "ts_end_iso": datetime.combine(end_exclusive, datetime.min.time(), tzinfo=self._tz).isoformat(timespec="seconds"),
            "seconds_sum": float(seconds_sum),
            "coal_kg_sum": float(coal_sum),
            "energy_kwh_sum": float(energy_sum),
            "burn_kgph_avg": float(burn_avg),
            "power_kw_avg": float(power_avg),
            "active_ratio": float(active_ratio),
            "burn_kgph_max_5m": float(burn_max_5m),
            "burn_kgph_min_active_5m": burn_min_active,
            "power_kw_max_5m": float(power_max_5m),
            "power_kw_min_active_5m": power_min_active,
            "coal_kg_max_5m": float(coal_max_5m),
        }

    @staticmethod
    def _add_months(d: date, months: int) -> date:
        # proste przesunięcie miesięcy dla 1-go dnia miesiąca
        y = d.year + (d.month - 1 + months) // 12
        m = (d.month - 1 + months) % 12 + 1
        return date(y, m, 1)

    # ---------- PUBLIKACJA DO runtime ----------

    def _publish(self, now: float, system_state: SystemState, enabled: bool) -> None:
        ts_iso = datetime.fromtimestamp(now).isoformat(timespec="seconds")

        # rolling 5m: ostatni zamknięty bucket, a jeśli go brak, to aktualny (częściowy)
        a5: Optional[_Agg] = self._b5m[-1].agg if len(self._b5m) >= 1 else None
        if a5 is None and self._cur.seconds > 0:
            burn = self._rate_kgph(self._cur.seconds, self._cur.coal_kg)
            power = self._rate_kw(self._cur.seconds, self._cur.energy_kwh)
            a5 = _Agg(
                seconds=self._cur.seconds,
                coal_kg=self._cur.coal_kg,
                energy_kwh=self._cur.energy_kwh,
                burn_kgph_avg=burn,
                burn_kgph_min=burn,
                burn_kgph_max=burn,
                power_kw_avg=power,
                power_kw_min=power,
                power_kw_max=power,
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

        # --- Kalendarz + słupki dzienne (jak wcześniej) ---
        cal = self._build_calendar_payload(now=float(now))
        payload["calendar"] = cal

        # --- NOWE: porównawcze słupki "sprzed X przez stały czas" ---
        if self._config.publish_compare_bars:
            payload["compare_bars"] = self._build_compare_bars(now_unix=float(now))

        system_state.runtime["stats"] = payload  # bez fallbacków

    # ---------- KALENDARZ / SEZON / SŁUPKI DZIENNE ----------

    def _build_calendar_payload(self, now: float) -> Dict[str, Any]:
        now_dt = datetime.fromtimestamp(now, tz=self._tz)
        today_key = now_dt.date().isoformat()
        yesterday_key = (now_dt.date() - timedelta(days=1)).isoformat()

        y = self._daily.get(yesterday_key)

        acc_today = self._day_acc if self._day_key == today_key else _DayAcc()

        today_seconds = acc_today.seconds_sum
        today_coal = acc_today.coal_kg_sum
        today_energy = acc_today.energy_kwh_sum
        today_burn_avg = self._rate_kgph(today_seconds, today_coal)
        today_power_avg = self._rate_kw(today_seconds, today_energy)
        today_active_ratio = (acc_today.active_seconds / today_seconds) if today_seconds > 0 else 0.0

        # miesiąc bieżący (sumy z daily + dziś)
        month_prefix = today_key[:7]
        month_days = [rec for k, rec in self._daily.items() if k.startswith(month_prefix)]
        month_seconds = sum(r.seconds_sum for r in month_days) + today_seconds
        month_coal = sum(r.coal_kg_sum for r in month_days) + today_coal
        month_energy = sum(r.energy_kwh_sum for r in month_days) + today_energy
        month_burn_avg = self._rate_kgph(month_seconds, month_coal)
        month_power_avg = self._rate_kw(month_seconds, month_energy)

        month_burn_max_5m = max([r.burn_kgph_max_5m for r in month_days] + [acc_today.burn_kgph_max_5m], default=0.0)
        month_power_max_5m = max([r.power_kw_max_5m for r in month_days] + [acc_today.power_kw_max_5m], default=0.0)
        month_coal_max_5m = max([r.coal_kg_max_5m for r in month_days] + [acc_today.coal_kg_max_5m], default=0.0)

        month_burn_min_active = self._min_optional(
            [r.burn_kgph_min_active_5m for r in month_days] + [acc_today.burn_kgph_min_active_5m if acc_today._has_active else None]
        )
        month_power_min_active = self._min_optional(
            [r.power_kw_min_active_5m for r in month_days] + [acc_today.power_kw_min_active_5m if acc_today._has_active else None]
        )

        season_start = self._season_start_date(now_dt.date())
        season_keys = [k for k in self._daily.keys() if k >= season_start.isoformat() and k <= yesterday_key]
        season_days = [self._daily[k] for k in sorted(season_keys)]

        season_seconds = sum(r.seconds_sum for r in season_days) + today_seconds
        season_coal = sum(r.coal_kg_sum for r in season_days) + today_coal
        season_energy = sum(r.energy_kwh_sum for r in season_days) + today_energy
        season_burn_avg = self._rate_kgph(season_seconds, season_coal)
        season_power_avg = self._rate_kw(season_seconds, season_energy)

        season_burn_max_5m = max([r.burn_kgph_max_5m for r in season_days] + [acc_today.burn_kgph_max_5m], default=0.0)
        season_power_max_5m = max([r.power_kw_max_5m for r in season_days] + [acc_today.power_kw_max_5m], default=0.0)
        season_coal_max_5m = max([r.coal_kg_max_5m for r in season_days] + [acc_today.coal_kg_max_5m], default=0.0)

        season_burn_min_active = self._min_optional(
            [r.burn_kgph_min_active_5m for r in season_days] + [acc_today.burn_kgph_min_active_5m if acc_today._has_active else None]
        )
        season_power_min_active = self._min_optional(
            [r.power_kw_min_active_5m for r in season_days] + [acc_today.power_kw_min_active_5m if acc_today._has_active else None]
        )

        bars_days = max(1, int(self._config.bars_days))
        bars = self._build_daily_bars(today_key=today_key, acc_today=acc_today, count=bars_days)

        return {
            "timezone": self._config.timezone,
            "season_start": season_start.isoformat(),

            "today": {
                "date": today_key,
                "seconds_sum": today_seconds,
                "coal_kg_sum": today_coal,
                "energy_kwh_sum": today_energy,
                "burn_kgph_avg": today_burn_avg,
                "power_kw_avg": today_power_avg,
                "active_seconds": acc_today.active_seconds,
                "active_ratio": today_active_ratio,
                "burn_kgph_max_5m": acc_today.burn_kgph_max_5m,
                "burn_kgph_min_active_5m": acc_today.burn_kgph_min_active_5m if acc_today._has_active else None,
                "power_kw_max_5m": acc_today.power_kw_max_5m,
                "power_kw_min_active_5m": acc_today.power_kw_min_active_5m if acc_today._has_active else None,
                "coal_kg_max_5m": acc_today.coal_kg_max_5m,
            },

            "yesterday": None if y is None else asdict(y),

            "month": {
                "month": month_prefix,
                "seconds_sum": month_seconds,
                "coal_kg_sum": month_coal,
                "energy_kwh_sum": month_energy,
                "burn_kgph_avg": month_burn_avg,
                "power_kw_avg": month_power_avg,
                "burn_kgph_max_5m": month_burn_max_5m,
                "burn_kgph_min_active_5m": month_burn_min_active,
                "power_kw_max_5m": month_power_max_5m,
                "power_kw_min_active_5m": month_power_min_active,
                "coal_kg_max_5m": month_coal_max_5m,
            },

            "season": {
                "start": season_start.isoformat(),
                "seconds_sum": season_seconds,
                "coal_kg_sum": season_coal,
                "energy_kwh_sum": season_energy,
                "burn_kgph_avg": season_burn_avg,
                "power_kw_avg": season_power_avg,
                "burn_kgph_max_5m": season_burn_max_5m,
                "burn_kgph_min_active_5m": season_burn_min_active,
                "power_kw_max_5m": season_power_max_5m,
                "power_kw_min_active_5m": season_power_min_active,
                "coal_kg_max_5m": season_coal_max_5m,
            },

            "bars_daily": bars,
        }

    def _build_daily_bars(self, today_key: str, acc_today: _DayAcc, count: int) -> List[Dict[str, Any]]:
        keys = sorted(self._daily.keys())
        tail = keys[-max(0, count - 1):] if count > 1 else []
        bars: List[Dict[str, Any]] = []

        for k in tail:
            r = self._daily[k]
            bars.append(
                {
                    "date": r.date_str,
                    "coal_kg_sum": r.coal_kg_sum,
                    "burn_kgph_avg": r.burn_kgph_avg,
                    "power_kw_avg": r.power_kw_avg,
                    "burn_kgph_max_5m": r.burn_kgph_max_5m,
                    "burn_kgph_min_active_5m": r.burn_kgph_min_active_5m,
                    "power_kw_max_5m": r.power_kw_max_5m,
                    "power_kw_min_active_5m": r.power_kw_min_active_5m,
                    "coal_kg_max_5m": r.coal_kg_max_5m,
                    "active_ratio": r.active_ratio,
                }
            )

        if self._day_key == today_key:
            seconds = acc_today.seconds_sum
            coal = acc_today.coal_kg_sum
            energy = acc_today.energy_kwh_sum
            bars.append(
                {
                    "date": today_key,
                    "coal_kg_sum": coal,
                    "burn_kgph_avg": self._rate_kgph(seconds, coal),
                    "power_kw_avg": self._rate_kw(seconds, energy),
                    "burn_kgph_max_5m": acc_today.burn_kgph_max_5m,
                    "burn_kgph_min_active_5m": acc_today.burn_kgph_min_active_5m if acc_today._has_active else None,
                    "power_kw_max_5m": acc_today.power_kw_max_5m,
                    "power_kw_min_active_5m": acc_today.power_kw_min_active_5m if acc_today._has_active else None,
                    "coal_kg_max_5m": acc_today.coal_kg_max_5m,
                    "active_ratio": (acc_today.active_seconds / seconds) if seconds > 0 else 0.0,
                }
            )

        return bars[-count:]

    def _season_start_date(self, today: date) -> date:
        mm = int(self._config.season_start_month)
        dd = int(self._config.season_start_day)
        candidate = date(today.year, mm, dd)
        if today >= candidate:
            return candidate
        return date(today.year - 1, mm, dd)

    @staticmethod
    def _min_optional(values: List[Optional[float]]) -> Optional[float]:
        xs = [v for v in values if v is not None]
        if not xs:
            return None
        return min(xs)

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

        if "log_dir" in values:
            self._config.log_dir = str(values["log_dir"])
            self._log_dir = (self._base_path / self._config.log_dir).resolve()
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._daily_path = self._log_dir / self._config.daily_file
            self._state_path = self._log_dir / self._config.state_file

        if "file_prefix_5m" in values:
            self._config.file_prefix_5m = str(values["file_prefix_5m"])

        if "daily_file" in values:
            self._config.daily_file = str(values["daily_file"])
            self._daily_path = self._log_dir / self._config.daily_file

        if "state_file" in values:
            self._config.state_file = str(values["state_file"])
            self._state_path = self._log_dir / self._config.state_file

        if "timezone" in values:
            self._config.timezone = str(values["timezone"])
            self._tz = ZoneInfo(self._config.timezone)

        if "season_start_month" in values:
            self._config.season_start_month = int(values["season_start_month"])
        if "season_start_day" in values:
            self._config.season_start_day = int(values["season_start_day"])

        if "bars_days" in values:
            self._config.bars_days = int(values["bars_days"])

        if "publish_compare_bars" in values:
            self._config.publish_compare_bars = bool(values["publish_compare_bars"])

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

        if "log_dir" in data:
            self._config.log_dir = str(data["log_dir"])
        if "file_prefix_5m" in data:
            self._config.file_prefix_5m = str(data["file_prefix_5m"])
        if "daily_file" in data:
            self._config.daily_file = str(data["daily_file"])
        if "state_file" in data:
            self._config.state_file = str(data["state_file"])
        if "timezone" in data:
            self._config.timezone = str(data["timezone"])
        if "season_start_month" in data:
            self._config.season_start_month = int(data["season_start_month"])
        if "season_start_day" in data:
            self._config.season_start_day = int(data["season_start_day"])
        if "bars_days" in data:
            self._config.bars_days = int(data["bars_days"])
        if "publish_compare_bars" in data:
            self._config.publish_compare_bars = bool(data["publish_compare_bars"])

    def _save_config_to_file(self) -> None:
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(self._config), f, sort_keys=True, allow_unicode=True)

