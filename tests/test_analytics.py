from datetime import date, datetime, timedelta

import pytest

from backend.core.analytics import Analytics, aggregate_day, linear_fit, summarize
from backend.core.demo_history import seed


def rows(day, spec):
    """spec: list of (seconds_from_midnight, mode, boiler, setpoint, outside)"""
    out = []
    for s, mode, boiler, setpoint, outside in spec:
        ts = datetime.combine(day, datetime.min.time()) + timedelta(seconds=s)
        out.append({"data_czas": ts.isoformat(timespec="seconds"), "tryb_pracy": mode, "temp_pieca": str(boiler),
                    "power": "50", "zadana": "" if setpoint is None else str(setpoint), "temp_zewn": str(outside)})
    return out


def test_regulation_ignores_first_20_min_of_work():
    day = date(2026, 1, 10)
    spec = [(s, "WORK", 40 if s < 1200 else 57.5, 57, 0) for s in range(0, 7200, 30)]
    agg = aggregate_day(day, rows(day, spec))
    reg = summarize([agg])["regulation"]
    assert reg["within_1"] == 100 and reg["mean_abs_error"] == 0.5
    assert reg["max_over"] == 0.5 and reg["max_under"] == 0.0


def test_ignitions_and_gaps():
    day = date(2026, 1, 10)
    # a 30 s click on IGNITION isn't an ignition; 6 min of IGNITION is
    spec = [(0, "WORK", 57, 57, 0), (30, "IGNITION", 50, 57, 0), (60, "WORK", 50, 57, 0)]
    spec += [(90 + 30 * i, "IGNITION", 45, 57, 0) for i in range(12)]
    spec += [(450, "WORK", 57, 57, 0), (960, "WORK", 57, 57, 0)]
    agg = aggregate_day(day, rows(day, spec))
    assert agg.ignitions == 1
    assert agg.gap_s == 480  # 450 -> 960 s pause


def test_linear_fit_needs_enough_spread():
    assert linear_fit([(1, 10), (1.5, 9), (2, 8), (2.5, 7), (3, 6)]) is None   # 2 °C spread
    fit = linear_fit([(-5, 30), (0, 25), (5, 20), (10, 15), (15, 10)])
    assert fit["slope"] == -1.0 and fit["intercept"] == 25.0 and fit["r2"] == 1.0


def test_report_on_demo_data(tmp_path):
    seed(tmp_path, 12)
    m = tmp_path / "modules"
    a = Analytics(m / "history" / "data", m / "stats" / "data", m / "eventlog" / "data", m / "stats" / "data" / "cache.json")
    today = date.today()
    report = a.report(today, today - timedelta(days=60), 57.0)

    d7 = report["periods"]["d7"]
    assert d7["days"] == 6 and 50 < d7["coal_kg"] < 250
    assert d7["regulation"]["within_2"] > 60
    fit = report["weather"]["coal"]["fit"]
    assert fit and fit["slope"] < 0  # colder -> more coal
    assert {c["title"] for c in report["checks"]} >= {"Ciągłość danych", "Regulacja temperatury"}
    # cached days give the same result
    again = Analytics(m / "history" / "data", m / "stats" / "data", m / "eventlog" / "data", m / "stats" / "data" / "cache.json")
    assert again.report(today, today - timedelta(days=60), 57.0)["periods"] == report["periods"]


def test_efficiency_metrics():
    from backend.core.analytics import DayAgg, flue_loss_pct, kg_per_degree_day

    assert flue_loss_pct(120) == pytest.approx(7.5)        # (120-20) * (0.68/10 + 0.007)
    day = DayAgg(date="2026-01-10", seconds=86400, coal_kg=30.0)
    day.sums["outside"], day.weights["outside"] = 3.0 * 86400, 86400   # mean 3 °C -> 15 degree-days
    assert kg_per_degree_day([day]) == pytest.approx(2.0)
    warm = DayAgg(date="2026-01-11", seconds=86400, coal_kg=5.0)
    warm.sums["outside"], warm.weights["outside"] = 17.0 * 86400, 86400  # 1 degree-day only
    assert kg_per_degree_day([warm]) is None                          # too few degree-days


def test_damaged_file_with_nul_bytes(tmp_path):
    """A power cut leaves NUL bytes in a CSV - rows around them are still read."""
    from backend.core.analytics import history_rows
    p = tmp_path / "boiler_20261025_10.csv"
    p.write_bytes(b"data_czas;temp_pieca\n2026-10-25T10:00:00;55\n\x00\x00\x00\x002026-10-25T10:00:30;56\n")
    rows = history_rows(tmp_path, date(2026, 10, 25))
    assert [r["temp_pieca"] for r in rows] == ["55", "56"]


def test_hourly_profile_and_drops():
    """Evening dip below setpoint - 4 C is listed as a drop and shows in the hour profile."""
    from backend.core.analytics import aggregate_day, hourly_profile, drops_list
    rows = []
    t = datetime(2026, 1, 10, 18, 0, 0)
    while t < datetime(2026, 1, 10, 22, 0, 0):
        temp = 56.0 if not (datetime(2026, 1, 10, 20, 10) <= t < datetime(2026, 1, 10, 20, 40)) else 51.0
        rows.append({"data_czas": t.isoformat(), "temp_pieca": str(temp), "power": "60", "tryb_pracy": "WORK", "zadana": "57"})
        t += timedelta(seconds=30)
    day = aggregate_day(date(2026, 1, 10), rows)
    assert len(day.drops) == 1
    d = day.drops[0]
    assert d["start"] == "2026-01-10T20:10" and d["minutes"] == 30 and d["min_temp"] == 51.0
    prof = hourly_profile([day])
    assert prof[20]["below_pct"] == 50.0 and prof[19]["below_pct"] == 0.0
    assert prof[19]["error"] == -1.0
    assert drops_list([day])["by_hour"][20] == 1


def test_last24_runs_from_yesterday_to_now():
    from backend.core.analytics import DayAgg, hourly_last24
    y, t = DayAgg(date="2026-10-26"), DayAgg(date="2026-10-27")
    y.hours[20] = [3600.0, 3600 * 55.0, 3600 * 59.0, 3600 * 40.0, 3600.0, 0.0]
    t.hours[13] = [3600.0, 3600 * 58.0, 3600 * 59.0, 3600 * 50.0, 3600.0, 0.0]
    out = hourly_last24(y, t, now_hour=13)
    assert len(out) == 24
    assert (out[0]["date"], out[0]["hour"]) == ("2026-10-26", 14)
    assert (out[-1]["date"], out[-1]["hour"]) == ("2026-10-27", 13)
    assert next(h for h in out if h["hour"] == 20)["boiler"] == 55.0
    assert out[-1]["boiler"] == 58.0 and out[-1]["error"] == -1.0
    assert hourly_last24(None, t, now_hour=13)[0]["boiler"] is None   # no data for yesterday


def test_seasons_and_forecast():
    from backend.core.analytics import DayAgg, season_forecast, season_months
    start = date(2026, 9, 1)
    days = []
    for i in range(20):
        d = DayAgg(date=(date(2026, 10, 1) + timedelta(days=i)).isoformat(), seconds=86400, coal_kg=10.0)
        d.sums["outside"], d.weights["outside"] = 8.0 * 86400, 86400   # 10 degree-days a day
        days.append(d)
    months = season_months(days, start)
    assert months[0]["month"] == 9 and months[0]["coal_kg"] is None      # no data in September
    assert months[1]["month"] == 10 and months[1]["coal_kg"] == 200.0
    f = season_forecast(days, start, date(2026, 10, 20))
    assert f["used_kg"] == 200.0 and f["kg_per_degree_day"] == 1.0
    assert f["forecast_kg"] == 200 + f["degree_days_left"] and f["degree_days_left"] > 2500
