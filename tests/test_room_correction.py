"""Korekta pokojowa: every few hours the house average and trend move the curve in steps -
for now only observation (logs + the would-be correction), the curve doesn't use it."""
import time

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.room_correction import RoomCorrectionConfig, RoomCorrectionModule, decide, window_stats

CFG = RoomCorrectionConfig()        # 21.0 ± 0.3, step 0.5, trend 0.1 °C/h


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
    m.set_config_values({"mode": "obserwacja"}, persist=False)
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
    assert [e.data["change"] for e in ev] == [0.5] and rt["offset"] == 0.5 and rt["applied"] is False
    assert "obserwacja" in ev[0].message and "podniósłbym" in ev[0].message
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
    m.set_config_values({"mode": "obserwacja"}, persist=False)
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
    assert m._config.mode == "wyłączona"
    r = m.tick(now=1_000_000.0, sensors=Sensors(), system_state=_state(1_000_000.0, 19.0))
    assert r.runtime == {} and r.events == []
