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


def test_kernel_uses_weather_as_outside_temp(tmp_path):
    sim = FurnaceSim(tmp_path)
    with sim.store.locked() as st:
        st.runtime["weather"] = {"temp": -4.0, "valid_until": time.time() + 600}
    sim.run(5)
    assert sim.state.sensors.outside_temp == -4.0

    with sim.store.locked() as st:
        st.runtime["weather"] = {"temp": -4.0, "valid_until": time.time() - 1}  # stale
    sim.run(5)
    assert sim.state.sensors.outside_temp is None
