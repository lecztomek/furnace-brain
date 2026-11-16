"""After a long time held at the power floor the PID adds power as soon as the boiler cools
below the setpoint - not only ~3 C below it (prod 16.11.2026: 59 C at the floor 30%, then
the boiler fell to 55 C before the power moved)."""
import yaml

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.power_work import WorkPowerModule


def _module(tmp_path):
    # the prod settings of 16.11.2026
    (tmp_path / "values.yaml").write_text(yaml.safe_dump(dict(
        boiler_set_temp=58.0, kp=10.0, ki=0.005, kd=0.0, min_power=20.0, max_power=93.0,
        max_slew_rate_percent_per_min=5.0, integral_classic=True, flue_brake_gain=0.0)))
    return WorkPowerModule(base_path=tmp_path, data_root=tmp_path)


def _run(m, st, t0, minutes, temp_at, floor=30.0):
    t, log = t0, []
    while t < t0 + minutes * 60:
        st.ts_mono = t
        st.runtime["power_floor"] = {"min": floor, "release_above": 3.0, "ts": t}
        boiler = temp_at(t)
        out = m.tick(now=t, sensors=Sensors(boiler_temp=boiler), system_state=st)
        log.append((t, boiler, out.partial_outputs.power_percent))
        t += 1.0
    return t, log


def test_power_rises_as_soon_as_the_boiler_drops_below_the_setpoint(tmp_path):
    m = _module(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    t, log = _run(m, st, 0.0, 30, lambda t: 59.0)                 # half an hour too warm: at the floor
    assert log[-1][2] == 30.0
    # then it cools 0.2 C/min, like on prod (59 -> 55 in 20 min)
    t, log = _run(m, st, t, 20, lambda tt: 59.0 - 0.2 * (tt - t) / 60)
    first_above = next(b for _, b, p in log if p > 31.0)
    assert first_above > 57.5, first_above                       # was ~55.5 C before
    assert log[-1][2] > 45.0                                      # and keeps adding while it falls


def test_too_warm_it_still_stays_at_the_floor(tmp_path):
    m = _module(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    _, log = _run(m, st, 0.0, 60, lambda t: 59.5)
    assert all(p == 30.0 for _, _, p in log[60:])                 # no extra power while too warm


def test_a_falling_floor_does_not_keep_the_old_power(tmp_path):
    """A warming day: the floor goes 30 -> 22% while the boiler is a little too warm - the power
    follows the floor down, it doesn't stay at 30% (that would overheat the boiler)."""
    m = _module(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    t, _ = _run(m, st, 0.0, 30, lambda t: 58.5, floor=30.0)
    _, log = _run(m, st, t, 30, lambda t: 58.5, floor=22.0)
    assert log[-1][2] == 22.0
    # and back at the setpoint it starts from the new floor, not the old one
    _, log = _run(m, st, t + 1800, 2, lambda t: 58.0, floor=22.0)
    assert abs(log[-1][2] - 22.0) < 1.0
