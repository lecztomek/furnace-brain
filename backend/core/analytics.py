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

CACHE_VERSION = 4
MAX_STEP_S = 60.0          # a row "covers" at most this long (2x the 30 s interval)
GAP_S = 120.0              # a longer pause between rows is a gap in the data
SETTLE_S = 20 * 60         # regulation quality: skip the first 20 min after entering WORK
# temperature drops: settled WORK, boiler this far below the setpoint
DROP_BELOW_C = 4.0
POWER_HIGH = 90.0          # power stability: "at full power" from this %
POWER_LOW = 25.0           # ... and "at the minimum" up to this %
DROP_MIN_S = 300.0         # shorter dips are not listed
DROP_GAP_S = 120.0         # a short return above the line doesn't split a drop
FULL_DAY_SHARE = 0.8       # days with less data - or less time burning - don't go into the models

AVG_KEYS = ("boiler", "radiators", "flue", "outside", "power", "fan", "feeder", "flue_work")

# efficiency estimates
HDD_BASE_C = 18.0          # degree-days: max(0, 18 - daily mean outside temperature)
IGNITION_MIN_S = 300.0     # shorter IGNITION stretches are clicks, not ignitions
# typical monthly mean outside temperature (Nowy Sącz, 1991-2020 normals) for the
# season forecast; heating season September - May
NORMAL_MONTH_C = {9: 13.5, 10: 8.5, 11: 3.5, 12: -0.5, 1: -2.5, 2: -1.5, 3: 2.5, 4: 8.5, 5: 13.5}
SEASON_MONTHS = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8]
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


def is_full_day(d: "DayAgg") -> bool:
    """A day for the coal models: recorded and burning (WORK or IGNITION) for most of it.
    A day the boiler stood off half the time burns half the coal - it would pull every forecast down."""
    burning = d.mode_s.get("WORK", 0.0) + d.mode_s.get("IGNITION", 0.0)
    return d.seconds >= FULL_DAY_SHARE * 86400 and burning >= FULL_DAY_SHARE * 86400


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
    # hour of day (settled WORK): [seconds, boiler*s, setpoint*s, power*s, power seconds, seconds below]
    hours: List[List[float]] = field(default_factory=lambda: [[0.0] * 6 for _ in range(24)])
    # drops below setpoint - DROP_BELOW_C: start, minutes, lowest temp, setpoint, power
    drops: List[Dict[str, Any]] = field(default_factory=list)
    # power in settled WORK: seconds, sum, sum of squares, at full power / minimum, |change| per row
    # (days cached before these fields have zeros - left out of the power stability)
    pw_s: float = 0.0
    pw_sum: float = 0.0
    pw_sq: float = 0.0
    pw_max_s: float = 0.0
    pw_min_s: float = 0.0
    pw_jump: float = 0.0
    pw_jumps: int = 0
    # hour of day in settled WORK (hot water estimate): [seconds, power*s, radiators*s, radiator seconds]
    hwr: List[List[float]] = field(default_factory=list)

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
                 index: Optional[Dict[str, List[Path]]] = None,
                 hourly: Optional[List[List[float]]] = None) -> Tuple[float, float, float]:
    """Coal, energy and the highest 5 min power of a day; `hourly` (24 x [kg, kWh]) gets the hours."""
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
                    kg = _float(r.get("coal_kg")) or 0.0
                    kwh = _float(r.get("energy_kwh")) or 0.0
                    coal += kg
                    energy += kwh
                    if hourly is not None:
                        hour = datetime.fromtimestamp(ts - 1).hour if ts is not None else int(end[11:13] or 0)
                        hourly[hour][0] += kg
                        hourly[hour][1] += kwh
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
    ign_s, ign_counted = 0.0, False
    work_since: Optional[datetime] = None
    drop: Optional[Dict[str, Any]] = None

    def close_drop(d: Optional[Dict[str, Any]]) -> None:
        if d and (d["last"] - d["start"]).total_seconds() + 30 >= DROP_MIN_S:
            agg.drops.append({
                "start": d["start"].isoformat(timespec="minutes"),
                "minutes": round(((d["last"] - d["start"]).total_seconds() + 30) / 60),
                "min_temp": round(d["min"], 1),
                "setpoint": round(d["setpoint"], 1),
                "power_before": None if d["power_before"] is None else round(d["power_before"]),
                "power_max": None if d["power_max"] is None else round(d["power_max"]),
            })

    recent_power: List[float] = []
    last_settled_power: Optional[float] = None
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
        if mode == "IGNITION":
            ign_s = ign_s + w if prev_mode == "IGNITION" else w
            if ign_s >= IGNITION_MIN_S and not ign_counted:
                agg.ignitions += 1
                ign_counted = True
        else:
            ign_counted = False
        if mode == "WORK" and prev_mode != "WORK":
            # a day that starts in WORK has been working since yesterday - already settled
            first_of_day = prev_mode is None and ts.hour == 0 and ts.minute < 5
            work_since = ts - timedelta(days=1) if first_of_day else ts
        prev_mode = mode

        # after lighting the boiler and the house catch up for ~2 h - not hot water
        if mode == "WORK" and work_since is not None and (ts - work_since).total_seconds() >= HW_SETTLE_S:
            pw, rad = _float(r.get("power")), _float(r.get("temp_grzejnikow"))
            if pw is not None:
                if not agg.hwr:
                    agg.hwr = [[0.0] * 4 for _ in range(24)]
                h = agg.hwr[ts.hour]
                h[0] += w
                h[1] += pw * w
                if rad is not None:
                    h[2] += rad * w
                    h[3] += w

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
        power = values["power"]
        if power is not None:
            recent_power = (recent_power + [power])[-60:]  # ~30 min of rows
        settled = mode == "WORK" and setpoint is not None and work_since is not None \
            and (ts - work_since).total_seconds() >= SETTLE_S
        below = settled and boiler < setpoint - DROP_BELOW_C
        if drop and (not settled or (ts - drop["last"]).total_seconds() > DROP_GAP_S) and not below:
            close_drop(drop)
            drop = None
        if below:
            if drop is None:
                before = recent_power[:-1][-60:]
                drop = {"start": ts, "last": ts, "min": boiler, "setpoint": setpoint, "power_max": power,
                        "power_before": sum(before) / len(before) if before else None}
            drop["last"] = ts
            drop["min"] = min(drop["min"], boiler)
            if power is not None:
                drop["power_max"] = power if drop["power_max"] is None else max(drop["power_max"], power)
        if settled:
            hb = agg.hours[ts.hour]
            hb[0] += w
            hb[1] += boiler * w
            hb[2] += setpoint * w
            if power is not None:
                hb[3] += power * w
                hb[4] += w
            if below:
                hb[5] += w
        if settled and power is not None:
            agg.pw_s += w
            agg.pw_sum += power * w
            agg.pw_sq += power * power * w
            agg.pw_max_s += w if power >= POWER_HIGH else 0.0
            agg.pw_min_s += w if power <= POWER_LOW else 0.0
            if last_settled_power is not None and step <= GAP_S:
                agg.pw_jump += abs(power - last_settled_power)
                agg.pw_jumps += 1
        last_settled_power = power if settled else None
        if settled:
            err = boiler - setpoint
            agg.st_s += w
            agg.st_abs += abs(err) * w
            agg.st_sq += err * err * w
            agg.st_in1 += w if abs(err) <= 1.0 else 0.0
            agg.st_in2 += w if abs(err) <= 2.0 else 0.0
            agg.st_over = max(agg.st_over, err)
            agg.st_under = max(agg.st_under, -err)
    close_drop(drop)
    agg.hwr = [[round(v, 1) for v in h] for h in agg.hwr]   # kept in the day cache - short numbers
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

    pw_days = [d for d in days if d.pw_s > 0]
    pw_s = sum(d.pw_s for d in pw_days)
    power_stats = None
    if pw_s >= 1800:
        mean = sum(d.pw_sum for d in pw_days) / pw_s
        std = math.sqrt(max(0.0, sum(d.pw_sq for d in pw_days) / pw_s - mean * mean))
        jumps = sum(d.pw_jumps for d in pw_days)
        power_stats = {
            "hours": _r(pw_s / 3600),
            "avg_pct": _r(mean, 0),
            "std_pct": _r(std, 1),
            "at_max_pct": _r(100 * sum(d.pw_max_s for d in pw_days) / pw_s, 0),
            "at_min_pct": _r(100 * sum(d.pw_min_s for d in pw_days) / pw_s, 0),
            "jump_pct": _r(sum(d.pw_jump for d in pw_days) / jumps, 2) if jumps else None,
            "high_pct": POWER_HIGH, "low_pct": POWER_LOW,
            "grade": "stabilnie" if std < 12 else "umiarkowanie" if std < 22 else "nerwowo",
        }

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
        "rows": sum(d.rows for d in days),
        "alarms": sum(d.alarms for d in days),
        "regulation": regulation,
        "power_stats": power_stats,
        "efficiency": {
            "degree_days": _r(sum(h for h in (degree_days(d) for d in days) if h is not None), 1),
            "kg_per_degree_day": _r(kg_per_degree_day(days), 3),
            "flue_loss_pct": _r(flue_loss_pct(avg("flue_work")), 1),
        },
        "hourly": hourly_profile(days),
        "drops": drops_list(days),
    }


def _hour_entry(hour: int, t: List[float]) -> Dict[str, Any]:
    secs = t[0]
    return {
        "hour": hour,
        "hours": _r(secs / 3600),
        "boiler": _r(t[1] / secs) if secs >= 600 else None,
        "setpoint": _r(t[2] / secs) if secs >= 600 else None,
        "error": _r((t[1] - t[2]) / secs, 2) if secs >= 600 else None,
        "power": _r(t[3] / t[4]) if t[4] >= 600 else None,
        "below_pct": _r(100 * t[5] / secs) if secs >= 600 else None,
    }


def hourly_profile(days: List[DayAgg]) -> List[Dict[str, Any]]:
    """Per hour of day (settled WORK): average boiler temp, setpoint, error, power, share below the drop line."""
    return [_hour_entry(hour, [sum(d.hours[hour][i] for d in days if len(d.hours) == 24) for i in range(6)])
            for hour in range(24)]


HourCoal = Dict[str, List[List[float]]]   # date -> 24 x [kg, kWh], only for the last two days
HourMin = Dict[str, List[Optional[float]]]  # date -> 24 x lowest boiler temp in WORK, only for the last two days


FINE_STEP_S = 300   # the regulation chart for the last day: 5 min averages, so every dip and jump shows


def fine_series(rows: List[Dict[str, str]], start: datetime, end: datetime,
                fallback_setpoint: Optional[float] = None, step_s: int = FINE_STEP_S) -> List[Dict[str, Any]]:
    """Boiler temperature and setpoint in WORK as step_s averages from start to end (None where it didn't work),
    plus stop=True where the boiler was in STOP (OFF) for any part of the step."""
    n = max(0, int((end - start).total_seconds() // step_s) + 1)
    sums = [[0.0, 0, 0.0, 0, 0.0, 0] for _ in range(n)]   # boiler, setpoint, power: sum and count each
    stopped = [False] * n
    last_stop: Optional[datetime] = None
    for r in rows:
        mode = (r.get("tryb_pracy") or "").upper()
        try:
            ts = datetime.fromisoformat(r.get("data_czas") or "")
        except ValueError:
            continue
        hit = _in_stop(mode, last_stop, ts)
        last_stop = hit or last_stop
        i = int((ts - start).total_seconds() // step_s)
        if not 0 <= i < n:
            continue
        if hit:
            stopped[i] = True
        if mode != "WORK":
            continue
        b = _float(r.get("temp_pieca"))
        if b is None:
            continue
        sums[i][0] += b
        sums[i][1] += 1
        sp = _float(r.get("zadana"))
        if sp is None:
            sp = fallback_setpoint
        if sp is not None:
            sums[i][2] += sp
            sums[i][3] += 1
        pw = _float(r.get("power"))
        if pw is not None:
            sums[i][4] += pw
            sums[i][5] += 1
    out = []
    for i, (bs, bn, ss, sn, ps, pn) in enumerate(sums):
        t = start + timedelta(seconds=i * step_s)
        out.append({"time": t.strftime("%H:%M"), "boiler": round(bs / bn, 1) if bn else None,
                    "setpoint": round(ss / sn, 1) if sn else None, "power": round(ps / pn) if pn else None,
                    "stop": stopped[i]})
    return out


STOP_GAP_S = 120   # rows are 30 s apart: a longer pause between STOP rows starts a new break


STOP_IGNITION_S = 300   # an IGNITION this soon after a STOP row belongs to the same break


def _in_stop(mode: str, last_stop: Optional[datetime], ts: datetime) -> Optional[datetime]:
    """A STOP break: OFF rows and the IGNITION right after them (STOP, coal or ash, start again -
    sometimes with a few WORK rows in between). Returns the time of this row if it is part of a break."""
    if mode == "OFF":
        return ts
    if mode == "IGNITION" and last_stop is not None and (ts - last_stop).total_seconds() <= STOP_IGNITION_S:
        return ts
    return None


def stop_intervals(rows: List[Dict[str, str]], start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Every STOP break between start and end: [{start, end, minutes}], oldest first.
    That's when coal is added or ash is taken out - OFF plus the IGNITION right after it."""
    out: List[Any] = []
    first = last = last_stop = None
    for r in rows:
        try:
            ts = datetime.fromisoformat(r.get("data_czas") or "")
        except ValueError:
            continue
        hit = _in_stop((r.get("tryb_pracy") or "").upper(), last_stop, ts)
        last_stop = hit or last_stop
        if not hit or not start <= ts <= end:
            continue
        # rows of one break: OFF, maybe a moment of WORK, IGNITION - a longer pause starts a new one
        if first is not None and (ts - last).total_seconds() <= STOP_GAP_S:
            last = ts
            continue
        if first is not None:
            out.append((first, last))
        first = last = ts
    if first is not None:
        out.append((first, last))
    # a row stands for its 30 s, so a single row is still half a minute
    return [{"start": a.isoformat(timespec="seconds"), "end": b.isoformat(timespec="seconds"),
             "minutes": max(1, round(((b - a).total_seconds() + 30) / 60))} for a, b in out]


def hour_min_boiler(rows: List[Dict[str, str]]) -> List[Optional[float]]:
    """Lowest boiler temperature in WORK for each hour of a day - a short dip shows even if the hour's average hides it."""
    out: List[Optional[float]] = [None] * 24
    for r in rows:
        b = _float(r.get("temp_pieca"))
        if b is None or (r.get("tryb_pracy") or "").upper() != "WORK":
            continue
        try:
            hour = int((r.get("data_czas") or "")[11:13])
        except ValueError:
            continue
        if 0 <= hour < 24 and (out[hour] is None or b < out[hour]):
            out[hour] = b
    return out


def _day_hour(day: Optional[DayAgg], hour: int, coal: Optional[HourCoal] = None,
              mins: Optional[HourMin] = None) -> Dict[str, Any]:
    t = list(day.hours[hour]) if day is not None and len(day.hours) == 24 else [0.0] * 6
    per_hour = (coal or {}).get(day.date) if day is not None else None
    kg, kwh = per_hour[hour] if per_hour else (0.0, 0.0)
    low = ((mins or {}).get(day.date) or [None] * 24)[hour] if day is not None else None
    return {**_hour_entry(hour, t), "date": day.date if day is not None else None,
            "coal_kg": round(kg, 2), "kwh": round(kwh, 1), "boiler_min": _r(low)}


def hourly_last24(yesterday: Optional[DayAgg], today: Optional[DayAgg], now_hour: int,
                  coal: Optional[HourCoal] = None, mins: Optional[HourMin] = None) -> List[Dict[str, Any]]:
    """The last 24 hours one by one, oldest first: yesterday after the current hour, then today up to now."""
    out = []
    for day, hours in ((yesterday, range(now_hour + 1, 24)), (today, range(0, now_hour + 1))):
        for hour in hours:
            out.append(_day_hour(day, hour, coal, mins))
    return out


def _settled_day(day: DayAgg) -> Dict[str, Any]:
    t = [sum(h[i] for h in day.hours) for i in range(6)] if len(day.hours) == 24 else [0.0] * 6
    e = _hour_entry(0, t)
    return {"setpoint": e["setpoint"], "boiler_work": e["boiler"], "below_pct": e["below_pct"]}


def hours_of_day(day: Optional[DayAgg], coal: Optional[HourCoal] = None,
                 mins: Optional[HourMin] = None) -> List[Dict[str, Any]]:
    """One day hour by hour (0-23)."""
    return [_day_hour(day, hour, coal, mins) for hour in range(24)]


def drops_list(days: List[DayAgg], limit: int = 50) -> Dict[str, Any]:
    """Drops below the setpoint, newest first, and how many started in each hour."""
    all_drops = [x for d in days for x in d.drops]
    by_hour = [0] * 24
    for x in all_drops:
        by_hour[int(x["start"][11:13])] += 1
    return {
        "count": len(all_drops),
        "below_c": DROP_BELOW_C,
        "by_hour": by_hour,
        "items": sorted(all_drops, key=lambda x: x["start"], reverse=True)[:limit],
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
    full = [d for d in days if is_full_day(d) and d.avg("outside") is not None]
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


# ---------------------------------------------------------------- seasons

def _season_month_key(d: date, season_start: date) -> Optional[int]:
    """0..11 = month of the season (0 = the season's first month), None outside the season."""
    months = (d.year - season_start.year) * 12 + d.month - season_start.month
    return months if 0 <= months < 12 else None


def season_months(days: List["DayAgg"], season_start: date) -> List[Dict[str, Any]]:
    """Coal per month of one season (only months with data have values)."""
    out = [{"month": (season_start.month - 1 + i) % 12 + 1, "coal_kg": 0.0, "hours": 0.0} for i in range(12)]
    for x in days:
        k = _season_month_key(date.fromisoformat(x.date), season_start)
        if k is None:
            continue
        out[k]["coal_kg"] += x.coal_kg
        out[k]["hours"] += x.seconds / 3600
    return [{"month": m["month"], "coal_kg": round(m["coal_kg"], 1) if m["hours"] >= 1 else None,
             "hours": round(m["hours"], 1)} for m in out]


def normal_degree_days(start: date, end: date) -> float:
    """Typical degree-days between two dates (end excluded), heating months only."""
    total, d = 0.0, start
    while d < end:
        t = NORMAL_MONTH_C.get(d.month)
        if t is not None:
            total += max(0.0, HDD_BASE_C - t)
        d += timedelta(days=1)
    return total


def season_forecast(this_season: List["DayAgg"], season_start: date, today: date) -> Dict[str, Any]:
    """Coal used so far + coal per degree-day x typical degree-days left until the end of May."""
    used = sum(x.coal_kg for x in this_season)
    full = [x for x in this_season if is_full_day(x)]
    k = kg_per_degree_day(full[-30:])
    end = date(season_start.year + 1, 6, 1)
    left_dd = normal_degree_days(today + timedelta(days=1), end)
    return {
        "used_kg": round(used, 1),
        "kg_per_degree_day": _r(k, 3),
        "degree_days_left": round(left_dd),
        "forecast_kg": round(used + k * left_dd) if k else None,
        "based_on_days": len(full[-30:]),
    }


# ---------------------------------------------------------------- health checks

def _avg_days(days: List[DayAgg], key: str) -> Optional[float]:
    w = sum(d.weights.get(key, 0.0) for d in days)
    return sum(d.sums.get(key, 0.0) for d in days) / w if w > 0 else None


CHECK_DAYS = 3   # the checks look at the last 3 days (today included): recent, but not a single odd day


def checks(period: Dict[str, Any], model: Dict[str, Any], last_full_day: Optional[DayAgg],
           overheat_temp: float = 80.0, trend: Optional[Dict[str, Any]] = None,
           period_label: str = "ostatnie 3 dni") -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []

    # label / value: a short name and a number for the service page tile; detail: the full sentence
    # period: what the number is counted from, shown on the tile
    def add(status: str, title: str, detail: str, label: str = "", value: str = "", over: str = "") -> None:
        out.append({"status": status, "title": title, "detail": detail, "label": label or title, "value": value,
                    "period": over or period_label})

    def pl(v: float, nd: int = 1) -> str:
        return f"{v:.{nd}f}".replace(".", ",")

    hours = period.get("hours") or 0
    if not hours:
        add("info", "Brak danych", "Historia z tego okresu jest pusta.", "Dane", "brak")
        return out

    gap = period.get("gap_hours") or 0
    share = 100 * gap / (hours + gap)
    add("ok" if share < 1 else "warn" if share < 5 else "bad", "Ciągłość danych",
        f"przerwy w zapisie: {gap:.1f} h ({share:.1f}%)" if gap else "bez przerw w zapisie",
        "Zapis danych", f"{pl(100 - share, 0)}%")

    missing = period.get("missing_boiler_rows") or 0
    miss_pct = 100 * missing / max(period.get("rows") or 1, 1)
    add("ok" if miss_pct < 0.5 else "warn" if miss_pct < 5 else "bad", "Czujnik kotła",
        "zawsze odczytany" if missing == 0 else f"brak odczytu w {missing} pomiarach ({miss_pct:.1f}%)",
        "Czujnik kotła", "OK" if missing == 0 else f"−{missing}")

    outside = period["avg"].get("outside")
    add("ok" if outside is not None else "warn", "Temperatura zewnętrzna",
        f"średnio {outside:.1f}°C" if outside is not None else "brak danych pogodowych (moduł Pogoda / internet)",
        "Pogoda", f"{pl(outside)}°" if outside is not None else "brak")

    bmax = period.get("boiler_max")
    if bmax is not None:
        add("bad" if bmax >= overheat_temp else "warn" if bmax >= overheat_temp - 5 else "ok",
            "Przegrzanie kotła", f"najwyżej {bmax:.1f}°C (próg ochrony {overheat_temp:.0f}°C)",
            "Kocioł max", f"{pl(bmax, 0)}°")

    if period.get("alarms"):
        add("bad", "Alarmy", f"{period['alarms']} alarmów w logach - sprawdź Logi (poziom ALARM)", "Alarmy",
            str(period["alarms"]))
    else:
        add("ok", "Alarmy", "brak", "Alarmy", "0")

    ipd = period.get("ignitions_per_day")
    if ipd is not None:
        add("warn" if ipd > 1.5 else "ok", "Rozpalenia",
            f"{period['ignitions']} ({ipd:.1f} na dobę)" + (" - czy piec nie wygasa?" if ipd > 1.5 else ""),
            "Rozpalenia", str(period["ignitions"]))

    reg = period.get("regulation")
    if reg:
        st = "ok" if reg["within_2"] >= 80 else "warn" if reg["within_2"] >= 60 else "bad"
        add(st, "Regulacja temperatury",
            f"{reg['grade']}: {reg['within_2']:.0f}% czasu w ±2°C, średnio {reg['mean_abs_error']:.1f}°C od zadanej",
            "W normie ±2°", f"{reg['within_2']:.0f}%")
    else:
        add("info", "Regulacja temperatury", "za mało pracy w trybie PRACA w tym okresie", "W normie ±2°", "--")

    change = (trend or {}).get("change_pct")
    if change is not None:
        add("warn" if change > 15 else "ok", "Efektywność (kg na stopniodzień)",
            f"ostatni tydzień {trend['recent_kg_per_degree_day']:.2f} vs wcześniej {trend['before_kg_per_degree_day']:.2f} "
            f"({change:+.0f}%)" + (" - kocioł pali więcej przy tej samej pogodzie: czyszczenie?" if change > 15 else ""),
            "Sprawność", f"{change:+.0f}%", "tydzień vs 3 tyg. wcześniej")

    fit = (model.get("coal") or {}).get("fit")
    if fit and last_full_day and last_full_day.avg("outside") is not None and last_full_day.seconds > 0:
        expected = fit["intercept"] + fit["slope"] * last_full_day.avg("outside")
        actual = last_full_day.coal_kg / last_full_day.seconds * 86400
        if expected > 0:
            diff = 100 * (actual - expected) / expected
            add("warn" if diff > 25 else "ok", "Zużycie względem pogody",
                f"{last_full_day.date}: {actual:.1f} kg przy {last_full_day.avg('outside'):.1f}°C, "
                f"oczekiwane ~{expected:.1f} kg ({diff:+.0f}%)" + (" - czas wyczyścić kocioł?" if diff > 25 else ""),
                "Zużycie / pogoda", f"{diff:+.0f}%", f"doba {last_full_day.date[8:10]}.{last_full_day.date[5:7]}")
    return out


# ---------------------------------------------------------------- whole report

# ---------------------------------------------------------------- hot water (CWU) estimate
# No tank sensor: the night (00-05, nobody takes water) is the heating alone. The radiators
# give the house ~ (mean radiator - room)^1.3, so any other hour gets the night's power scaled
# by its radiator supply; what the boiler gave above that went to hot water. Not the outside
# temperature: the curve follows a many-hour average and the house cools slowly - at a warm
# noon the radiators get as much as at night. Counted in 3 h blocks (the PID swings for
# ~1.5 h), a block below the line counts 0. Tank losses at night count as heating.
HW_REF_HOURS = range(0, 5)
HW_SETTLE_S = 2 * 3600       # after lighting, the boiler and the house catch up - not counted
HW_REF_MIN_S = 3 * 3600      # at least 3 h of WORK in the night
HW_HOUR_MIN_S = 45 * 60      # an hour counts with 45 min of WORK
HW_RAD_ZERO_C = 22.0         # rooms ~20 °C + the radiator mean ~2 °C below the supply
HW_RAD_EXP = 1.3             # radiator output ~ (mean - room)^1.3
HW_MIN_RAD_DT = 3.0          # night supply at least 25 °C - lower and the scale is too touchy
HW_BLOCKS = [(5, 8), (8, 11), (11, 14), (14, 17), (17, 20), (20, 23), (23, 24)]


def _night_ref(agg: Optional["DayAgg"]) -> Optional[Tuple[float, float]]:
    """(power %, radiator supply °C) of the night 00-05 in WORK, or None."""
    if agg is None or not agg.hwr:
        return None
    ref_s = sum(agg.hwr[h][0] for h in HW_REF_HOURS)
    ref_rad_s = sum(agg.hwr[h][3] for h in HW_REF_HOURS)
    if ref_s < HW_REF_MIN_S or ref_rad_s < HW_REF_MIN_S:
        return None
    rad_ref = sum(agg.hwr[h][2] for h in HW_REF_HOURS) / ref_rad_s
    if rad_ref - HW_RAD_ZERO_C < HW_MIN_RAD_DT:
        return None
    return sum(agg.hwr[h][1] for h in HW_REF_HOURS) / ref_s, rad_ref


def hot_water_day(agg: "DayAgg") -> Optional[Dict[str, Any]]:
    """{kwh, share, hours[24] (kWh above the heating line)} or None when the day can't tell.
    Only days that worked through their night: on a day the boiler was lit the house is still
    warming up in the evening, which would read as hot water."""
    if not agg.hwr or agg.energy_kwh <= 0:
        return None
    ref = _night_ref(agg)
    if ref is None:
        return None
    p_ref, rad_ref = ref
    # kWh per (% power x hour) of this day - from the coal the feeder gave
    pct_h = sum(h[1] for h in agg.hwr) / 3600
    if pct_h <= 0:
        return None
    kwh_per = agg.energy_kwh / pct_h
    hours = [0.0] * 24
    for a, b in HW_BLOCKS:
        excess, per_hour = 0.0, {}
        for h in range(a, b):
            s_, ps, rs, rn = agg.hwr[h]
            if s_ < HW_HOUR_MIN_S or rn <= 0:
                continue
            rad = rs / rn
            expected = p_ref * (max(0.0, rad - HW_RAD_ZERO_C) / (rad_ref - HW_RAD_ZERO_C)) ** HW_RAD_EXP
            per_hour[h] = (ps / s_ - expected) * s_ / 3600 * kwh_per
            excess += per_hour[h]
        if excess > 0:
            pos = sum(v for v in per_hour.values() if v > 0)
            for h, v in per_hour.items():
                hours[h] = excess * v / pos if v > 0 else 0.0
    kwh = sum(hours)
    return {"kwh": kwh, "share": kwh / agg.energy_kwh, "hours": hours}


def hot_water_summary(days: List["DayAgg"]) -> Optional[Dict[str, Any]]:
    """The estimate over a period: kWh, share of the coal, per day and by hour of day."""
    est = [(d, hot_water_day(d)) for d in days]
    est = [(d, e) for d, e in est if e]
    if not est:
        return None
    kwh = sum(e["kwh"] for _, e in est)
    total = sum(d.energy_kwh for d, _ in est)
    coal = sum(d.coal_kg for d, _ in est)
    n = len(est)
    return {
        "days": n,
        "kwh": round(kwh, 1),
        "kwh_per_day": round(kwh / n, 1),
        "share_pct": round(100 * kwh / total) if total else None,
        "coal_kg_per_day": round(coal * kwh / total / n, 2) if total else None,
        "by_hour": [round(sum(e["hours"][h] for _, e in est) / n, 2) for h in range(24)],
    }


class Analytics:
    def __init__(self, history_dir: Path, stats_dir: Path, events_dir: Path, cache_path: Path) -> None:
        self.history_dir = history_dir
        self.stats_dir = stats_dir
        self.progress: Dict[str, int] = {"done": 0, "total": 0}   # days read in the running build
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
        # days cached before the power stability / hot water fields: the last 30 are read again once
        stale = key in self._cache and ("pw_s" not in self._cache[key] or "hwr" not in self._cache[key]) \
            and d >= today - timedelta(days=30)
        if d < today and key in self._cache and not stale:
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
        prev_start = date(season_start.year - 1, season_start.month, season_start.day)
        first = min(prev_start, today - timedelta(days=29))
        days: List[DayAgg] = []
        unsaved = 0   # past days read from files since the last save (new ones and re-read ones)
        indexes = {
            "history": files_by_day(self.history_dir, "boiler"),
            "stats": files_by_day(self.stats_dir, "stats5m"),
            "events": files_by_day(self.events_dir, "events"),
        }
        d = first
        self.progress = {"done": 0, "total": (today - first).days + 1}
        while d <= today:
            self.progress["done"] = (d - first).days
            cached = self._cache.get(d.isoformat())
            from_files = not (d < today and cached is not None
                              and ("pw_s" in cached or d < today - timedelta(days=30)))
            agg = self.day(d, today, fallback_setpoint, indexes)
            if from_files and pause_s:
                time.sleep(pause_s)
            if from_files and d < today:
                unsaved += 1
            if unsaved >= 10:
                # save the progress: a restart in the middle of a long first
                # build (months of CSV on the Pi) doesn't lose the work done
                self._save_cache()
                unsaved = 0
            if agg.rows or agg.coal_kg:
                days.append(agg)
            d += timedelta(days=1)
        if unsaved:
            self._save_cache()

        def since(start: date) -> List[DayAgg]:
            return [x for x in days if date.fromisoformat(x.date) >= start]

        periods = {
            "today": summarize(since(today)),
            "yesterday": summarize([x for x in days if x.date == (today - timedelta(days=1)).isoformat()]),
            "d3": summarize(since(today - timedelta(days=CHECK_DAYS - 1))),
            "d7": summarize(since(today - timedelta(days=6))),
            "d30": summarize(since(today - timedelta(days=29))),
            "season": summarize(since(season_start)),
        }
        # hot water estimate: today and yesterday on their own, longer periods over their days
        for key, part in (("today", since(today)),
                          ("yesterday", [x for x in days if x.date == (today - timedelta(days=1)).isoformat()]),
                          ("d7", since(today - timedelta(days=6))), ("d30", since(today - timedelta(days=29))),
                          ("season", since(season_start))):
            periods[key]["hot_water"] = hot_water_summary(part)
        by_date = {x.date: x for x in days}
        now_hour = datetime.now().hour if today == date.today() else 23
        # coal and the lowest boiler temp per hour only for the last two days - read on the fly, not cached
        yday = today - timedelta(days=1)
        hour_coal: HourCoal = {}
        hour_min: HourMin = {}
        day_rows: Dict[str, List[Dict[str, str]]] = {}
        for d in (yday, today):
            per_hour = [[0.0, 0.0] for _ in range(24)]
            coal_for_day(self.stats_dir, d, index=indexes["stats"], hourly=per_hour)
            hour_coal[d.isoformat()] = per_hour
            day_rows[d.isoformat()] = history_rows(self.history_dir, d, index=indexes["history"])
            hour_min[d.isoformat()] = hour_min_boiler(day_rows[d.isoformat()])
        # the regulation chart of the last day in 10 min steps
        now_dt = datetime.now() if today == date.today() else datetime.combine(today, datetime.max.time())
        end = now_dt.replace(minute=now_dt.minute - now_dt.minute % (FINE_STEP_S // 60), second=0, microsecond=0)
        both = day_rows[yday.isoformat()] + day_rows[today.isoformat()]
        periods["today"]["fine24"] = fine_series(both, end - timedelta(hours=24) + timedelta(seconds=FINE_STEP_S),
                                                 end, fallback_setpoint)
        y0 = datetime.combine(yday, datetime.min.time())
        periods["yesterday"]["fine"] = fine_series(day_rows[yday.isoformat()], y0,
                                                   y0 + timedelta(seconds=86400 - FINE_STEP_S), fallback_setpoint)
        periods["today"]["stops24"] = stop_intervals(both, end - timedelta(hours=24), end + timedelta(seconds=FINE_STEP_S))
        periods["yesterday"]["stops"] = stop_intervals(day_rows[yday.isoformat()], y0, y0 + timedelta(days=1))
        periods["today"]["last24"] = hourly_last24(
            by_date.get(yday.isoformat()), by_date.get(today.isoformat()), now_hour, hour_coal, hour_min)
        periods["yesterday"]["hours24"] = hours_of_day(by_date.get(yday.isoformat()), hour_coal, hour_min)
        model = weather_model(since(today - timedelta(days=365)))
        full_days = [x for x in days if x.date < today.isoformat() and is_full_day(x)]
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
            "kg_per_degree_day": _r(kg_per_degree_day([x]), 3) if is_full_day(x) else None,
            # regulation per day (settled WORK, like the hourly chart)
            **_settled_day(x),
            "power_work": _r(x.pw_sum / x.pw_s, 0) if x.pw_s >= 1800 else None,
            "power_std": _r(math.sqrt(max(0.0, x.pw_sq / x.pw_s - (x.pw_sum / x.pw_s) ** 2)), 1) if x.pw_s >= 1800 else None,
            "cwu_kwh": _r((hot_water_day(x) or {}).get("kwh"), 1),
        } for x in since(min(season_start, today - timedelta(days=29)))]

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
            "checks": checks(periods["d3"], model, full_days[-1] if full_days else None, overheat_temp, trend,
                             f"3 dni (od {today - timedelta(days=CHECK_DAYS - 1):%d.%m})"),
            "checks_period": f"ostatnie {CHECK_DAYS} dni (od {today - timedelta(days=CHECK_DAYS - 1):%d.%m})",
            "seasons": self._seasons(days, season_start, prev_start, today),
        }

    @staticmethod
    def _seasons(days: List[DayAgg], season_start: date, prev_start: date, today: date) -> Dict[str, Any]:
        this = [x for x in days if x.date >= season_start.isoformat()]
        prev = [x for x in days if prev_start.isoformat() <= x.date < season_start.isoformat()]
        same_day = today.replace(year=today.year - 1) if not (today.month == 2 and today.day == 29) \
            else date(today.year - 1, 2, 28)
        prev_to_date = [x for x in prev if x.date <= same_day.isoformat()]
        return {
            "this": {"start": season_start.isoformat(), "months": season_months(this, season_start),
                     "coal_kg": round(sum(x.coal_kg for x in this), 1)},
            "previous": {"start": prev_start.isoformat(), "months": season_months(prev, prev_start),
                         "coal_kg": round(sum(x.coal_kg for x in prev), 1),
                         # same part of the season a year ago - only if there was data then
                         "coal_kg_to_date": round(sum(x.coal_kg for x in prev_to_date), 1)
                         if sum(x.seconds for x in prev_to_date) >= 86400 else None},
            "forecast": season_forecast(this, season_start, today),
        }


# ---------------------------------------------------------------- forecast

RUNNING_MIN_KG = 3.0       # a day with less coal: the furnace was (mostly) off - not in the model
FORECAST_RECENT_DAYS = 60  # recent days describe the current settings best


def forecast_model(points: List[Dict[str, Any]], today: date) -> Dict[str, Any]:
    """
    Coal per day against the outside temperature, from days the furnace ran
    (weather model points: date, x = outside, y = kg/day). A straight line
    when the days cover enough temperatures, else kg per degree-day.
    """
    running = [p for p in points if p.get("y") is not None and p["y"] >= RUNNING_MIN_KG and p.get("x") is not None]
    recent = [p for p in running if p.get("date", "") >= (today - timedelta(days=FORECAST_RECENT_DAYS)).isoformat()]
    use = recent if len(recent) >= 10 else running
    fit = linear_fit([(p["x"], p["y"]) for p in use])
    if fit and fit["slope"] < 0:
        return {"method": "fit", "intercept": fit["intercept"], "slope": fit["slope"], "days": len(use),
                "r2": fit["r2"]}
    hdd = sum(max(0.0, HDD_BASE_C - p["x"]) for p in use)
    if hdd >= MIN_HDD:
        return {"method": "degree_days", "kg_per_degree_day": sum(p["y"] for p in use) / hdd, "days": len(use)}
    return {"method": None, "days": len(use)}


def coal_per_day_at(model: Dict[str, Any], outside: float) -> Optional[float]:
    if model.get("method") == "fit":
        return max(0.0, model["intercept"] + model["slope"] * outside)
    if model.get("method") == "degree_days":
        return model["kg_per_degree_day"] * max(0.0, HDD_BASE_C - outside)
    return None


def coal_forecast(forecast: Optional[Dict[str, Any]], model: Dict[str, Any], now: datetime,
                  price_pln_per_t: float = 0.0, calorific_mj_per_kg: Optional[float] = None) -> Dict[str, Any]:
    """Coal, cost and heat for the rest of today and the next days, hour by hour from the forecast."""
    if not forecast or not forecast.get("hourly"):
        return {"available": False, "model": model}
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    hours = []
    for h in forecast["hourly"]:
        try:
            t = datetime.fromisoformat(h["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if t < hour_start or h.get("temp") is None:
            continue
        per_day = coal_per_day_at(model, float(h["temp"]))
        hours.append({"time": h["time"], "temp": h["temp"], "code": h.get("code"), "wind": h.get("wind"),
                      "rain_pct": h.get("rain_pct"), "kg": None if per_day is None else per_day / 24})
    daily_info = {d.get("date"): d for d in forecast.get("daily") or []}
    days = []
    for key in sorted({h["time"][:10] for h in hours}):
        part = [h for h in hours if h["time"][:10] == key]
        temps = [h["temp"] for h in part]
        kg = None if any(h["kg"] is None for h in part) else sum(h["kg"] for h in part)
        info = daily_info.get(key) or {}
        kwh = kg * calorific_mj_per_kg / 3.6 if kg is not None and calorific_mj_per_kg else None
        days.append({
            "date": key,
            "hours": len(part),
            "partial": len(part) < 24,
            "tmin": info.get("tmin") if len(part) >= 24 and info.get("tmin") is not None else min(temps),
            "tmax": info.get("tmax") if len(part) >= 24 and info.get("tmax") is not None else max(temps),
            "tavg": round(sum(temps) / len(temps), 1) + 0.0,
            "code": info.get("code") if info.get("code") is not None else part[len(part) // 2].get("code"),
            "wind_max": max((h["wind"] for h in part if h.get("wind") is not None), default=None),
            "rain_pct": max((h["rain_pct"] for h in part if h.get("rain_pct") is not None), default=None),
            "coal_kg": None if kg is None else round(kg, 1),
            "cost_pln": None if kg is None or not price_pln_per_t else round(kg * price_pln_per_t / 1000, 2),
            "energy_kwh": None if kwh is None else round(kwh, 1),
            "power_kw": None if kwh is None else round(kwh / len(part), 2),
        })
    for h in hours:
        h["kg"] = None if h["kg"] is None else round(h["kg"], 3)
    return {"available": True, "model": model, "hours": hours[:48], "days": days,
            "fetched_at": forecast.get("fetched_at"), "location": forecast.get("location")}
