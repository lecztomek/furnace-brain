"""Classic PID integral: it must not wind up while the boiler is already heading for the setpoint."""
import yaml

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.power_work import WorkPowerModule


def _module(tmp_path):
    (tmp_path / "values.yaml").write_text(yaml.safe_dump(dict(
        boiler_set_temp=59.0, kp=4.5, ki=0.015, min_power=20.0, max_power=93.0,
        max_slew_rate_percent_per_min=5.0, integral_classic=True)))
    return WorkPowerModule(base_path=tmp_path, data_root=tmp_path)


def _run(m, st, t0, minutes, temp_at):
    t = t0
    while t < t0 + minutes * 60:
        st.ts_mono = t
        m.tick(now=t, sensors=Sensors(boiler_temp=temp_at(t)), system_state=st)
        t += 1.0
    return t


def test_no_windup_while_rising(tmp_path):
    m = _module(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    # 10 min standing 5 C too cold: the integral builds up
    t = _run(m, st, 0.0, 10, lambda t: 54.0)
    built = m._integral
    assert built > 0
    # 20 min rising 0.25 C/min towards the setpoint: the fire is catching up, no more integral
    t = _run(m, st, t, 20, lambda tt: 54.0 + 0.25 * (tt - t) / 60)
    assert m._integral <= built * 1.15     # only the first ~minute, until the trend shows


def test_integrates_while_standing_still(tmp_path):
    m = _module(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    t = _run(m, st, 0.0, 10, lambda t: 57.0)
    first = m._integral
    _run(m, st, t, 10, lambda t: 57.0)
    assert m._integral > first * 1.5      # stuck below the setpoint: the integral keeps pulling
