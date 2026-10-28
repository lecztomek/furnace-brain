"""Overheat protection: the boiler and the hopper are guarded separately - a missing reading
of one sensor must not switch off the protection of the other."""
import yaml

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.overheat import OverheatModule


def _module(tmp_path):
    (tmp_path / "values.yaml").write_text(yaml.safe_dump(dict(
        enabled=True, boiler_trip_temp=66.0, boiler_hysteresis=4.5,
        hopper_trip_temp=60.0, hopper_hysteresis=4.5, hopper_purge_minutes=5.0)))
    return OverheatModule(base_path=tmp_path)


def _tick(m, st, t, boiler, hopper):
    st.ts_mono = t
    return m.tick(now=t, sensors=Sensors(boiler_temp=boiler, hopper_temp=hopper), system_state=st)


def test_normal_temperatures_force_nothing(tmp_path):
    m = _module(tmp_path)
    out = _tick(m, SystemState(mode=BoilerMode.WORK), 1.0, 56.0, 28.0).partial_outputs
    assert out.pump_co_on is None and out.feeder_on is None and out.fan_power is None


def test_boiler_overheat_works_without_the_hopper_sensor(tmp_path):
    m = _module(tmp_path)
    r = _tick(m, SystemState(mode=BoilerMode.WORK), 100.0, 67.0, None)    # hopper sensor dropped out
    out = r.partial_outputs
    assert out.pump_co_on and out.pump_cwu_on and out.fan_power == 0 and out.feeder_on is False
    assert out.mixer_open_on is True                                        # heat away to the radiators
    assert any(e.type == "BOILER_OVERHEAT_ON" for e in r.events)
    assert any(e.type == "OVERHEAT_MISSING_SENSOR" and "podajnika" in e.message for e in r.events)


def test_hopper_overheat_works_without_the_boiler_sensor(tmp_path):
    m = _module(tmp_path)
    out = _tick(m, SystemState(mode=BoilerMode.WORK), 1.0, None, 62.0).partial_outputs
    assert out.feeder_on is True and out.fan_power == 0 and out.pump_co_on      # pushes the fire back
    assert out.mixer_open_on is False


def test_an_alarm_does_not_end_when_the_sensor_drops_out(tmp_path):
    m = _module(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    _tick(m, st, 1.0, 67.0, 28.0)
    out = _tick(m, st, 2.0, None, 28.0).partial_outputs                         # boiler reading lost mid-alarm
    assert out.pump_co_on and out.fan_power == 0 and out.feeder_on is False
    r = _tick(m, st, 3.0, 61.0, 28.0)                                           # back and cooled below 61.5
    assert r.partial_outputs.pump_co_on is None
    assert any(e.type == "BOILER_OVERHEAT_OFF" for e in r.events)


def test_purge_runs_its_minutes_even_if_the_hopper_reading_flickers(tmp_path):
    m = _module(tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    _tick(m, st, 0.0, 56.0, 62.0)
    assert _tick(m, st, 60.0, 56.0, None).partial_outputs.feeder_on is True    # 1 min in, sensor gone
    assert _tick(m, st, 240.0, 56.0, 61.0).partial_outputs.feeder_on is True   # 4 min
    assert _tick(m, st, 301.0, 56.0, 61.0).partial_outputs.feeder_on is False  # 5 min over, still hot: fan off, no feed


def test_overheat_opens_the_valve_even_without_the_radiator_sensor(tmp_path):
    """The safety module (ticks after overheat) blocks the valve without a radiator reading -
    but not during a boiler overheat: the heat must get out of the boiler."""
    from backend.modules.safety import SafetyModule
    (tmp_path / "o").mkdir()
    (tmp_path / "s").mkdir()
    overheat = _module(tmp_path / "o")
    safety = SafetyModule(base_path=tmp_path / "s")
    st = SystemState(mode=BoilerMode.WORK)

    def step(t, boiler):
        st.ts_mono = t
        sensors = Sensors(boiler_temp=boiler, hopper_temp=28.0, radiators_temp=None, flue_gas_temp=60.0)
        a = overheat.tick(now=t, sensors=sensors, system_state=st).partial_outputs
        b = safety.tick(now=t, sensors=sensors, system_state=st).partial_outputs
        return b.mixer_open_on if b.mixer_open_on is not None else a.mixer_open_on

    assert step(1.0, 56.0) is False          # normal work without the radiator sensor: the valve stays put
    assert step(2.0, 67.0) is True           # boiler overheat: open anyway
    assert step(3.0, 61.0) is False          # overheat over: blocked again
