"""Krzywa minimum mocy: the PID's lower limit from the outside temperature, set by hand like the radiator curve."""
import time

import yaml

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.power_floor import PowerFloorModule
from backend.modules.power_work import WorkPowerModule


def _floor(tmp_path, **values):
    d = tmp_path / "floor"
    d.mkdir(exist_ok=True)
    (d / "values.yaml").write_text(yaml.safe_dump(values))
    return PowerFloorModule(base_path=d)


def _pid(tmp_path, **extra):
    d = tmp_path / "pid"
    d.mkdir(exist_ok=True)
    values = dict(boiler_set_temp=56.0, kp=10.0, ki=0.005, min_power=20.0, max_power=93.0,
                  max_slew_rate_percent_per_min=0.0, integral_classic=True)
    values.update(extra)
    (d / "values.yaml").write_text(yaml.safe_dump(values))
    return WorkPowerModule(base_path=d, data_root=d)


def test_the_curve():
    m = PowerFloorModule.__new__(PowerFloorModule)
    from backend.modules.power_floor import PowerFloorConfig
    m._config = PowerFloorConfig(min_at_0=50, slope=1.5, floor_low=20, floor_high=70)
    assert m.floor_at(0) == 50 and m.floor_at(10) == 35 and m.floor_at(-10) == 65
    assert m.floor_at(30) == 20 and m.floor_at(-30) == 70          # within the limits


def test_published_from_the_averaged_outside(tmp_path):
    m = _floor(tmp_path, min_at_0=50, slope=1.5, avg_h=1)
    st = SystemState(mode=BoilerMode.WORK)
    for i, out in enumerate([10.0] * 30 + [0.0] * 30):              # 30 min at 10 C, 30 at 0 C
        st.ts_mono = i * 60.0
        m.tick(now=time.time(), sensors=Sensors(outside_temp=out), system_state=st)
    fl = st.runtime["power_floor"]
    assert fl["outside_avg"] == 5.0 and fl["min"] == 42.5


def test_no_weather_no_floor(tmp_path):
    m = _floor(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    m.tick(now=time.time(), sensors=Sensors(), system_state=st)
    assert st.runtime["power_floor"]["min"] is None
    (tmp_path / "x").mkdir()
    off = _floor(tmp_path / "x", enabled=False)
    st2 = SystemState(mode=BoilerMode.WORK)
    off.tick(now=time.time(), sensors=Sensors(outside_temp=0.0), system_state=st2)
    assert st2.runtime["power_floor"]["min"] is None


def _run_pid(m, st, boiler, minutes, floor=None, t0=0.0, release=3.0):
    t = t0
    out = None
    while t < t0 + minutes * 60:
        st.ts_mono = t
        if floor is not None:
            st.runtime["power_floor"] = {"min": floor, "release_above": release, "ts": t}
        out = m.tick(now=t, sensors=Sensors(boiler_temp=boiler), system_state=st)
        t += 1.0
    return t, out.partial_outputs.power_percent


def test_the_pid_does_not_go_below_the_floor_and_its_integral_does_not_wind_down(tmp_path):
    m = _pid(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    t, p = _run_pid(m, st, 56.8, 60, floor=40.0)                    # a little too warm for an hour
    assert p == 40.0 and st.runtime["power_work"]["min_now"] == 40.0
    integral_held = m._integral
    t, p = _run_pid(m, st, 56.8, 30, floor=40.0, t0=t)
    assert m._integral == integral_held                             # anti-windup at the floor


def test_the_floor_fades_out_well_above_the_setpoint(tmp_path):
    m = _pid(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    _run_pid(m, st, 58.0, 1, floor=40.0)                            # +2: the whole floor
    assert m._min_now == 40.0
    _run_pid(m, st, 59.5, 1, floor=40.0, t0=100)                    # +3.5: half of it
    assert m._min_now == 20.0
    _run_pid(m, st, 60.5, 1, floor=40.0, t0=200)                    # +4.5: gone, the fixed minimum
    assert m._min_now == 20.0


def test_a_stale_or_missing_floor_falls_back_to_the_fixed_minimum(tmp_path):
    m = _pid(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    _run_pid(m, st, 56.5, 1, floor=40.0)
    assert m._min_now == 40.0
    st.runtime["power_floor"]["ts"] = -100.0                        # the module stopped
    st.ts_mono = 200.0
    m.tick(now=200.0, sensors=Sensors(boiler_temp=56.5), system_state=st)
    assert m._min_now == 20.0
    del st.runtime["power_floor"]
    m.tick(now=201.0, sensors=Sensors(boiler_temp=56.5), system_state=st)
    assert m._min_now == 20.0


def test_never_above_max_nor_below_the_fixed_minimum(tmp_path):
    m = _pid(tmp_path, min_power=30.0, max_power=80.0)
    st = SystemState(mode=BoilerMode.WORK)
    _run_pid(m, st, 56.0, 1, floor=95.0)
    assert m._min_now == 80.0
    _run_pid(m, st, 56.0, 1, floor=10.0, t0=100)
    assert m._min_now == 30.0
