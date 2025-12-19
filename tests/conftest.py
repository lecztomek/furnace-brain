# tests/conftest.py
import pytest
from backend.core.state import SystemState, Sensors, Outputs

# TODO: ZMIEŃ TEN IMPORT na właściwy:
# from backend.modules.stats import StatsModule
from backend.modules.stats import StatsModule  # <-- popraw ścieżkę jeśli trzeba


@pytest.fixture
def stats_module():
    return StatsModule()  # jeśli wymaga depsów, wstrzyknij tu mocki


@pytest.fixture
def state():
    return SystemState(ts=0.0, sensors=Sensors(), outputs=Outputs(), runtime={})


@pytest.fixture
def sensors_ok():
    # stats bazuje na feeder_on, ale Sensors wymagane przez tick
    return Sensors(boiler_temp=50.0)
