"""Korekta pokojowa: every few hours the house average and trend move the curve in steps;
the curve adds the correction only while the module is on and fresh."""
import time

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.room_correction import RoomCorrectionConfig, RoomCorrectionModule, decide, window_stats

CFG = RoomCorrectionConfig(deadband=0.3)        # the example from the talk: 21.0 ± 0.3, step 0.5, trend 0.1 °C/h


def test_the_rule_on_the_example_from_the_talk():
    assert decide(20.3, -0.1, CFG)[0] == 1          # too cold, cooling: up
    assert decide(20.5, +0.1, CFG)[0] == 0          # too cold, but warming: wait
    assert decide(20.8, +0.1, CFG)[0] == 0          # fine: stays
    assert decide(21.4, +0.2, CFG)[0] == -1         # too warm, still warming: down
    assert decide(21.2, -0.1, CFG)[0] == 0          # fine
    assert decide(21.5, -0.15, CFG)[0] == 0         # too warm, but cooling: wait
    assert decide(20.5, 0.0, CFG)[0] == 1           # too cold and standing still: up


def test_average_and_trend_of_the_window():
    now = 100_000.0
    samples = [(now - 3 * 3600 + i * 30, 20.0 + 0.3 * i * 30 / 3600) for i in range(361)]   # +0.3 °C/h
    avg, trend = window_stats(samples, now, 3.0)
    assert abs(avg - 20.45) < 0.02 and abs(trend - 0.3) < 0.02
    assert window_stats(samples[-100:], now, 3.0) is None                                   # too few readings


def _state(t, temp, mode=BoilerMode.WORK):
    st = SystemState(mode=mode)
    st.runtime = {"thermometers": {"roles": {"w_domu": {"temp": temp, "valid_until": t + 600}}},
                  "curve": {"curve_active": True}}
    return st


def _run(m, t0, hours, temp_at, mode=BoilerMode.WORK):
    events, t = [], t0
    while t < t0 + hours * 3600:
        r = m.tick(now=t, sensors=Sensors(), system_state=_state(t, temp_at(t), mode))
        events += [e for e in r.events if e.type == "ROOM_CORRECTION"]
        t += 10.0
    return t, r.runtime, events


def _module(tmp_path):
    m = RoomCorrectionModule(base_path=tmp_path, data_root=tmp_path)
    m.set_config_values({"enabled": True}, persist=False)
    for _ in range(200):                                       # no history in a test: [] quickly
        m.tick(now=0.0, sensors=Sensors(), system_state=SystemState())
        if m._refilled:
            break
        time.sleep(0.01)
    return m


def test_too_cold_raises_once_then_waits_while_warming(tmp_path):
    m = _module(tmp_path)
    t0 = 1_000_000.0
    t, rt, ev = _run(m, t0, 3.1, lambda t: 20.3)                     # 3 h at 20.3: up
    assert [e.data["change"] for e in ev] == [0.5] and rt["offset"] == 0.5 and rt["ts"] > t0
    assert "krzywa w górę o 0,5" in ev[0].message
    t, rt, ev = _run(m, t, 3.0, lambda tt: 20.3 + 0.15 * (tt - t) / 3600)   # warming 0.15 °C/h: waits
    assert [e.data["change"] for e in ev] == [0.0] and rt["offset"] == 0.5


def test_fine_stays_and_the_limit_holds(tmp_path):
    m = _module(tmp_path)
    t, rt, ev = _run(m, 1_000_000.0, 15.1, lambda t: 19.0)            # cold for long: up to the limit
    assert rt["offset"] == 2.0 and [e.data["change"] for e in ev] == [0.5, 0.5, 0.5, 0.5, 0.0]
    t, rt, ev = _run(m, t, 6.0, lambda t: 21.1)                       # fine: stays at +2
    assert rt["offset"] == 2.0 and all(e.data["change"] == 0.0 for e in ev)


def test_no_learning_outside_work(tmp_path):
    m = _module(tmp_path)
    _, rt, ev = _run(m, 1_000_000.0, 3.1, lambda t: 19.0, mode=BoilerMode.OFF)
    assert rt["offset"] == 0.0 and "PRACY" in ev[0].data["why"]


def test_a_restart_keeps_the_correction_and_the_time(tmp_path):
    m = _module(tmp_path)
    t, rt, _ = _run(m, 1_000_000.0, 3.1, lambda t: 20.3)
    assert rt["offset"] == 0.5
    m2 = _module(tmp_path)                                           # the controller restarts
    r = m2.tick(now=t, sensors=Sensors(), system_state=_state(t, 20.3))
    assert r.runtime["offset"] == 0.5 and r.runtime["last_decision"] == rt["last_decision"]
    assert not r.events                                              # no decision right after the start


def test_after_a_restart_the_average_comes_from_the_history(tmp_path):
    import datetime as dt
    hist = tmp_path / "modules" / "history" / "data"
    hist.mkdir(parents=True)
    now = time.time()
    lines = ["data_czas;temp_pieca;temp_wewn"]
    for i in range(0, 3 * 3600 + 60, 30):
        ts = dt.datetime.fromtimestamp(now - 3 * 3600 + i)
        lines.append(f"{ts.isoformat(timespec='seconds')};58.0;20.2")
    by_hour = {}
    for line in lines[1:]:
        by_hour.setdefault(line[:13].replace("-", "").replace("T", "_"), []).append(line)
    for h, ls in by_hour.items():
        (hist / f"boiler_{h}.csv").write_text("\n".join([lines[0]] + ls) + "\n", encoding="utf-8")
    m = RoomCorrectionModule(base_path=tmp_path, data_root=tmp_path)
    m.set_config_values({"enabled": True}, persist=False)
    ev = []
    for i in range(300):                                             # the history is read in the background
        r = m.tick(now=now, sensors=Sensors(), system_state=_state(now, 20.2))
        ev += [e for e in r.events if e.type == "ROOM_CORRECTION"]
        if ev:
            break
        time.sleep(0.01)
    assert ev and ev[0].data["change"] == 0.5 and abs(ev[0].data["room_avg"] - 20.2) < 0.01


def test_off_by_default_does_nothing(tmp_path):
    m = RoomCorrectionModule(base_path=tmp_path, data_root=tmp_path)
    assert m._config.enabled is False
    r = m.tick(now=1_000_000.0, sensors=Sensors(), system_state=_state(1_000_000.0, 19.0))
    assert r.runtime == {} and r.events == []


def test_without_readings_for_12_h_it_goes_back_to_0(tmp_path):
    m = _module(tmp_path)
    t, rt, _ = _run(m, 1_000_000.0, 6.1, lambda t: 19.5)            # two steps up: +1.0
    assert rt["offset"] == 1.0
    last, changes = t, []
    st = SystemState(mode=BoilerMode.WORK)
    st.runtime = {"curve": {"curve_active": True}}                  # the thermometer is gone
    while t < last + 24 * 3600:
        r = m.tick(now=t, sensors=Sensors(), system_state=st)
        changes += [((t - last) / 3600, e.data["offset"]) for e in r.events if e.data.get("change")]
        t += 60.0
    assert [o for _, o in changes] == [0.5, 0.0]                      # a step at a time
    assert changes[0][0] >= 12.0 and changes[1][0] - changes[0][0] >= 2.9


def _curve(tmp_path):
    from backend.modules.curve import CurveModule
    import yaml
    (tmp_path / "values.yaml").write_text(yaml.safe_dump(dict(
        curve_enabled=True, curve_at_0=29.5, curve_slope=0.4, curve_min=22.0, curve_max=45.0,
        curve_avg_h=1.0, sun_enabled=False)))
    return CurveModule(base_path=tmp_path)


def _curve_target(m, room, now=2_000_000.0):
    st = SystemState(mode=BoilerMode.WORK)
    st.ts_mono = now
    st.runtime = {"weather": {"temp": 5.0, "valid_until": now + 3600}}
    if room is not None:
        st.runtime["room_correction"] = room
    r = m.tick(now=now, sensors=Sensors(), system_state=st)
    return st.runtime["curve"], r.events


def test_the_curve_adds_the_correction_only_when_on_and_fresh(tmp_path):
    m = _curve(tmp_path)
    now = 2_000_000.0
    rc, _ = _curve_target(m, None, now)
    assert rc["target"] == 27.5 and rc["room_offset"] == 0.0           # no module: as before
    rc, ev = _curve_target(m, {"offset": 1.0, "ts": now}, now)
    assert rc["target"] == 28.5 and rc["room_offset"] == 1.0
    assert any(e.type == "CURVE_ROOM" and "+1,0" in e.message.replace(".", ",") for e in ev)
    rc, _ = _curve_target(m, {"offset": 1.0, "ts": now - 120}, now)     # the module stopped: ignored
    assert rc["target"] == 27.5
    rc, _ = _curve_target(m, {}, now)                                   # switched off: nothing
    assert rc["target"] == 27.5
    rc, _ = _curve_target(m, {"offset": -9.0, "ts": now}, now)          # nonsense: ignored
    assert rc["target"] == 27.5


def test_the_house_in_the_state_for_the_main_page(tmp_path):
    """/api/state/current: the house tile - thermometer, the wanted temperature, the correction."""
    import shutil
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.state_api import create_state_router
    from backend.core.config_store import ConfigStore
    from backend.core.state_store import StateStore
    mdir = tmp_path / "room_correction"
    mdir.mkdir()
    shutil.copy(Path("backend/modules/room_correction/schema.yaml"), mdir / "schema.yaml")
    (mdir / "values.yaml").write_text("enabled: true\nroom_target: 21.5\n", encoding="utf-8")
    store = StateStore()
    now = time.time()
    with store.locked() as st:
        st.runtime["thermometers"] = {"roles": {"w_domu": {"temp": 20.4, "valid_until": now + 600}}}
        st.runtime["curve"] = {"room_offset": 0.5}
    app = FastAPI()
    app.include_router(create_state_router(store, ConfigStore(tmp_path, ["room_correction"])), prefix="/api")
    house = TestClient(app).get("/api/state/current").json()["house"]
    assert house == {"temp": 20.4, "target": 21.5, "correction_on": True, "offset": 0.5}
