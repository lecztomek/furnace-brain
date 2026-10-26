"""
Statistics computed from the data the controller already records:
- history CSV (every ~30 s): temperatures, power, mode, outside temp,
  blower/feeder share, WORK setpoint,
- stats 5 min buckets: coal burned,
- event log: alarms.

Everything is aggregated per day (past days are cached in a JSON file, so the
season view doesn't re-read months of CSV) and then summarised per period.
Read-only - nothing here touches the control loop.
"""
from __future__ import annotations

import csv
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from backend.core.csv_utils import no_nul

CACHE_VERSION = 2
MAX_STEP_S = 60.0          # a row "covers" at most this long (2x the 30 s interval)
GAP_S = 120.0              # a longer pause between rows is a gap in the data
SETTLE_S = 20 * 60         # regulation quality: skip the first 20 min after entering WORK
FULL_DAY_SHARE = 0.8       # days with less data don't go into the weather model

AVG_KEYS = ("boiler", "radiators", "flue", "outside", "power", "fan", "feeder", "flue_work")

# efficiency estimates
HDD_BASE_C = 18.0          # degree-days: max(0, 18 - daily mean outside temperature)
MIN_HDD = 3.0              # a period needs at least this many degree-days for kg/degree-day
ROOM_C = 20.0              # boiler room (no sensor) for the flue loss
SIEGERT_A1, SIEGERT_B = 0.68, 0.007   # Siegert coefficients for coal
ASSUMED_CO2 = 10.0         # % CO2 in the flue gas - not measured, typical value


def flue_loss_pct(flue_temp: Optional[float]) -> Optional[float]:
    """Siegert flue gas loss [%] - an estimate (CO2 assumed, not measured)."""
    if flue_temp is None:
        return None
    return max(0.0, (flue_temp - ROOM_C) * (SIEGERT_A1 / ASSUMED_CO2 + SIEGERT_B))


def degree_days(day: "DayAgg") -> Optional[float]:
    outside = day.avg("outside")
    if outside is None:
        return None
    return max(0.0, HDD_BASE_C - outside) * day.seconds / 86400.0


def kg_per_degree_day(days: List["DayAgg"]) -> Optional[float]:
    pairs = [(d.coal_kg, degree_days(d)) for d in days]
    hdd = sum(h for _, h in pairs if h is not None)
    coal = sum(c for c, h in pairs if h is not None)
    return coal / hdd if hdd >= MIN_HDD else None


@dataclass
class DayAgg:
    date: str
    seconds: float = 0.0
    gap_s: float = 0.0
    rows: int = 0
    missing_boiler: int = 0
    sums: Dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in AVG_KEYS})
    weights: Dict[str, float] = field(default_factory=lambda: {k: 0.0 for k in AVG_KEYS})
    mode_s: Dict[str, float] = field(default_factory=dict)
    ignitions: int = 0
    boiler_max: Optional[float] = None
    # regulation quality (settled WORK)
    st_s: float = 0.0
    st_abs: float = 0.0
    st_sq: float = 0.0
    st_in1: float = 0.0
    st_in2: float = 0.0
    st_over: float = 0.0
    st_under: float = 0.0
    coal_kg: float = 0.0
    energy_kwh: float = 0.0
    power_kw_max: float = 0.0  # highest 5 min average
    alarms: int = 0

    def avg(self, key: str) -> Optional[float]:
        w = self.weights.get(key, 0.0)
        return self.sums[key] / w if w > 0 else None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DayAgg":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- reading files

def _rows(path: Path) -> Iterable[Dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            yield from csv.DictReader(no_nul(f), delimiter=";")
    except (OSError, csv.Error):
        return  # a damaged file must not break the whole report


def files_by_day(directory: Path, prefix: str) -> Dict[str, List[Path]]:
    """
    {"YYYYMMDD": [files]} for <prefix>_YYYYMMDD*.csv - the directory is listed
    once per report instead of once per day (thousands of files on the Pi).
    """
    out: Dict[str, List[Path]] = {}
    try:
        names = os.listdir(directory)
    except OSError:
        return out
    start = len(prefix) + 1
    for name in sorted(names):
        if name.startswith(prefix + "_") and name.endswith(".csv"):
            out.setdefault(name[start:start + 8], []).append(directory / name)
    return out


def _day_files(directory: Path, prefix: str, d: date, index: Optional[Dict[str, List[Path]]]) -> List[Path]:
    if index is not None:
        return index.get(f"{d:%Y%m%d}", [])
    return sorted(directory.glob(f"{prefix}_{d:%Y%m%d}*.csv"))


def history_rows(history_dir: Path, day: date, prefix: str = "boiler",
                 index: Optional[Dict[str, List[Path]]] = None) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path in _day_files(history_dir, prefix, day, index):
        rows.extend(_rows(path))
    rows.sort(key=lambda r: r.get("data_czas") or "")
    return rows


def coal_for_day(stats_dir: Path, day: date, prefix: str = "stats5m",
                 index: Optional[Dict[str, List[Path]]] = None) -> Tuple[float, float, float]:
    coal = energy = power_max = 0.0
    key = day.isoformat()
    # a bucket ending at 00:00 belongs to the previous day - also look one file ahead
    for d in (day, day + timedelta(days=1)):
        for path in _day_files(stats_dir, prefix, d, index):
            for r in _rows(path):
                end = r.get("ts_end_iso") or ""
                ts = _float(r.get("ts_end_unix"))
                bucket_day = (datetime.fromtimestamp(ts - 1).date().isoformat() if ts is not None else end[:10])
                if bucket_day == key:
                    coal += _float(r.get("coal_kg")) or 0.0
                    energy += _float(r.get("energy_kwh")) or 0.0
                    power_max = max(power_max, _float(r.get("power_kw")) or 0.0)
    return coal, energy, power_max


def alarms_for_day(events_dir: Path, day: date, prefix: str = "events",
                   index: Optional[Dict[str, List[Path]]] = None) -> int:
    n = 0
    for path in _day_files(events_dir, prefix, day, index):
        try:
            if "ALARM" not in path.read_text(encoding="utf-8", errors="replace").upper():
                continue  # most files have no alarm - skip parsing them
        except OSError:
            continue
        n += sum(1 for r in _rows(path) if (r.get("level") or "").upper() == "ALARM")
    return n


# ---------------------------------------------------------------- one day

def aggregate_day(day: date, rows: List[Dict[str, str]], coal_kg: float = 0.0,
                  energy_kwh: float = 0.0, alarms: int = 0,
                  fallback_setpoint: Optional[float] = None, power_kw_max: float = 0.0) -> DayAgg:
    agg = DayAgg(date=day.isoformat(), coal_kg=coal_kg, energy_kwh=energy_kwh, alarms=alarms,
                 power_kw_max=power_kw_max)
    parsed = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r.get("data_czas") or "")
        except ValueError:
            continue
        parsed.append((ts, r))

    prev_mode = None
    work_since: Optional[datetime] = None
    for i, (ts, r) in enumerate(parsed):
        nxt = parsed[i + 1][0] if i + 1 < len(parsed) else None
        step = (nxt - ts).total_seconds() if nxt else 30.0
        if step > GAP_S:
            agg.gap_s += step - 30.0
        w = max(0.0, min(step, MAX_STEP_S))
        agg.seconds += w
        agg.rows += 1

        mode = (r.get("tryb_pracy") or "").upper() or "UNKNOWN"
        agg.mode_s[mode] = agg.mode_s.get(mode, 0.0) + w
        if mode == "IGNITION" and prev_mode not in (None, "IGNITION"):
            agg.ignitions += 1
        if mode == "WORK" and prev_mode != "WORK":
            work_since = ts
        prev_mode = mode

        boiler = _float(r.get("temp_pieca"))
        values = {
            "boiler": boiler,
            "radiators": _float(r.get("temp_grzejnikow")),
            "flue": _float(r.get("temp_spalin")),
            "outside": _float(r.get("temp_zewn")),
            "power": _float(r.get("power")),
            "fan": _float(r.get("dmuchawa")),
            "feeder": _float(r.get("slimak")),
            "flue_work": _float(r.get("temp_spalin")) if mode == "WORK" else None,
        }
        for k, v in values.items():
            if v is not None:
                agg.sums[k] += v * w
                agg.weights[k] += w
        if boiler is None:
            agg.missing_boiler += 1
            continue
        agg.boiler_max = boiler if agg.boiler_max is None else max(agg.boiler_max, boiler)

        setpoint = _float(r.get("zadana"))
        if setpoint is None:
            setpoint = fallback_setpoint
        if mode == "WORK" and setpoint is not None and work_since is not None \
                and (ts - work_since).total_seconds() >= SETTLE_S:
            err = boiler - setpoint
            agg.st_s += w
            agg.st_abs += abs(err) * w
            agg.st_sq += err * err * w
            agg.st_in1 += w if abs(err) <= 1.0 else 0.0
            agg.st_in2 += w if abs(err) <= 2.0 else 0.0
            agg.st_over = max(agg.st_over, err)
            agg.st_under = max(agg.st_under, -err)
    return agg


# ---------------------------------------------------------------- periods

def _r(v: Optional[float], nd: int = 1) -> Optional[float]:
    return None if v is None else round(v, nd)


def summarize(days: List[DayAgg]) -> Dict[str, Any]:
    secs = sum(d.seconds for d in days)
    covered_days = secs / 86400.0

    def avg(key: str) -> Optional[float]:
        w = sum(d.weights.get(key, 0.0) for d in days)
        return sum(d.sums.get(key, 0.0) for d in days) / w if w > 0 else None

    coal = sum(d.coal_kg for d in days)
    mode_s: Dict[str, float] = {}
    for d in days:
        for m, s in d.mode_s.items():
            mode_s[m] = mode_s.get(m, 0.0) + s

    st_s = sum(d.st_s for d in days)
    regulation = None
    if st_s >= 1800:
        in1 = 100 * sum(d.st_in1 for d in days) / st_s
        in2 = 100 * sum(d.st_in2 for d in days) / st_s
        regulation = {
            "hours": _r(st_s / 3600),
            "mean_abs_error": _r(sum(d.st_abs for d in days) / st_s, 2),
            "std": _r(math.sqrt(sum(d.st_sq for d in days) / st_s), 2),
            "within_1": _r(in1),
            "within_2": _r(in2),
            "max_over": _r(max(d.st_over for d in days)),
            "max_under": _r(max(d.st_under for d in days)),
            "grade": ("bardzo dobra" if in1 >= 80 else "dobra" if in2 >= 80
                      else "przeciętna" if in2 >= 60 else "słaba"),
        }

    return {
        "days": len(days),
        "hours": _r(secs / 3600),
        "gap_hours": _r(sum(d.gap_s for d in days) / 3600),
        "coal_kg": _r(coal, 2),
        "coal_kg_per_day": _r(coal / covered_days, 2) if covered_days >= 0.04 else None,
        "coal_kg_per_hour": _r(coal / (secs / 3600), 3) if secs >= 3600 else None,
        "energy_kwh": _r(sum(d.energy_kwh for d in days), 1),
        "energy_kwh_per_day": _r(sum(d.energy_kwh for d in days) / covered_days, 1) if covered_days >= 0.04 else None,
        "power_kw_avg": _r(sum(d.energy_kwh for d in days) / (secs / 3600), 2) if secs >= 3600 else None,
        "power_kw_max": _r(max((d.power_kw_max for d in days), default=0.0), 2),
        "avg": {k: _r(avg(k)) for k in AVG_KEYS},
        "boiler_max": _r(max((d.boiler_max for d in days if d.boiler_max is not None), default=None)),
        "mode_share": {m: _r(100 * s / secs) for m, s in sorted(mode_s.items())} if secs else {},
        "ignitions": sum(d.ignitions for d in days),
        "ignitions_per_day": _r(sum(d.ignitions for d in days) / covered_days, 2) if covered_days >= 0.5 else None,
        "missing_boiler_rows": sum(d.missing_boiler for d in days),
        "alarms": sum(d.alarms for d in days),
        "regulation": regulation,
        "efficiency": {
            "degree_days": _r(sum(h for h in (degree_days(d) for d in days) if h is not None), 1),
            "kg_per_degree_day": _r(kg_per_degree_day(days), 3),
            "flue_loss_pct": _r(flue_loss_pct(avg("flue_work")), 1),
        },
    }


# ---------------------------------------------------------------- weather model

def linear_fit(points: List[Tuple[float, float]]) -> Optional[Dict[str, float]]:
    n = len(points)
    if n < 5:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    if max(xs) - min(xs) < 3.0:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"slope": round(slope, 4), "intercept": round(intercept, 3), "r2": round(r2, 3), "n": n}


def weather_model(days: List[DayAgg]) -> Dict[str, Any]:
    full = [d for d in days if d.seconds >= FULL_DAY_SHARE * 86400 and d.avg("outside") is not None]
    coal_pts = [(d.avg("outside"), d.coal_kg / d.seconds * 86400) for d in full]
    energy_pts = [(d.avg("outside"), d.energy_kwh / d.seconds * 86400) for d in full]
    power_pts = [(d.avg("outside"), d.avg("power")) for d in full if d.avg("power") is not None]
    return {
        "days_with_data": len(full),
        "days_needed": 5,
        "coal": {"fit": linear_fit(coal_pts),
                 "points": [{"date": d.date, "x": round(x, 1), "y": round(y, 2)} for d, (x, y) in zip(full, coal_pts)]},
        "energy": {"fit": linear_fit(energy_pts),
                   "points": [{"x": round(x, 1), "y": round(y, 1)} for x, y in energy_pts]},
        "power": {"fit": linear_fit(power_pts),
                  "points": [{"x": round(x, 1), "y": round(y, 1)} for x, y in power_pts]},
    }


# ---------------------------------------------------------------- health checks

def _avg_days(days: List[DayAgg], key: str) -> Optional[float]:
    w = sum(d.weights.get(key, 0.0) for d in days)
    return sum(d.sums.get(key, 0.0) for d in days) / w if w > 0 else None


def checks(period: Dict[str, Any], model: Dict[str, Any], last_full_day: Optional[DayAgg],
           overheat_temp: float = 80.0, trend: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    def add(status: str, title: str, detail: str) -> None:
        out.append({"status": status, "title": title, "detail": detail})

    hours = period.get("hours") or 0
    if not hours:
        add("info", "Brak danych", "Historia z tego okresu jest pusta.")
        return out

    gap = period.get("gap_hours") or 0
    share = 100 * gap / (hours + gap)
    add("ok" if share < 1 else "warn" if share < 5 else "bad", "Ciągłość danych",
        f"przerwy w zapisie: {gap:.1f} h ({share:.1f}%)" if gap else "bez przerw w zapisie")

    missing = period.get("missing_boiler_rows") or 0
    add("ok" if missing == 0 else "bad", "Czujnik kotła",
        "zawsze odczytany" if missing == 0 else f"brak odczytu w {missing} pomiarach")

    outside = period["avg"].get("outside")
    add("ok" if outside is not None else "warn", "Temperatura zewnętrzna",
        f"średnio {outside:.1f}°C" if outside is not None else "brak danych pogodowych (moduł Pogoda / internet)")

    bmax = period.get("boiler_max")
    if bmax is not None:
        add("bad" if bmax >= overheat_temp else "warn" if bmax >= overheat_temp - 5 else "ok",
            "Przegrzanie kotła", f"najwyżej {bmax:.1f}°C (próg ochrony {overheat_temp:.0f}°C)")

    if period.get("alarms"):
        add("bad", "Alarmy", f"{period['alarms']} alarmów w logach - sprawdź Logi (poziom ALARM)")
    else:
        add("ok", "Alarmy", "brak")

    ipd = period.get("ignitions_per_day")
    if ipd is not None:
        add("warn" if ipd > 1.5 else "ok", "Rozpalenia",
            f"{period['ignitions']} ({ipd:.1f} na dobę)" + (" - czy piec nie wygasa?" if ipd > 1.5 else ""))

    flue = period["avg"].get("flue_work")
    if flue is not None:
        add("warn" if flue > 220 or flue < 60 else "ok", "Spaliny w pracy (orientacyjnie)",
            f"średnio {flue:.0f}°C" + (" - wysokie: straty / zabrudzony kocioł?" if flue > 220
                                        else " - niskie: słaby ogień / zbyt mało powietrza?" if flue < 60 else ""))

    reg = period.get("regulation")
    if reg:
        st = "ok" if reg["within_2"] >= 80 else "warn" if reg["within_2"] >= 60 else "bad"
        add(st, "Regulacja temperatury",
            f"{reg['grade']}: {reg['within_2']:.0f}% czasu w ±2°C, średnio {reg['mean_abs_error']:.1f}°C od zadanej")
    else:
        add("info", "Regulacja temperatury", "za mało pracy w trybie PRACA w tym okresie")

    change = (trend or {}).get("change_pct")
    if change is not None:
        add("warn" if change > 15 else "ok", "Efektywność (kg na stopniodzień)",
            f"ostatni tydzień {trend['recent_kg_per_degree_day']:.2f} vs wcześniej {trend['before_kg_per_degree_day']:.2f} "
            f"({change:+.0f}%)" + (" - kocioł pali więcej przy tej samej pogodzie: czyszczenie?" if change > 15 else ""))

    fit = (model.get("coal") or {}).get("fit")
    if fit and last_full_day and last_full_day.avg("outside") is not None and last_full_day.seconds > 0:
        expected = fit["intercept"] + fit["slope"] * last_full_day.avg("outside")
        actual = last_full_day.coal_kg / last_full_day.seconds * 86400
        if expected > 0:
            diff = 100 * (actual - expected) / expected
            add("warn" if diff > 25 else "ok", "Zużycie względem pogody",
                f"{last_full_day.date}: {actual:.1f} kg przy {last_full_day.avg('outside'):.1f}°C, "
                f"oczekiwane ~{expected:.1f} kg ({diff:+.0f}%)" + (" - czas wyczyścić kocioł?" if diff > 25 else ""))
    return out


# ---------------------------------------------------------------- whole report

class Analytics:
    def __init__(self, history_dir: Path, stats_dir: Path, events_dir: Path, cache_path: Path) -> None:
        self.history_dir = history_dir
        self.stats_dir = stats_dir
        self.events_dir = events_dir
        self.cache_path = cache_path
        self._cache: Dict[str, Dict[str, Any]] = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data.get("days", {}) if data.get("version") == CACHE_VERSION else {}
        except (OSError, ValueError):
            return {}

    def _save_cache(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": CACHE_VERSION, "days": self._cache}), encoding="utf-8")
            tmp.replace(self.cache_path)
        except OSError:
            pass

    def day(self, d: date, today: date, fallback_setpoint: Optional[float],
            indexes: Optional[Dict[str, Dict[str, List[Path]]]] = None) -> DayAgg:
        key = d.isoformat()
        if d < today and key in self._cache:
            return DayAgg.from_dict(self._cache[key])
        ix = indexes or {}
        coal, energy, power_max = coal_for_day(self.stats_dir, d, index=ix.get("stats"))
        agg = aggregate_day(d, history_rows(self.history_dir, d, index=ix.get("history")), coal, energy,
                            alarms_for_day(self.events_dir, d, index=ix.get("events")), fallback_setpoint, power_max)
        if d < today:
            self._cache[key] = asdict(agg)
        return agg

    def report(self, today: date, season_start: date, fallback_setpoint: Optional[float] = None,
               overheat_temp: float = 80.0, pause_s: float = 0.0) -> Dict[str, Any]:
        """pause_s: pause after each day read from files (background warm-up on the Pi)."""
        first = min(season_start, today - timedelta(days=29))
        days: List[DayAgg] = []
        cached_before = len(self._cache)
        indexes = {
            "history": files_by_day(self.history_dir, "boiler"),
            "stats": files_by_day(self.stats_dir, "stats5m"),
            "events": files_by_day(self.events_dir, "events"),
        }
        d = first
        while d <= today:
            from_files = not (d < today and d.isoformat() in self._cache)
            agg = self.day(d, today, fallback_setpoint, indexes)
            if from_files and pause_s:
                time.sleep(pause_s)
            if agg.rows or agg.coal_kg:
                days.append(agg)
            d += timedelta(days=1)
        if len(self._cache) != cached_before:
            self._save_cache()

        def since(start: date) -> List[DayAgg]:
            return [x for x in days if date.fromisoformat(x.date) >= start]

        periods = {
            "today": summarize(since(today)),
            "yesterday": summarize([x for x in days if x.date == (today - timedelta(days=1)).isoformat()]),
            "d7": summarize(since(today - timedelta(days=6))),
            "d30": summarize(since(today - timedelta(days=29))),
            "season": summarize(since(season_start)),
        }
        model = weather_model(since(today - timedelta(days=365)))
        full_days = [x for x in days if x.date < today.isoformat() and x.seconds >= FULL_DAY_SHARE * 86400]
        daily = [{
            "date": x.date,
            "coal_kg": round(x.coal_kg, 2),
            "energy_kwh": round(x.energy_kwh, 1),
            "hours": round(x.seconds / 3600, 1),
            "outside": _r(x.avg("outside")),
            "power": _r(x.avg("power")),
            "boiler": _r(x.avg("boiler")),
            "within_2": _r(100 * x.st_in2 / x.st_s) if x.st_s >= 1800 else None,
            # efficiency per day only for (nearly) full days - a partial day skews it
            "kg_per_degree_day": _r(kg_per_degree_day([x]), 3) if x.seconds >= FULL_DAY_SHARE * 86400 else None,
            "flue_loss_pct": _r(flue_loss_pct(x.avg("flue_work")), 1) if x.seconds >= FULL_DAY_SHARE * 86400 else None,
        } for x in since(today - timedelta(days=29))]

        # efficiency trend: last 7 full days against the 3 weeks before
        recent = [x for x in full_days if x.date >= (today - timedelta(days=7)).isoformat()]
        before = [x for x in full_days if (today - timedelta(days=30)).isoformat() <= x.date < (today - timedelta(days=7)).isoformat()]
        k_recent, k_before = kg_per_degree_day(recent), kg_per_degree_day(before)
        trend = {
            "recent_kg_per_degree_day": _r(k_recent, 3),
            "before_kg_per_degree_day": _r(k_before, 3),
            "change_pct": _r(100 * (k_recent - k_before) / k_before) if k_recent and k_before else None,
            "recent_flue_loss_pct": _r(flue_loss_pct(_avg_days(recent, "flue_work")), 1),
            "before_flue_loss_pct": _r(flue_loss_pct(_avg_days(before, "flue_work")), 1),
        }
        return {
            "today": today.isoformat(),
            "season_start": season_start.isoformat(),
            "periods": periods,
            "daily": daily,
            "weather": model,
            "efficiency_trend": trend,
            "efficiency_assumptions": {"degree_day_base_c": HDD_BASE_C, "room_c": ROOM_C, "co2_pct": ASSUMED_CO2},
            "checks": checks(periods["d7"], model, full_days[-1] if full_days else None, overheat_temp, trend),
        }
