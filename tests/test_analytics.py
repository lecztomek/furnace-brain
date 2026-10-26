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
    spec = [(0, "WORK", 57, 57, 0), (30, "OFF", 50, 57, 0), (60, "IGNITION", 45, 57, 0),
            (90, "WORK", 57, 57, 0), (600, "WORK", 57, 57, 0)]
    agg = aggregate_day(day, rows(day, spec))
    assert agg.ignitions == 1
    assert agg.gap_s == 480  # 90 -> 600 s pause


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
