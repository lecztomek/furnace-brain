import pytest

from backend.core.state import BoilerMode, Event, EventLevel, Outputs
from backend.modules.eventlog.digest import SUMMARY_PERIOD_S, LogDigest


def ev(ts, source, type_, data=None, message="x"):
    return Event(ts=ts, source=source, level=EventLevel.INFO, type=type_, message=message, data=data or {})


def run(d, events, now, mode=BoilerMode.WORK, **outputs):
    return d.process(events, now=now, mode=mode, outputs=Outputs(**outputs))


def test_mixer_movement_is_one_entry():
    d = LogDigest()
    out = run(d, [
        ev(1, "mixer", "MIXER_MOVE", {"direction": "open", "pulse_s": 2, "radiators_temp": 38, "target_temp": 40, "mode": "ramp"}),
        ev(1, "mixer", "MIXER_MOVE_START", {"direction": "open"}),
        ev(3, "mixer", "MIXER_MOVE_STOP", {"direction": "open", "actual_run_s": 2.0, "mode": "ramp",
                                           "radiators_temp": 38, "target_temp": 40}),
    ], now=3)
    assert [e.type for e in out] == ["MIXER_MOVE"]
    assert out[0].message == "Zawór mieszający: otwieranie 2.0 s (CO 38.0°C → zadana 40.0°C, tryb ramp)"


def test_boiler_protection_first_close_and_summary():
    d = LogDigest()
    events = []
    for i in range(5):
        t = 10 * i
        events += [ev(t, "mixer", "MIXER_PROTECT_CLOSE", {"pulse_s": 6, "mode": "protect"}),
                   ev(t, "mixer", "MIXER_MOVE_START", {"direction": "close"}),
                   ev(t + 6, "mixer", "MIXER_MOVE_STOP", {"direction": "close", "actual_run_s": 6.0, "mode": "protect"})]
    events.append(ev(60, "mixer", "MIXER_MODE_CHANGED", {"prev_mode": "protect", "mode": "stabilize"}))
    out = run(d, events, now=60)
    assert [e.type for e in out] == ["MIXER_PROTECT_CLOSE", "MIXER_PROTECT_END", "MIXER_MODE_CHANGED"]
    assert "5 przymknięć" in out[1].message and "30 s" in out[1].message


def test_blower_and_feeder_replaced_by_summary():
    d = LogDigest()
    out = []
    t = 0.0
    while t <= SUMMARY_PERIOD_S:
        events = [ev(t, "blower", "BLOWER_POWER_CHANGED")]
        if t % 60 == 0:
            events += [ev(t, "feeder", "FEEDER_ON"), ev(t + 10, "feeder", "FEEDER_OFF")]
        out += run(d, events, now=t, fan_power=40 if t % 4 == 0 else 0)
        t += 2
    types = [e.type for e in out]
    assert "BLOWER_POWER_CHANGED" not in types and "FEEDER_ON" not in types and "FEEDER_OFF" not in types
    assert types.count("BLOWER_SUMMARY") == 1 and types.count("FEEDER_SUMMARY") == 1
    blower = next(e for e in out if e.type == "BLOWER_SUMMARY")
    feeder = next(e for e in out if e.type == "FEEDER_SUMMARY")
    assert blower.data["on_percent"] == pytest.approx(50, abs=0.5)
    assert blower.data["avg_percent"] == pytest.approx(20, abs=0.5)
    # one 10 s feed per minute over 15 min (the summary closes the window at 900 s)
    assert feeder.data["feeds"] == 16 and feeder.data["feed_s"] == pytest.approx(160)


def test_no_summary_when_idle():
    d = LogDigest()
    out = run(d, [], now=0) + run(d, [], now=SUMMARY_PERIOD_S + 1)
    assert out == []


def test_manual_feeding_logged_right_away():
    d = LogDigest()
    out = run(d, [], now=0, mode=BoilerMode.MANUAL, feeder_on=True)
    out += run(d, [], now=95, mode=BoilerMode.OFF, feeder_on=False)
    assert [e.type for e in out] == ["FEEDER_MANUAL_ON", "FEEDER_MANUAL_OFF"]
    assert "1 min 35 s" in out[1].message


def test_other_events_pass_through():
    d = LogDigest()
    others = [ev(1, "mode", "MODE_CHANGED"), ev(2, "pump_co", "PUMP_CO_ON"), ev(3, "weather", "WEATHER_OK"),
              ev(4, "safety", "SENSOR_BOILER_TEMP_MISSING_ON"), ev(5, "blower", "BLOWER_MODE_CHANGED")]
    assert run(d, others, now=5) == others
