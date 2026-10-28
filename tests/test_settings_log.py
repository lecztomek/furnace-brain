"""Settings history: all settings with the version at every start, change and once a day."""
import json
from datetime import datetime, timedelta

from backend.core.settings_log import SettingsLog, diff, read_entries


def _log(tmp_path, values, clock):
    return SettingsLog(tmp_path / "settings_log", snapshot=lambda: json.loads(json.dumps(values)),
                       version="1.45.0", commit="abc1234", now=lambda: clock[0])


def test_start_change_daily(tmp_path):
    values = {"power_work": {"kp": 10.0, "ki": 0.005}, "remote_view": {"enabled": True, "db_secret": "x"}}
    clock = [datetime(2026, 10, 28, 15, 0)]
    log = _log(tmp_path, values, clock)
    start = log.record("start")
    assert start["version"] == "1.45.0" and start["commit"] == "abc1234" and start["changed"] == {}
    assert "db_secret" not in start["settings"]["remote_view"]               # no secrets on the stick
    assert log.record("change") is None                                      # nothing changed - no line
    values["power_work"]["kp"] = 8.0
    change = log.record("change")
    assert change["changed"] == {"power_work": {"kp": [10.0, 8.0]}}
    log.maybe_daily()                                                         # same day: nothing
    clock[0] += timedelta(hours=10)                                           # past midnight
    log.maybe_daily()
    entries = read_entries(tmp_path / "settings_log", datetime(2026, 10, 1))
    assert [e["reason"] for e in entries] == ["start", "change", "daily"]
    assert entries[2]["settings"]["power_work"]["kp"] == 8.0


def test_a_restart_continues_from_the_last_entry(tmp_path):
    values = {"power_work": {"kp": 10.0}}
    clock = [datetime(2026, 10, 28, 15, 0)]
    _log(tmp_path, values, clock).record("start")
    values["power_work"]["kp"] = 8.0                                          # changed while it was down
    with (tmp_path / "settings_log" / "settings_202610.jsonl").open("a") as f:
        f.write('{"ts": "2026-10-28T15:01:00", "cut by a power lo')           # a broken last line
    again = _log(tmp_path, values, clock).record("start")
    assert again["changed"] == {"power_work": {"kp": [10.0, 8.0]}}


def test_never_raises(tmp_path):
    def broken():
        raise OSError("usb stick gone")
    log = SettingsLog(tmp_path / "x", snapshot=broken, version="1")
    assert log.record("start") is None


def test_diff():
    assert diff({"a": {"x": 1}}, {"a": {"x": 1}, "b": {"y": 2}}) == {"b": {"y": [None, 2]}}


def test_the_module_writes_at_start_after_a_change_and_not_otherwise(tmp_path):
    import os
    import shutil
    from pathlib import Path

    import yaml

    from backend.core.state import Sensors, SystemState
    from backend.modules.settings_log import SettingsLogModule
    src = Path(__file__).resolve().parents[1] / "backend" / "modules"
    mods = tmp_path / "modules"
    shutil.copytree(src, mods, ignore=shutil.ignore_patterns("values.yaml", "__pycache__", "data", "*.csv"))
    data = tmp_path / "data"
    m = SettingsLogModule(base_path=mods / "settings_log", data_root=data)
    st = SystemState()
    t0 = datetime(2026, 11, 10, 12).timestamp()                          # the furnace clock (the tick's now)
    m.tick(now=t0 + 0.0, sensors=Sensors(), system_state=st)                 # start
    m.tick(now=t0 + 20.0, sensors=Sensors(), system_state=st)                # nothing changed
    pw = mods / "power_work" / "values.yaml"
    pw.write_text(yaml.safe_dump({"kp": 8.0}))                          # changed in the GUI (or by hand)
    os.utime(pw, (1, 1))
    m.tick(now=t0 + 25.0, sensors=Sensors(), system_state=st)                # within check_every_s: not looked at yet
    m.tick(now=t0 + 40.0, sensors=Sensors(), system_state=st)
    entries = read_entries(data / "modules" / "settings_log" / "data", datetime(2000, 1, 1))
    assert [e["reason"] for e in entries] == ["start", "change"]
    assert entries[1]["changed"]["power_work"]["kp"][1] == 8.0
    assert "power_work" in entries[0]["settings"] and entries[0]["version"]
    # stamped with the furnace clock, like the history - the simulator's runs ahead of the wall clock
    assert entries[1]["ts"] == "2026-11-10T12:00:40"
