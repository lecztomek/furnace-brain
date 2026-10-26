"""Current heat output on the main page: coal fed in the last 10 min x calorific value."""
from backend.modules.stats import StatsConfig, StatsModule


def _module(tmp_path, **cfg):
    return StatsModule(base_path=tmp_path, config=StatsConfig(**cfg), data_root=tmp_path)


def test_power_from_feeder_time(tmp_path):
    m = _module(tmp_path, feeder_kg_per_hour=10.0, calorific_mj_per_kg=29.0)
    t = 1000.0
    for i in range(2400):  # 20 min of 0.5 s ticks, feeder on 25 % of the time
        t += 0.5
        m._track_recent_feed(t, 0.5, feeder_on=(i % 40) < 10)
    # 10 kg/h * 25 % * 29 MJ/kg / 3.6 = 20.1 kW
    assert abs(m._power_kw_now() - 20.14) < 0.3
    assert abs(m._coal_kgph_now() - 2.5) < 0.05  # 10 kg/h * 25 %


def test_unknown_at_start_and_without_calorific(tmp_path):
    m = _module(tmp_path, feeder_kg_per_hour=10.0, calorific_mj_per_kg=29.0)
    m._track_recent_feed(1000.0, 0.5, True)
    assert m._power_kw_now() is None  # no full feeder cycle yet
    m2 = _module(tmp_path, feeder_kg_per_hour=10.0, calorific_mj_per_kg=0.0)
    for i in range(600):
        m2._track_recent_feed(1000.0 + i * 0.5, 0.5, True)
    assert m2._power_kw_now() is None


def test_feeder_off_goes_to_zero(tmp_path):
    m = _module(tmp_path, feeder_kg_per_hour=10.0, calorific_mj_per_kg=29.0)
    t = 0.0
    for _ in range(1200):
        t += 0.5
        m._track_recent_feed(t, 0.5, True)
    for _ in range(1300):  # > 10 min off
        t += 0.5
        m._track_recent_feed(t, 0.5, False)
    assert m._power_kw_now() == 0.0


def test_steady_feeding_gives_a_steady_value(tmp_path):
    """Feeding 8 s every 77 s: the value doesn't jump when a portion enters/leaves a window."""
    m = _module(tmp_path, feeder_kg_per_hour=10.0, calorific_mj_per_kg=29.0)
    t, values = 0.0, []
    for i in range(int(40 * 60 / 0.5)):  # 40 min
        t += 0.5
        m._track_recent_feed(t, 0.5, (t % 77.0) < 8.0)
        if t > 20 * 60:
            values.append(m._power_kw_now())
    expected = 10.0 * 8 / 77 * 29 / 3.6
    assert abs(min(values) - expected) < 0.2 and abs(max(values) - expected) < 0.2
