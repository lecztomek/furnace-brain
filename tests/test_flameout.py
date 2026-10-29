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
    for i, (hm, boiler, power, flue) in enumerate(rows):
        for k in range(6):
            t = i * 60.0 + k * 10.0
            st.ts_mono = t
            st.outputs.power_percent = power
            res = m.tick(now=t, sensors=Sensors(boiler_temp=boiler, flue_gas_temp=flue), system_state=st)
            if any(e.type == "FLAMEOUT" for e in res.events):
                return hm, st, res.events
    return None, st, []


def test_early_stop_on_the_real_nights():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "data" / "flameout_prod.json").read_text())
    at, st, events = _replay(data["29.10 wygasł (zasyp)"])
    # the coal hung up: full power from ~05:06, the flue kept falling - stopped ~05:22 (the old rule: 07:13)
    assert at is not None and "05:18" <= at <= "05:26", at
    assert st.mode == BoilerMode.OFF and st.runtime["flameout"]["early"] is True
    assert "zasyp" in events[0].message
    for name in ("28.10 23:20 dołek, ogień wrócił", "28.10 18:00 dołek, ogień wrócił"):
        assert _replay(data[name])[0] is None, name                    # deep dips at full power, the fire came back


def test_fire_coming_back_cancels_the_early_stop():
    # fading at 93 % for 9 min, then the flue rises 1 C/min (like 28.10 23:10-23:20): no stop
    flue = lambda t: 50.0 - t / 60 if t < 9 * 60 else 41.0 + (t - 9 * 60) / 60
    _, st, events, _ = _run(30, boiler=lambda t: 50.0, flue=flue)
    assert "FLAMEOUT" not in events and st.mode == BoilerMode.WORK


def test_broken_flue_sensor_with_the_boiler_rising_is_not_stopped():
    # the flue reading falls, but the boiler rises at full power - something burns
    _, st, events, _ = _run(30, boiler=lambda t: 48.0 + t / 300, flue=lambda t: 50.0 - t / 60)
    assert "FLAMEOUT" not in events and st.mode == BoilerMode.WORK


def test_early_stop_can_be_switched_off():
    m = FlameoutModule(base_path=__import__("pathlib").Path("/nonexistent"))
    m._apply({"early_enabled": False})
    _, st, events, _ = _run(30, boiler=lambda t: 50.0, flue=lambda t: 50.0 - t / 60, m=m)
    assert "FLAMEOUT" not in events and st.mode == BoilerMode.WORK
