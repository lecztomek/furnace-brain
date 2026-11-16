"""Termometry WiFi: thermometers with roles are read (Tasmota) and the "w domu" one is recorded."""
import csv
import datetime as dt
import time

import pytest

from backend.core.room import room_temp
from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.history import HistoryModule
from backend.modules.thermometers import ThermometersModule, parse_temperature, reading_url, thermometer_address

TASMOTA = {"StatusSNS": {"Time": "2026-11-16T12:51:03", "DS18B20": {"Id": "0000006FE9A2", "Temperature": 21.4},
                         "TempUnit": "C"}}


def test_reading_a_tasmota_answer():
    assert parse_temperature(TASMOTA) == 21.4
    assert parse_temperature({"StatusSNS": {"DS18B20-1": {"Temperature": 70.5}, "TempUnit": "F"}}) == pytest.approx(21.39, abs=0.01)
    assert parse_temperature({"id": "sensor-temperatura", "value": 20.9}) == 20.9
    with pytest.raises(ValueError):
        parse_temperature({"StatusSNS": {"Time": "x"}})
    assert reading_url("192.168.1.245") == "http://192.168.1.245/cm?cmnd=Status%2010"
    assert thermometer_address(245, "192.168.1.17") == "192.168.1.245"
    with pytest.raises(ValueError):
        thermometer_address(245, "127.0.1.1")


def _module(tmp_path, temps, **values):
    seen = []

    def fetch(address):
        seen.append(address)
        t = temps[address]
        if isinstance(t, Exception):
            raise t
        return t
    m = ThermometersModule(base_path=tmp_path, fetcher=fetch, ip_finder=lambda: "192.168.1.17")
    m.set_config_values(values, persist=False)
    return m, seen


def _tick_until_read(module, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        module.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
        if not module._fetching and module._next_attempt > 0:
            return module.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
        time.sleep(0.01)
    raise AssertionError("read did not finish")


def test_the_default_thermometer_245_is_the_house(tmp_path):
    m, seen = _module(tmp_path, {"192.168.1.245": 21.4})
    rt = _tick_until_read(m).runtime
    assert seen == ["192.168.1.245"]
    assert rt["roles"]["w_domu"]["temp"] == 21.4 and rt["roles"]["w_domu"]["address"] == "192.168.1.245"
    assert room_temp({"thermometers": rt}) == 21.4
    assert room_temp({"thermometers": rt}, now=time.time() + 11 * 60) is None   # too old
    assert any(e.type == "THERMOMETER_OK" for e in _tick_until_read(m).events) or m._readings[1]["temp"] == 21.4


def test_several_thermometers_the_first_working_one_has_the_role(tmp_path):
    m, seen = _module(tmp_path, {"192.168.1.245": OSError("timed out"), "192.168.1.246": 20.5, "192.168.1.247": 5.0},
                      t2_address=246, t2_role="w_domu", t3_address=247, t3_role="brak")
    rt = _tick_until_read(m).runtime
    assert seen == ["192.168.1.245", "192.168.1.246"]          # role "brak" is not read
    assert rt["roles"]["w_domu"]["address"] == "192.168.1.246"
    assert [d["slot"] for d in rt["devices"]] == [1, 2] and rt["devices"][0]["error"]


def test_nothing_set_up_reads_nothing_and_says_nothing(tmp_path):
    m, seen = _module(tmp_path, {}, t1_address=0)
    for _ in range(3):
        r = m.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
        assert r.runtime == {} and r.events == []
    time.sleep(0.05)
    assert seen == []


def test_a_broken_reading_is_not_used(tmp_path):
    m, _ = _module(tmp_path, {"192.168.1.245": 85.0})      # DS18B20 power-on value
    rt = _tick_until_read(m).runtime
    assert "w_domu" not in rt["roles"] and "poza zakresem" in rt["devices"][0]["error"]


def _history(tmp_path, t0, runtime, n=20):
    m = HistoryModule(data_root=tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    st.runtime = runtime
    for i in range(n):
        st.ts_mono = i * 2.0
        m.tick(now=t0 + i * 2.0, sensors=Sensors(boiler_temp=56.0, hopper_temp=38.9), system_state=st)
    return m


def _house(t0, temp=21.4):
    return {"thermometers": {"roles": {"w_domu": {"temp": temp, "valid_until": t0 + 600}}}}


def test_the_house_temperature_is_in_the_history(tmp_path):
    t0 = time.time()
    _history(tmp_path, t0, _house(t0))
    rows = list(csv.DictReader(sorted(tmp_path.rglob("boiler_*.csv"))[0].open(encoding="utf-8"), delimiter=";"))
    assert rows and all(r["temp_wewn"] == "21.4" for r in rows)


def test_without_a_thermometer_the_column_is_empty(tmp_path):
    _history(tmp_path, time.time(), {})
    rows = list(csv.DictReader(sorted(tmp_path.rglob("boiler_*.csv"))[0].open(encoding="utf-8"), delimiter=";"))
    assert rows and all(r["temp_wewn"] == "" for r in rows)


def test_a_file_from_before_the_thermometer_keeps_its_columns(tmp_path):
    t0 = time.time()
    m = HistoryModule(data_root=tmp_path)
    m._log_dir.mkdir(parents=True, exist_ok=True)
    name = f"{m._config.file_prefix}_{dt.datetime.fromtimestamp(t0).strftime('%Y%m%d_%H')}.csv"
    header = "data_czas;temp_pieca;power;temp_grzejnikow;temp_spalin;tryb_pracy;temp_zewn;dmuchawa;slimak;zadana;temp_podajnika"
    (m._log_dir / name).write_text(header + "\n", encoding="utf-8")
    _history(tmp_path, t0, _house(t0), n=5)
    lines = (m._log_dir / name).read_text(encoding="utf-8").splitlines()
    assert len(lines) > 1 and all(len(line.split(";")) == 11 for line in lines)
