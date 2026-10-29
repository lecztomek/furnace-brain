"""Flame-out: stop feeding when the fire is out in WORK - and only then."""
from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.flameout import FlameoutModule


def _run(minutes, boiler, power=93.0, flue=lambda t: 30.0, mode=BoilerMode.WORK, m=None, st=None, t0=0.0):
    m = m or FlameoutModule(base_path=__import__("pathlib").Path("/nonexistent"))
    st = st or SystemState(mode=mode)
    events = []
    t = t0
    while t < t0 + minutes * 60:
        st.ts_mono = t
        st.outputs.power_percent = power
        res = m.tick(now=t, sensors=Sensors(boiler_temp=boiler(t), flue_gas_temp=flue(t)), system_state=st)
        events += [e.type for e in res.events]
        if st.mode != BoilerMode.WORK:
            break
        t += 10.0
    return m, st, events, t


def test_dead_fire_stops_the_feeder():
    m, st, events, t = _run(60, boiler=lambda t: 39.0 - t / 3600, flue=lambda t: 30.0 - t / 600)
    assert "FLAMEOUT" in events and st.mode == BoilerMode.OFF
    assert 29 * 60 <= t <= 32 * 60                     # after the 30 min window
    assert st.runtime["flameout"]["boiler_temp"] < 40


def test_boiler_coming_back_is_left_alone():
    _, st, events, _ = _run(60, boiler=lambda t: 35.0 + t / 600)   # +1 C every 10 min
    assert "FLAMEOUT" not in events and st.mode == BoilerMode.WORK


def test_rising_flue_means_something_burns():
    _, st, events, _ = _run(60, boiler=lambda t: 38.0, flue=lambda t: 30.0 + t / 300)   # +6 C in 30 min
    assert "FLAMEOUT" not in events and st.mode == BoilerMode.WORK


def test_warm_boiler_low_power_or_ignition_are_not_flameouts():
    for kw in (dict(boiler=lambda t: 45.0), dict(boiler=lambda t: 38.0, power=50.0),
               dict(boiler=lambda t: 30.0, mode=BoilerMode.IGNITION)):
        _, st, events, _ = _run(60, **kw)
        assert "FLAMEOUT" not in events


def test_alarm_clears_when_started_again():
    m, st, _, t = _run(60, boiler=lambda t: 38.0)
    assert st.runtime["flameout"]
    st.mode = BoilerMode.IGNITION
    st.ts_mono = t + 10
    m.tick(now=t + 10, sensors=Sensors(boiler_temp=38.0), system_state=st)
    assert st.runtime["flameout"] == {}


def test_problems_for_the_warning_sign(tmp_path):
    """Flame-out, overheating and a missing sensor come in /api/state/current for the main page."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.state_api import create_state_router
    from backend.core.config_store import ConfigStore
    from backend.core.state_store import StateStore
    store = StateStore()
    with store.locked() as st:
        st.runtime["flameout"] = {"at": 1793100000.0, "boiler_temp": 38.5, "power": 93}
        st.runtime["overheat"] = {"boiler": True, "hopper": False}
        st.runtime["safety"] = {"missing": ["hopper_temp"]}
    app = FastAPI()
    app.include_router(create_state_router(store, ConfigStore(tmp_path, [])), prefix="/api")
    problems = TestClient(app).get("/api/state/current").json()["problems"]
    assert [p["short"] for p in problems] == ["WYGASŁ", "GORĄCO", "CZUJNIK"]
    assert "38.5" in problems[0]["text"] and "podajnika" in problems[2]["text"]


def _replay(rows):
    """Prod history, one row a minute: (HH:MM, boiler, power, flue) - fed every 10 s."""
    m = FlameoutModule(base_path=__import__("pathlib").Path("/nonexistent"))
    st = SystemState(mode=BoilerMode.WORK)
    warned = []
    for i, (hm, boiler, power, flue) in enumerate(rows):
        for k in range(6):
            t = i * 60.0 + k * 10.0
            st.ts_mono = t
            st.outputs.power_percent = power
            res = m.tick(now=t, sensors=Sensors(boiler_temp=boiler, flue_gas_temp=flue), system_state=st)
            warned += [hm for e in res.events if e.type == "FLAMEOUT_WARNING"]
    return warned, st


def test_early_warning_on_the_real_nights():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "data" / "flameout_prod.json").read_text())
    warned, st = _replay(data["29.10 wygasł (zasyp)"])
    # the coal hung up: full power from ~05:06, the flue kept falling - warned once, ~05:11 (the alarm came at 07:13)
    assert len(warned) == 1 and "05:09" <= warned[0] <= "05:14", warned
    assert st.mode == BoilerMode.WORK                                  # only a warning, nothing stopped
    assert st.runtime["flameout"]["warning"]["flue"] < 55
    for name in ("28.10 23:20 dołek, ogień wrócił", "28.10 18:00 dołek, ogień wrócił"):
        assert _replay(data[name])[0] == [], name                      # deep dips at full power, the fire came back


def test_the_warning_goes_when_the_flue_rises_again():
    m, st, events, t = _run(12, boiler=lambda t: 50.0, flue=lambda t: 50.0 - t / 60)       # -1 C/min at 93 %
    assert "FLAMEOUT_WARNING" in events and st.runtime["flameout"].get("warning")
    _, st, events, _ = _run(10, boiler=lambda t: 50.0, flue=lambda t: 38.0 + (t - 720) / 30, m=m, st=st, t0=720)
    assert "FLAMEOUT_WARNING" not in events and not st.runtime["flameout"].get("warning")
