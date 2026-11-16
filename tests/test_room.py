"""Temperatura w domu: the room thermometer is read (Tasmota / ESPHome) and recorded in the history."""
import csv
import datetime as dt
import time

import pytest

from backend.core.room import room_temp
from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.history import HistoryModule
from backend.modules.room import RoomModule, parse_temperature, reading_url, thermometer_address

TASMOTA = {"StatusSNS": {"Time": "2026-11-16T10:00:00", "DS18B20": {"Id": "01193A4B", "Temperature": 21.4},
                         "TempUnit": "C"}}


def test_reading_a_tasmota_and_an_esphome_answer():
    assert parse_temperature(TASMOTA) == 21.4
    assert parse_temperature({"StatusSNS": {"DS18B20-1": {"Temperature": 70.5}, "TempUnit": "F"}}) == pytest.approx(21.39, abs=0.01)
    assert parse_temperature({"id": "sensor-temperatura", "value": 20.9, "state": "20.9 °C"}) == 20.9
    with pytest.raises(ValueError):
        parse_temperature({"StatusSNS": {"Time": "x"}})
    assert reading_url("192.168.1.50") == "http://192.168.1.50/cm?cmnd=Status%2010"
    assert thermometer_address(50, "192.168.1.17") == "192.168.1.50"
    with pytest.raises(ValueError):
        thermometer_address(50, "127.0.1.1")


def _tick_until_read(module, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        result = module.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
        if not module._fetching and module._next_attempt > 0:
            return module.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
        time.sleep(0.01)
    raise AssertionError("read did not finish")


def test_publishes_the_room_temperature(tmp_path):
    seen = []
    m = RoomModule(base_path=tmp_path, fetcher=lambda address: seen.append(address) or 21.37,
                   ip_finder=lambda: "192.168.1.17")
    m.set_config_values({"address_last": 50}, persist=False)
    result = _tick_until_read(m)
    assert result.runtime["temp"] == 21.37 and seen == ["192.168.1.50"]
    assert result.runtime["address"] == "192.168.1.50"
    assert room_temp({"room": result.runtime}) == 21.37
    assert room_temp({"room": result.runtime}, now=time.time() + 11 * 60) is None   # too old


def test_address_0_reads_nothing_and_says_nothing(tmp_path):
    calls = []
    m = RoomModule(base_path=tmp_path, fetcher=lambda address: calls.append(address) or 21.0)
    m.set_config_values({"address_last": 0}, persist=False)
    for _ in range(3):
        r = m.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
        assert r.runtime == {} and r.events == []
    time.sleep(0.05)
    assert calls == []


def test_a_broken_reading_is_not_used(tmp_path):
    m = RoomModule(base_path=tmp_path, fetcher=lambda address: 85.0,     # DS18B20 power-on value
                   ip_finder=lambda: "192.168.1.17")
    m.set_config_values({"address_last": 50}, persist=False)
    result = _tick_until_read(m)
    assert result.runtime["temp"] is None and "poza zakresem" in result.runtime["error"]
    assert any(e.type == "ROOM_FETCH_ERROR" for e in result.events) or m._problems._open


def _history(tmp_path, t0, runtime, n=20):
    m = HistoryModule(data_root=tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    st.runtime = runtime
    for i in range(n):
        st.ts_mono = i * 2.0
        m.tick(now=t0 + i * 2.0, sensors=Sensors(boiler_temp=56.0, hopper_temp=38.9), system_state=st)
    return m


def test_the_room_temperature_is_in_the_history(tmp_path):
    t0 = time.time()
    _history(tmp_path, t0, {"room": {"temp": 21.4, "valid_until": t0 + 600}})
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
    _history(tmp_path, t0, {"room": {"temp": 21.4, "valid_until": t0 + 600}}, n=5)
    lines = (m._log_dir / name).read_text(encoding="utf-8").splitlines()
    assert len(lines) > 1 and all(len(line.split(";")) == 11 for line in lines)
