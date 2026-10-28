import logging
import threading
import time

import pytest

from backend.core.state import Sensors, SystemState
from backend.modules.weather import WeatherModule
from furnace_sim import FurnaceSim


@pytest.fixture(autouse=True)
def quiet_logs():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def tick_until_fetched(module, state, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        result = module.tick(now=time.time(), sensors=Sensors(), system_state=state)
        if not module._fetching and module._next_attempt > 0:
            return module.tick(now=time.time(), sensors=Sensors(), system_state=state)
        time.sleep(0.01)
    raise AssertionError("fetch did not finish")


def test_publishes_temperature(tmp_path):
    module = WeatherModule(base_path=tmp_path, fetcher=lambda lat, lon: 3.5)
    result = tick_until_fetched(module, SystemState())

    assert result.runtime["temp"] == 3.5
    assert result.runtime["location"] == "Nowy Sącz"
    assert result.runtime["valid_until"] > time.time() + 50 * 60
    assert any(e.type == "WEATHER_OK" for e in result.events) or module._error is None


def test_fetch_error_reported_once_and_retried_later(tmp_path):
    def fail(lat, lon):
        raise OSError("no internet")

    module = WeatherModule(base_path=tmp_path, fetcher=fail)
    state = SystemState()
    result = tick_until_fetched(module, state)
    first_events = [e.type for e in result.events] + [e.type for e in module._pending_events]

    assert result.runtime["temp"] is None
    assert "no internet" in result.runtime["error"]
    assert module._next_attempt - time.time() > 4 * 60  # retry in ~5 min, not every tick
    # one warning, no new fetch on the next ticks
    assert module.tick(now=time.time(), sensors=Sensors(), system_state=state).events == []
    assert first_events.count("WEATHER_FETCH_ERROR") <= 1


def test_slow_network_never_blocks_tick(tmp_path):
    release = threading.Event()

    def slow(lat, lon):
        release.wait(5)
        return 1.0

    module = WeatherModule(base_path=tmp_path, fetcher=slow)
    start = time.time()
    for _ in range(20):
        module.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
    assert time.time() - start < 0.5
    release.set()


def test_fetches_at_most_every_refresh_interval(tmp_path):
    calls = []
    module = WeatherModule(base_path=tmp_path, fetcher=lambda lat, lon: calls.append(1) or 2.0)
    state = SystemState()
    tick_until_fetched(module, state)
    for _ in range(50):
        module.tick(now=time.time(), sensors=Sensors(), system_state=state)
    time.sleep(0.05)
    assert len(calls) == 1


def test_everyone_reads_the_weather_from_the_shared_state(tmp_path):
    """The weather module publishes runtime["weather"]; the curve, the history, diagnostics and
    the main page read it from there (backend.core.outside) - the kernel knows nothing of it."""
    from backend.core.outside import outside_temp
    now = time.time()
    fresh = {"weather": {"temp": -4.0, "valid_until": now + 600}}
    assert outside_temp(None, fresh, now) == -4.0
    assert outside_temp(None, {"weather": {"temp": -4.0, "valid_until": now - 1}}, now) is None   # stale
    assert outside_temp(2.5, fresh, now) == 2.5                                  # an outside sensor wins
    sim = FurnaceSim(tmp_path)
    with sim.store.locked() as st:
        st.runtime["weather"] = {"temp": -4.0, "valid_until": time.time() + 600}
    sim.run(5)
    assert sim.state.sensors.outside_temp is None                                # the kernel doesn't touch it
    assert sim.state.runtime["curve"]["outside_avg"] == -4.0                     # the curve read it


def test_forecast_fetched_with_the_temperature_and_published(tmp_path):
    forecast = {"hourly": [{"time": "2026-11-10T12:00", "temp": -3.0, "code": 71}], "daily": []}
    calls = []
    module = WeatherModule(base_path=tmp_path, fetcher=lambda lat, lon: 1.0,
                           forecast_fetcher=lambda lat, lon: calls.append(1) or forecast)
    tick_until_fetched(module, SystemState())
    deadline = time.time() + 2
    while module._fetching and time.time() < deadline:
        time.sleep(0.01)
    result = module.tick(time.time(), Sensors(), SystemState())
    assert result.runtime["forecast"]["hourly"] == forecast["hourly"]
    assert result.runtime["forecast"]["location"] == "Nowy Sącz"
    assert calls == [1]      # once an hour, not with every 15 min temperature fetch


def test_history_writes_the_weather_temperature(tmp_path):
    """No outside sensor on the furnace: the history column temp_zewn comes from the weather."""
    import csv
    from backend.modules.history import HistoryModule
    from backend.core.state import BoilerMode
    m = HistoryModule(data_root=tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    st.runtime["weather"] = {"temp": -3.5, "valid_until": time.time() + 600}
    t = time.time()
    for i in range(40):
        st.ts_mono = i * 2.0
        m.tick(now=t + i * 2.0, sensors=Sensors(boiler_temp=56.0, radiators_temp=30.0), system_state=st)
    files = list(tmp_path.rglob("boiler_*.csv"))
    rows = list(csv.DictReader(files[0].open(encoding="utf-8"), delimiter=";"))
    assert rows and all(r["temp_zewn"] == "-3.5" for r in rows)
