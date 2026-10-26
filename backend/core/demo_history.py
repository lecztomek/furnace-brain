"""
Demo data for the cloud test environment (simulator only): writes N past days
of history, coal buckets and a few events, so the statistics page has
something to show right after a (data-wiping) restart. Enabled with
FURNACE_BRAIN_DEMO_DAYS=<days>; never used on the real furnace.
"""
from __future__ import annotations

import csv
import math
import random
from datetime import date, datetime, timedelta
from pathlib import Path

HISTORY_HEADER = ["data_czas", "temp_pieca", "power", "temp_grzejnikow", "temp_spalin", "tryb_pracy",
                  "temp_zewn", "dmuchawa", "slimak", "zadana"]
STATS_HEADER = ["ts_end_iso", "ts_end_unix", "seconds", "coal_kg", "energy_kwh", "burn_kgph", "power_kw"]
FEEDER_KG_PER_HOUR = 12.0
SETPOINT = 57.0


def seed(run_root: Path, days: int, seed_value: int = 7) -> None:
    rng = random.Random(seed_value)
    hist_dir = run_root / "modules" / "history" / "data"
    stats_dir = run_root / "modules" / "stats" / "data"
    hist_dir.mkdir(parents=True, exist_ok=True)
    stats_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    for back in range(days, 0, -1):
        _seed_day(rng, today - timedelta(days=back), back, hist_dir, stats_dir)


def _seed_day(rng: random.Random, day: date, back: int, hist_dir: Path, stats_dir: Path) -> None:
    day_mean = 3.0 + 6.0 * math.sin(back / 6.0) + rng.gauss(0, 2.0)  # varied weather
    day_factor = rng.gauss(1.0, 0.08)  # windy / sunny days etc.
    off_at = rng.randrange(6, 20) * 3600 if rng.random() < 0.2 else None  # sometimes the fire went out
    boiler = SETPOINT
    hist_rows = {}
    bucket = {"coal": 0.0, "n": 0, "power": 0.0}
    stats_rows = {}

    for step in range(0, 86400, 30):
        ts = datetime.combine(day, datetime.min.time()) + timedelta(seconds=step)
        hour = step / 3600
        outside = day_mean + 3.0 * math.sin((hour - 9) / 24 * 2 * math.pi) + rng.gauss(0, 0.3)

        if off_at is not None and off_at <= step < off_at + 3600:
            mode, power = "OFF", 0.0
            boiler += (25 - boiler) * 0.004
        elif off_at is not None and off_at + 3600 <= step < off_at + 3600 + 2400:
            mode, power = "IGNITION", 90.0
            boiler = min(boiler + 0.25, SETPOINT + 3)
        else:
            mode = "WORK"
            power = max(10.0, min(100.0, (10 + 2.6 * (18 - outside)) * day_factor + rng.gauss(0, 6)))
            target = SETPOINT + 1.2 * math.sin(step / 1500.0)
            boiler += (target - boiler) * 0.08 + rng.gauss(0, 0.35)

        feeder = power * 0.16
        fan = 0.0 if power <= 0 else min(100.0, power * 0.5 + 20)
        radiators = 22 + 0.7 * max(0.0, 18 - outside) + rng.gauss(0, 0.4) if mode != "OFF" else 20.0
        flue = 60 + power * 1.4 + rng.gauss(0, 6) if mode != "OFF" else 35.0

        hist_rows.setdefault(ts.strftime("%Y%m%d_%H"), []).append([
            ts.isoformat(timespec="seconds"), round(boiler, 2), round(power, 1), round(radiators, 2),
            round(flue, 1), mode, round(outside, 1), round(fan, 1), round(feeder, 1), SETPOINT,
        ])

        bucket["coal"] += feeder / 100 * FEEDER_KG_PER_HOUR * 30 / 3600
        bucket["power"] += power
        bucket["n"] += 1
        if bucket["n"] == 10:  # 5 min
            end = ts + timedelta(seconds=30)
            kwh = bucket["coal"] * 29 / 3.6  # like the stats module: energy in the fuel
            stats_rows.setdefault(end.strftime("%Y%m%d_%H"), []).append([
                end.isoformat(timespec="seconds"), f"{end.timestamp():.3f}", "300.000000",
                f"{bucket['coal']:.6f}", f"{kwh:.6f}", f"{bucket['coal'] * 12:.6f}", f"{kwh * 12:.6f}",
            ])
            bucket = {"coal": 0.0, "n": 0, "power": 0.0}

    for key, rows in hist_rows.items():
        _write(hist_dir / f"boiler_{key}.csv", HISTORY_HEADER, rows)
    for key, rows in stats_rows.items():
        _write(stats_dir / f"stats5m_{key}.csv", STATS_HEADER, rows)


def _write(path: Path, header, rows) -> None:
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerows(rows)
