"""
Furnace scenario tests on the simulator (see tests/furnace_sim.py).
Settings come from values.yaml.sample, the rest from schema defaults.
"""
import logging

import pytest

from backend.core.state import BoilerMode, Sensors
from furnace_sim import FurnaceSim

MIN = 60.0


@pytest.fixture(autouse=True)
def quiet_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def boiler_set_temp(sim: FurnaceSim) -> float:
    return float(sim.kernel._modules["power_work"]._config.boiler_set_temp)


def test_cold_ignition_switches_to_work(tmp_path):
    sim = FurnaceSim(tmp_path)
    sim.set_mode(BoilerMode.IGNITION)

    assert sim.run_until(lambda s: s.mode == BoilerMode.WORK, max_seconds=45 * MIN), (
        f"still in IGNITION after 45 min, boiler={sim.state.sensors.boiler_temp:.1f}°C"
    )
    assert not sim.alarms
    assert not sim.module_errors


def test_cold_start_in_work_reaches_setpoint(tmp_path):
    # e.g. backend restart with a cold boiler - the backend starts in WORK
    sim = FurnaceSim(tmp_path)
    sim.set_mode(BoilerMode.WORK)
    target = boiler_set_temp(sim)

    sim.run(10 * MIN, sample_every_s=1)
    first = sim.samples
    assert sum(s.power for s in first) / len(first) > 50, "power stays low with a cold boiler"

    assert sim.run_until(lambda s: s.state.sensors.boiler_temp >= target - 3, max_seconds=30 * MIN)
    assert not sim.alarms
    assert not sim.module_errors


def test_work_holds_temperature(tmp_path):
    sim = FurnaceSim(tmp_path)
    sim.set_mode(BoilerMode.WORK)
    target = boiler_set_temp(sim)

    sim.run(60 * MIN)
    sim.run(60 * MIN, sample_every_s=10)
    temps = [s.boiler_temp for s in sim.samples_between(60 * MIN, 120 * MIN)]

    assert abs(sum(temps) / len(temps) - target) < 4
    assert min(temps) > target - 10
    assert max(temps) < target + 8
    assert not sim.alarms


def test_boiler_overheat_stops_feeding_and_raises_alarm(tmp_path):
    sim = FurnaceSim(tmp_path)
    sim.set_mode(BoilerMode.WORK)
    sim.run(15 * MIN)

    sim.sensor_override = lambda s: Sensors(**{**s.__dict__, "boiler_temp": 93.0})
    sim.run(2 * MIN, sample_every_s=1)

    assert any(e.type == "BOILER_OVERHEAT_ON" for e in sim.alarms)
    last = sim.samples[-60:]
    assert not any(s.feeder_on for s in last)
    assert all(s.fan_power == 0 for s in last)


def test_missing_boiler_sensor_stops_feeding(tmp_path):
    sim = FurnaceSim(tmp_path)
    sim.set_mode(BoilerMode.WORK)
    sim.run(15 * MIN)

    sim.sensor_override = lambda s: Sensors(**{**s.__dict__, "boiler_temp": None})
    sim.run(2 * MIN, sample_every_s=1)

    assert sim.events_of("SENSOR_BOILER_TEMP_MISSING_ON")
    last = sim.samples[-60:]
    assert not any(s.feeder_on for s in last)
    assert all(s.fan_power == 0 for s in last)
    assert all(s.pump_co_on for s in last)


def test_off_stops_feeder_and_fan(tmp_path):
    sim = FurnaceSim(tmp_path)
    sim.set_mode(BoilerMode.WORK)
    sim.run(20 * MIN)

    sim.set_mode(BoilerMode.OFF)
    sim.run(2 * MIN, sample_every_s=1)

    last = sim.samples[-60:]
    assert not any(s.feeder_on for s in last)
    assert all(s.fan_power == 0 for s in last)


def test_work_settles_on_the_setpoint(tmp_path):
    """Classic integral: the boiler settles on the setpoint, not ~1-2 C below (leaky integral)."""
    sim = FurnaceSim(tmp_path)
    sim.set_mode(BoilerMode.WORK)
    target = boiler_set_temp(sim)

    sim.run(120 * MIN)
    sim.run(120 * MIN, sample_every_s=30)
    temps = [s.boiler_temp for s in sim.samples_between(120 * MIN, 240 * MIN)]
    assert abs(sum(temps) / len(temps) - target) < 0.6
