"""Heating curve: the radiators target follows the outside temperature averaged over hours."""
import yaml

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.mixer import MixerModule


def _module(tmp_path, **values):
    (tmp_path / "values.yaml").write_text(yaml.safe_dump({"target_temp": 30.0, **values}))
    return MixerModule(base_path=tmp_path)


def _tick(m, t, outside, st=None):
    st = st or SystemState(mode=BoilerMode.WORK)
    st.ts_mono = t
    res = m.tick(now=t, sensors=Sensors(boiler_temp=60.0, radiators_temp=30.0, outside_temp=outside), system_state=st)
    return st.runtime["mixer"], [e.type for e in res.events or []]


def test_off_by_default_keeps_fixed_target(tmp_path):
    m = _module(tmp_path)
    rt, _ = _tick(m, 0.0, -10.0)
    assert rt["target"] == 30.0 and not rt["curve_active"]


def test_curve_target_and_clamps(tmp_path):
    m = _module(tmp_path, curve_enabled=True)
    assert m.curve_target(0.0) == 30.0
    assert m.curve_target(-10.0) == 34.5
    assert m.curve_target(20.0) == 22.0      # min
    assert m.curve_target(-50.0) == 45.0     # max


def test_averaged_over_hours(tmp_path):
    m = _module(tmp_path, curve_enabled=True, curve_avg_h=6.0)
    types = []
    for i in range(6 * 60):                  # 6 h at 0 C, one sample a minute
        types += _tick(m, i * 60.0, 0.0)[1]
    assert "MIXER_CURVE_ON" in types
    # a frosty hour moves the target only a little (6 h average)
    for i in range(60):
        rt, _ = _tick(m, (360 + i) * 60.0, -12.0)
    assert -2.5 < rt["outside_avg"] < -1.5
    assert 30.5 < rt["target"] < 31.5


def test_no_outside_temperature_falls_back(tmp_path):
    m = _module(tmp_path, curve_enabled=True)
    _tick(m, 0.0, -10.0)
    rt, types = _tick(m, 2 * 3600.0, None)   # weather gone for 2 h
    assert rt["target"] == 30.0 and not rt["curve_active"]
    assert "MIXER_CURVE_NO_WEATHER" in types


def test_switch_from_gui(tmp_path):
    m = _module(tmp_path)
    _tick(m, 0.0, -10.0)
    (tmp_path / "values.yaml").write_text(yaml.safe_dump({"target_temp": 30.0, "curve_enabled": True, "curve_at_0": 32.0}))
    m.reload_config_from_file()
    rt, _ = _tick(m, 1.0, -10.0)
    assert rt["curve_active"] and rt["target"] == 36.5


def test_net_movement_last_30_min(tmp_path):
    """Main page shows how much the valve opened (+) or closed (-) in the last 30 min."""
    m = _module(tmp_path, preclose_full_close_time_s=100.0)

    def run(t0, t1, open_, close):
        m._last_out_open, m._last_out_close = open_, close
        t, rt = t0, None
        while t < t1:
            t += 0.5
            rt = m._movement(t)
        return rt

    m._movement(0.0)
    run(0.0, 20.0, True, False)              # opened for 20 s
    rt = run(20.0, 25.0, False, True)        # closed for 5 s
    assert rt["moves_30m_s"] == 15.0 and rt["moves_30m_pct"] == 15 and rt["moving"] == "close"
    assert run(25.0, 31 * 60.0, False, False)["moves_30m_s"] == 0.0   # older than 30 min: gone


def test_changed_curve_is_logged_with_the_new_target(tmp_path):
    m = _module(tmp_path, curve_enabled=True, curve_at_0=29.0)
    _tick(m, 0.0, 10.0)
    (tmp_path / "values.yaml").write_text(yaml.safe_dump({"target_temp": 30.0, "curve_enabled": True, "curve_at_0": 30.0}))
    m.reload_config_from_file()
    rt, types = _tick(m, 1.0, 10.0)
    assert "MIXER_CURVE_CHANGED" in types and rt["target"] == 25.5
    _, types = _tick(m, 2.0, 10.0)
    assert "MIXER_CURVE_CHANGED" not in types          # once
    m.reload_config_from_file()                         # same values again: nothing new
    _, types = _tick(m, 3.0, 10.0)
    assert "MIXER_CURVE_CHANGED" not in types
