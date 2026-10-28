"""Trend: °C per 10 min of the boiler temperature, under it on the main page."""
from backend.core.state import Sensors, SystemState
from backend.modules.trend import TrendModule, slope_per_10m


def run(m, temps, step=2.0, t0=1_800_000_000.0):
    st = SystemState()
    for i, temp in enumerate(temps):
        result = m.tick(now=t0 + i * step, sensors=Sensors(boiler_temp=temp), system_state=st)
    return result.runtime


def test_rising_and_falling(tmp_path):
    # 2 s aux ticks, +0.1 °C a minute = +1.0 per 10 min
    up = run(TrendModule(base_path=tmp_path), [50 + 0.1 * i * 2 / 60 for i in range(400)])
    assert abs(up["boiler_per_10m"] - 1.0) < 0.05
    down = run(TrendModule(base_path=tmp_path), [56 - 0.2 * i * 2 / 60 for i in range(400)])
    assert abs(down["boiler_per_10m"] + 2.0) < 0.05


def test_sensor_steps_dont_make_a_trend(tmp_path):
    # DS18B20 flicking between two steps - flat
    flat = run(TrendModule(base_path=tmp_path), [56.0 if i % 7 < 3 else 56.0625 for i in range(400)])
    assert abs(flat["boiler_per_10m"]) < 0.1


def test_nothing_until_enough_readings(tmp_path):
    assert run(TrendModule(base_path=tmp_path), [50.0, 51.0, 52.0])["boiler_per_10m"] is None
    # lost sensor: no readings, no trend
    assert run(TrendModule(base_path=tmp_path), [None] * 400)["boiler_per_10m"] is None


def test_slope():
    assert slope_per_10m([(0, 1.0), (300, 1.5), (600, 2.0)]) == 1.0
    assert slope_per_10m([(0, 1.0)]) is None
