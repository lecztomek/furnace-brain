"""Flue brake: the flue gas warms up 10-20 min before the boiler - power is taken off ahead of an overshoot."""
import yaml

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.power_work import WorkPowerModule


def _module(tmp_path, **extra):
    values = dict(boiler_set_temp=56.0, kp=10.0, ki=0.005, min_power=20.0, max_power=93.0,
                  max_slew_rate_percent_per_min=0.0, integral_classic=True)
    values.update(extra)
    (tmp_path / "values.yaml").write_text(yaml.safe_dump(values))
    return WorkPowerModule(base_path=tmp_path, data_root=tmp_path)


def _run(m, st, t0, minutes, flue_at, boiler=55.0):
    t = t0
    out = None
    while t < t0 + minutes * 60:
        st.ts_mono = t
        out = m.tick(now=t, sensors=Sensors(boiler_temp=boiler, flue_gas_temp=flue_at(t)), system_state=st)
        t += 1.0
    return t, out.partial_outputs.power_percent


def test_off_by_default(tmp_path):
    m = _module(tmp_path)
    assert m._config.flue_brake_gain == 0.0
    st = SystemState(mode=BoilerMode.WORK)
    _run(m, st, 0.0, 15, lambda t: 60.0 + t / 60)           # flue rising 1 C/min
    assert m._brake == 0.0


def test_rising_flue_takes_power_off_and_falling_adds(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = _module(tmp_path / "a")
    braked = _module(tmp_path / "b", flue_brake_gain=8.0)
    st = SystemState(mode=BoilerMode.WORK)
    # boiler 5 C below the setpoint: the PID asks ~50%, well inside min..max
    _, p0 = _run(plain, SystemState(mode=BoilerMode.WORK), 0.0, 15, lambda t: 60.0 + t / 60, boiler=51.0)
    _, p1 = _run(braked, st, 0.0, 15, lambda t: 60.0 + t / 60, boiler=51.0)
    assert braked._flue_rate > 0.9                           # ~1 C/min once smoothed
    assert -8.5 < braked._brake < -7.0 and p1 < p0 - 6       # 8 points less while the fire builds up
    # the fire fades: the flue falls 1 C/min - power is added
    _run(braked, st, 15 * 60.0, 20, lambda t: 75.0 - (t - 900) / 60, boiler=51.0)
    assert 7.0 < braked._brake < 8.5


def test_brake_is_limited(tmp_path):
    m = _module(tmp_path, flue_brake_gain=30.0, flue_brake_max=10.0)
    _run(m, SystemState(mode=BoilerMode.WORK), 0.0, 15, lambda t: 60.0 + 2 * t / 60)
    assert m._brake == -10.0


def test_no_flue_reading_lets_go(tmp_path):
    m = _module(tmp_path, flue_brake_gain=8.0)
    st = SystemState(mode=BoilerMode.WORK)
    t, _ = _run(m, st, 0.0, 15, lambda t: 60.0 + t / 60)
    assert m._brake < -5
    _run(m, st, t, 1, lambda t: None)                         # sensor lost
    assert m._brake == 0.0 and m._flue_rate == 0.0


def test_trend_starts_over_on_entering_work(tmp_path):
    m = _module(tmp_path, flue_brake_gain=8.0)
    st = SystemState(mode=BoilerMode.WORK)
    t, _ = _run(m, st, 0.0, 15, lambda t: 60.0 + t / 60)
    st.mode = BoilerMode.IGNITION                              # STOP / ignition: the flue jumps around
    t, _ = _run(m, st, t, 10, lambda t: 150.0)
    st.mode = BoilerMode.WORK
    _run(m, st, t, 0.5, lambda t: 70.0)                        # back in WORK at 70 C: no jump read as a trend
    assert m._brake == 0.0


def test_settings_from_the_gui(tmp_path):
    m = _module(tmp_path)
    data = yaml.safe_load((tmp_path / "values.yaml").read_text())
    data.update(flue_brake_gain=6, flue_brake_max=15)
    (tmp_path / "values.yaml").write_text(yaml.safe_dump(data))
    m.reload_config_from_file()
    assert m._config.flue_brake_gain == 6.0 and m._config.flue_brake_max == 15.0


def test_a_bad_reading_does_not_jerk_the_power(tmp_path):
    m = _module(tmp_path, flue_brake_gain=8.0)
    st = SystemState(mode=BoilerMode.WORK)
    t, _ = _run(m, st, 0.0, 15, lambda t: 65.0)                 # steady fire
    t, _ = _run(m, st, t, 0.5, lambda t: 85.0)                  # one glitch: +20 C in 30 s
    t, _ = _run(m, st, t, 0.5, lambda t: 65.0)                  # and back
    assert abs(m._brake) < 0.5
