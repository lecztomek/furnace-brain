"""Service page: tuning advice from the history, setting changes and the endpoints."""
import math
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.config_api import create_config_router
from backend.api.service_api import LoopStats, create_service_router
from backend.core import tuning as T
from backend.core.config_store import ConfigStore
from backend.core.state_store import StateStore

START = datetime(2026, 11, 10, 0, 0, 0)
SETTINGS = {
    "power_work": {"boiler_set_temp": 56.0, "kp": 10.0, "ki": 0.005, "min_power": 20.0, "max_power": 93.0},
    "feeder": {"feed_on_base_s": 8.0, "feed_off_base_s": 56.0, "min_power_to_feed": 6.0},
    "blower": {"base_fan_percent": 28.0, "min_power_to_blow": 15.0},
    "curve": {"curve_enabled": False, "target_temp": 30.0},
    "mode": {"switch_temp": 54.0},
    "pump_co": {"boiler_on_temp": 40.0},
    "overheat": {"enabled": True, "boiler_trip_temp": 66.0},
    "flameout": {"enabled": True},
}


def work_rows(hours, boiler, power, start=START, mode="WORK", setpoint=56.0):
    """30 s rows; boiler / power are functions of the minute."""
    out = []
    for i in range(int(hours * 120)):
        m = i / 2
        out.append({"ts": start + timedelta(seconds=30 * i), "mode": mode, "boiler": boiler(m),
                    "power": power(m), "rad": 30.0, "outside": 0.0, "setpoint": setpoint})
    return out


def advice(rows, settings=SETTINGS):
    steady = T.settled(rows, 56.0)
    reg = T.regulation(steady, 20, 93)
    items = T.advise(reg, T.radiators(steady, settings["curve"]), T.ignitions(rows, 56.0),
                     T.event_summary([]), settings)
    return {i["area"]: i for i in items}, reg


def test_steady_furnace_gets_no_suggestions():
    rows = work_rows(12, lambda m: 56 + 0.4 * math.sin(m / 10), lambda m: 50)
    items, reg = advice(rows)
    assert reg["hours"] == 10.0               # the first 2 h after entering WORK are skipped
    assert items["Całka (Ki)"]["status"] == "ok"
    assert items["Wzmocnienie (Kp)"]["status"] == "ok"
    assert items["Ślimak"]["status"] == "ok"
    assert T.verdict(list(items.values()))["status"] == "ok"


def test_too_little_coal_suggests_a_longer_feed_and_more_air():
    rows = work_rows(12, lambda m: 53.5, lambda m: 93)
    items, _ = advice(rows)
    assert items["Ślimak"]["status"] == "bad"
    assert "8 → 9 s" in items["Ślimak"]["tip"]
    assert items["Całka (Ki)"]["status"] == "ok"          # not the integral's fault
    assert "28 → 32%" in items["Dmuchawa"]["tip"]        # air in the same proportion


def test_fast_swing_blames_kp_slow_swing_blames_ki():
    fast = work_rows(12, lambda m: 56 + 4.5 * math.sin(2 * math.pi * m / 30), lambda m: 50)
    items, reg = advice(fast)
    assert 25 <= reg["period_min"] <= 35
    assert items["Wzmocnienie (Kp)"]["status"] == "bad"
    assert "10 → 8" in items["Wzmocnienie (Kp)"]["tip"]

    slow = work_rows(14, lambda m: 56 + 4.5 * math.sin(2 * math.pi * m / 120), lambda m: 50)
    items, reg = advice(slow)
    assert reg["period_min"] >= 100
    assert items["Całka (Ki)"]["status"] == "bad"
    assert "0,005 → 0,0035" in items["Całka (Ki)"]["tip"]
    assert items["Wzmocnienie (Kp)"]["status"] == "ok"


def test_overshoot_after_ignition_but_not_after_a_click_on_a_hot_boiler():
    ign = work_rows(1, lambda m: 30 + m * 0.4, lambda m: 100, mode="IGNITION")
    after = work_rows(2, lambda m: 54 + 6 * math.sin(math.pi * m / 60), lambda m: 40,
                      start=ign[-1]["ts"] + timedelta(seconds=30))
    found = T.ignitions(ign + after, 56.0)
    assert len(found) == 1 and found[0]["peak_over"] > 3

    click = work_rows(0.2, lambda m: 63, lambda m: 30, mode="IGNITION")
    hot = work_rows(2, lambda m: 63, lambda m: 20, start=click[-1]["ts"] + timedelta(seconds=30))
    assert T.ignitions(click + hot, 56.0) == []


def test_setting_change_compared_before_and_after():
    swinging = work_rows(14, lambda m: 56 + 2.5 * math.sin(2 * math.pi * m / 30), lambda m: 50)
    calm = work_rows(14, lambda m: 56 + 0.3 * math.sin(m / 10), lambda m: 50,
                     start=swinging[-1]["ts"] + timedelta(seconds=30))
    change_at = calm[0]["ts"]
    events = [{"type": "SETTINGS_CHANGED", "data_czas": change_at.isoformat(),
               "data_json": '{"module": "power_work", "changes": {"kp": [10.0, 8.0], "state_dir": ["a", "b"]}}'}]
    changes = T.setting_changes(events)
    assert [c["label"] for c in changes] == ["Kp"]       # only the tuning settings
    steady = T.settled(swinging, 56.0) + [dict(r, err=r["boiler"] - 56.0) for r in calm]
    cmp = T.compare_change(change_at, steady, 20, 93)
    assert cmp["verdict"] == "better"


def _config_dir(tmp_path):
    root = Path(__file__).resolve().parents[1] / "backend" / "modules"
    cfg = tmp_path / "cfg"
    for mid in ("power_work", "feeder", "blower"):
        (cfg / mid).mkdir(parents=True)
        shutil.copy(root / mid / "schema.yaml", cfg / mid / "schema.yaml")
    return cfg


def test_settings_change_is_logged_with_old_and_new_value(tmp_path):
    store = StateStore()
    cfg = ConfigStore(_config_dir(tmp_path), ["power_work", "feeder", "blower"])
    app = FastAPI()
    app.include_router(create_config_router(cfg, publish_events=store.publish_events), prefix="/api")
    client = TestClient(app)
    assert client.put("/api/config/value/power_work/ki", json=0.015).status_code == 200
    assert client.put("/api/config/value/power_work/ki", json=0.005).status_code == 200
    assert client.put("/api/config/value/power_work/ki", json=0.005).status_code == 200   # no change, no entry
    events, _, _ = store.events_since(0)
    changed = [e for e in events if e.type == "SETTINGS_CHANGED"]
    assert changed[-1].data["changes"] == {"ki": [0.015, 0.005]}
    assert "ki 0,015 → 0,005" in changed[-1].message
    assert len(changed) == 2


def test_service_endpoints(tmp_path):
    store = StateStore()
    cfg = ConfigStore(_config_dir(tmp_path), ["power_work", "feeder", "blower"])
    stats = {"control": LoopStats(0.5), "aux": LoopStats(2.0)}
    stats["control"].add(0.01)
    stats["control"].add(0.7)
    app = FastAPI()
    app.include_router(create_service_router(store, cfg, tmp_path, {"version": "9.9.9", "started_at": 1.0}, stats,
                                             warm_up_s=None), prefix="/api")
    client = TestClient(app)
    first = client.get("/api/service/tuning").json()          # never waits for the analysis
    assert "live" in first
    deadline = time.time() + 10
    tuning = first
    while "verdict" not in tuning and time.time() < deadline:
        time.sleep(0.05)
        tuning = client.get("/api/service/tuning").json()
    assert tuning["verdict"]["status"] in ("ok", "warn", "bad")
    assert "pid" in tuning["live"] and "feeder" in tuning["live"]
    diag = client.get("/api/service/diagnostics").json()
    loop = [i for g in diag["groups"] for i in g["items"] if i["label"] == "Pętla sterowania"][0]
    assert loop["status"] == "warn" and "spóźniona 1×" in loop["value"]


def test_past_days_are_read_once(tmp_path, monkeypatch):
    hist, ev = tmp_path / "h", tmp_path / "e"
    hist.mkdir()
    ev.mkdir()
    now = datetime(2026, 11, 20, 18, 0)
    for back in range(9):
        day = (now - timedelta(days=back)).date()
        with open(hist / f"boiler_{day:%Y%m%d}.csv", "w") as f:
            f.write("data_czas;temp_pieca;power;temp_grzejnikow;temp_spalin;tryb_pracy;temp_zewn;dmuchawa;slimak;zadana\n")
            for i in range(0, 2880, 10):
                ts = datetime.combine(day, datetime.min.time()) + timedelta(seconds=30 * i)
                f.write(f"{ts.isoformat()};56.2;50;30;80;WORK;2;28;0;56\n")
    report = T.TuningReport(hist, ev)
    calls = []
    real = T.history_rows
    monkeypatch.setattr(T, "history_rows", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    report.build(SETTINGS, now)
    assert len(calls) == 8                       # 7 days back + today
    calls.clear()
    second = report.build(SETTINGS, now)
    assert calls == [now.date()]                 # only today again
    assert second["regulation"]["hours"] > 10


def test_diagnostics_show_how_long_the_analyses_took(tmp_path):
    from backend.core import timings
    timings.reset()
    with timings.measure("tuning"):
        time.sleep(0.02)
    try:
        with timings.measure("forecast"):
            raise OSError("no internet")
    except OSError:
        pass
    store = StateStore()
    cfg = ConfigStore(_config_dir(tmp_path), ["power_work", "feeder", "blower"])
    app = FastAPI()
    app.include_router(create_service_router(store, cfg, tmp_path, {}, {}), prefix="/api")
    diag = TestClient(app).get("/api/service/diagnostics").json()
    items = {i["label"]: i for g in diag["groups"] if g["title"] == "Analizy w tle" for i in g["items"]}
    assert items["Strojenie"]["status"] == "ok" and "trwało" in items["Strojenie"]["value"] and "ms" in items["Strojenie"]["value"]
    assert items["Prognoza pogody"]["status"] == "warn" and "błąd" in items["Prognoza pogody"]["note"]
    assert items["Statystyki"]["value"] == "jeszcze nie liczone od startu"
    timings.reset()


def test_changelog_sections_newest_first(tmp_path):
    from backend.api.service_api import parse_changelog
    text = "# Changelog\n\nintro\n\n## 1.37 · 28.10.2026\n- One.\n- Two that\n  wraps.\n\n## 1.36\n- Old.\n"
    out = parse_changelog(text)
    assert [v["version"] for v in out] == ["1.37", "1.36"] and out[0]["date"] == "28.10.2026"
    assert out[0]["items"] == ["One.", "Two that wraps."]
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    app = FastAPI()
    cfg = ConfigStore(_config_dir(tmp_path), ["power_work", "feeder", "blower"])
    app.include_router(create_service_router(StateStore(), cfg, tmp_path, {"version": "1.37.2"}, {}, changelog_path=path),
                       prefix="/api")
    body = TestClient(app).get("/api/service/changelog").json()
    assert body["current"] == "1.37.2" and body["versions"][1]["items"] == ["Old."]


def test_the_real_changelog_parses():
    from backend.api.service_api import CHANGELOG_PATH, parse_changelog
    versions = parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8"))
    assert versions and all(v["items"] for v in versions)


def test_versions_get_their_commit_and_tag(tmp_path):
    import subprocess
    from backend.api import service_api
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-q")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "First (1.0.0)")
    git("tag", "v1.0.0")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "Second (1.0.1)")
    service_api._commits_cache.clear()
    c = service_api.version_commits(repo)
    assert c["1.0.0"]["tag"] == "v1.0.0" and len(c["1.0.0"]["hash"]) >= 7
    assert "tag" not in c["1.0.1"]
    service_api._commits_cache.clear()
    assert service_api.version_commits(tmp_path / "no-git") == {}


def test_setpoint_change_overshoot_is_not_judged():
    # 59 °C for 4 h, then 56 °C: the hot boiler overshoots for ~2 h, then holds ±1.3 °C with a 90 min wave
    before = work_rows(4, lambda m: 59 + 0.3 * math.sin(m / 10), lambda m: 50, setpoint=59.0)
    after = work_rows(12, lambda m: 56 + (8 * (1 - m / 120) if m < 120 else 1.8 * math.sin(2 * math.pi * m / 90)),
                      lambda m: 50, start=before[-1]["ts"] + timedelta(seconds=30))
    items, reg = advice(before + after)
    assert reg["max_over"] < 2.5                     # the overshoot after the change is left out
    assert 1.0 < reg["std"] < 1.5                     # a mild wave - fine for a coal bed
    assert items["Całka (Ki)"]["status"] == "ok"
    assert items["Wzmocnienie (Kp)"]["status"] == "hint"   # only a hint to soften it


def test_mild_regular_wave_gets_a_hint_not_a_problem():
    # like prod 28.10: ±1.4 °C, a wave every ~75 min
    rows = work_rows(14, lambda m: 56 + 2.0 * math.sin(2 * math.pi * m / 75), lambda m: 60)
    items, reg = advice(rows)
    assert 1.0 < reg["std"] < 1.5
    kp = items["Wzmocnienie (Kp)"]
    # the flue lead is off: switch it on first, Kp and Ki stay
    assert kp["status"] == "hint" and "wyprzedzanie spalinami (0 → 5)" in kp["tip"] and "Kp" not in kp["tip"]
    assert T.verdict(list(items.values()))["status"] == "ok"      # not counted as a problem

    def kp_with(gain):
        settings = {**SETTINGS, "power_work": {**SETTINGS["power_work"], "flue_brake_gain": gain}}
        steady = T.settled(rows, 56.0)
        found = T.advise(T.regulation(steady, 20, 93), None, [], T.event_summary([]), settings)
        return [i for i in found if i["area"] == "Wzmocnienie (Kp)"][0]
    assert "(5 → 8)" in kp_with(5.0)["tip"]                        # on, but weak: more of it
    soft = kp_with(8.0)                                             # on and strong: then soften the PID
    assert "Kp 10 → 8" in soft["tip"] and "Ki 0,005 → 0,004" in soft["tip"]


def test_curve_power_reserve():
    curve = {"curve_enabled": True, "curve_at_0": 32.0, "curve_slope": 0.45, "curve_min": 22.0, "curve_max": 45.0,
             "curve_avg_h": 6.0}
    reg = {"power_avg": 67, "hours": 10}
    rad = {"rad_avg": 30.0, "outside_avg": 5.0, "mean": 0.0, "mean_abs": 0.4, "within_1": 90}
    items = T.advise(None, rad, [], T.event_summary([]), {**SETTINGS, "curve": curve})
    # PID is not judged without reg - the curve needs the power: nothing about the reserve
    assert [i for i in items if i["area"] == "Krzywa"][0]["status"] == "info"

    steady = T.settled(work_rows(12, lambda m: 56.0, lambda m: 67), 56.0)
    reg = T.regulation(steady, 20, 93)
    items = T.advise(reg, rad, [], T.event_summary([]), {**SETTINGS, "curve": curve})
    k = [i for i in items if i["area"] == "Krzywa"]
    # -10 °C: 36.5 °C on the radiators, (14.5 / 8)^1.3 x 67% = ~145% - over 93%: a hint, not a problem
    assert k[0]["status"] == "hint" and "0,45 → 0,35" in k[0]["tip"] and "145%" in k[0]["detail"]
    assert len(k) == 1

    gentle = {**curve, "curve_slope": 0.15, "curve_avg_h": 12.0}
    calm = T.regulation(T.settled(work_rows(12, lambda m: 56.0, lambda m: 50), 56.0), 20, 93)
    k = [i for i in T.advise(calm, rad, [], T.event_summary([]), {**SETTINGS, "curve": gentle}) if i["area"] == "Krzywa"]
    assert len(k) == 1 and k[0]["status"] == "ok"
    assert T.verdict([i for i in items if i["status"] == "hint"])["status"] == "ok"


def test_after_a_pid_change_only_the_work_since_then_counts(tmp_path):
    import json as _json
    # 12 h of a wave, then the flue lead switched on and 2 h of calm work
    wave = work_rows(12, lambda m: 56 + 2.0 * math.sin(2 * math.pi * m / 75), lambda m: 60)
    change_at = wave[-1]["ts"] + timedelta(seconds=30)
    calm = work_rows(2, lambda m: 56.0, lambda m: 60, start=change_at)
    ev = [{"type": "SETTINGS_CHANGED", "data_czas": change_at.isoformat(),
           "data_json": _json.dumps({"module": "power_work", "changes": {"flue_brake_gain": [0, 5]}})}]
    changes = T.setting_changes(ev)
    assert changes[0]["label"] == "wyprzedzanie spalinami"
    pid = next(c for c in changes if (c["module"], c["key"]) in T.PID_KEYS)
    steady = [r for r in T.settled(wave + calm, 56.0) if r["ts"] >= change_at + timedelta(seconds=T.CHANGE_SKIP_S)]
    items = T.advise(T.regulation(steady, 20, 93), None, [], T.event_summary([]),
                     {**SETTINGS, "power_work": {**SETTINGS["power_work"], "flue_brake_gain": 5.0}}, pid)
    first = items[0]
    # 1.5 h after the change is too little: no suggestions from yesterday's wave
    assert first["title"] == "Za mało pracy od zmiany ustawień" and "0 → 5" in first["detail"]
    assert not any(i["area"] == "Wzmocnienie (Kp)" for i in items)


def test_changes_from_the_settings_history_and_version_updates():
    entries = [
        {"ts": "2026-11-09T10:00:00", "version": "1.44.6", "changed": {}},
        {"ts": "2026-11-09T12:00:00", "version": "1.44.6", "changed": {"power_work": {"kp": [10.0, 8.0]}}},
        {"ts": "2026-11-09T18:00:00", "version": "1.45.0", "changed": {}},                       # an update
        {"ts": "2026-11-09T19:00:00", "version": "1.45.0", "changed": {"blower": {"cycle_time_s": [40, 30]}}},  # not tracked
    ]
    found = T.log_changes(entries)
    assert [(c["label"], c["old"], c["new"]) for c in found] == [("wersja programu", "1.44.6", "1.45.0"), ("Kp", 10.0, 8.0)]
    # the same Kp change from the events log as well: shown once
    ev = [{"ts": datetime(2026, 11, 9, 12, 1), "module": "power_work", "key": "kp", "label": "Kp", "old": 10.0, "new": 8.0}]
    merged = T.merge_changes(ev, found)
    assert len([c for c in merged if c["key"] == "kp"]) == 1 and len(merged) == 2
