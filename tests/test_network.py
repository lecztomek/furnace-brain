"""Sieć: a longer outage of the home network / internet is a warning on the main screen."""
import time

from backend.core.state import Sensors, SystemState
from backend.modules.network import NetworkModule


def _run(tmp_path, result, alarm_after_min=3.0, shift=0.0):
    m = NetworkModule(base_path=tmp_path, checker=lambda: dict(result))
    m.set_config_values({"alarm_after_min": alarm_after_min}, persist=False)
    end = time.time() + 2
    while time.time() < end:
        m.tick(now=time.time(), sensors=Sensors(), system_state=SystemState())
        if not m._checking and m._next > 0:
            break
        time.sleep(0.01)
    if shift and m._down_since:
        m._down_since -= shift                       # the outage started earlier
    return m.tick(now=time.time(), sensors=Sensors(), system_state=SystemState()).runtime


OK = {"lan": True, "internet": True, "dns": True, "gateway": "192.168.1.1"}


def test_all_fine_no_warning(tmp_path):
    rt = _run(tmp_path, OK)
    assert rt["lan"] and rt["internet"] and rt["down"] is None


def test_a_short_drop_is_not_shown(tmp_path):
    rt = _run(tmp_path, {**OK, "lan": False, "internet": False})
    assert rt["lan"] is False and rt["down"] is None


def test_no_router_for_minutes_is_shown(tmp_path):
    rt = _run(tmp_path, {**OK, "lan": False, "internet": False, "gateway": None}, shift=4 * 60)
    assert rt["down"] == "lan" and rt["down_since"] < time.time() - 200


def test_router_but_no_internet_is_shown(tmp_path):
    assert _run(tmp_path, {**OK, "internet": False}, shift=4 * 60)["down"] == "internet"
    assert _run(tmp_path, {**OK, "dns": False}, shift=4 * 60)["down"] == "internet"


def test_the_warning_on_the_main_screen(tmp_path):
    """The outage comes as a warning (yellow sign) in /api/state/current, like the other warnings."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.state_api import create_state_router
    from backend.core.config_store import ConfigStore
    from backend.core.state_store import StateStore
    store = StateStore()
    with store.locked() as st:
        st.runtime["network"] = {"lan": False, "internet": False, "down": "lan", "down_since": 1794725752.0}
    app = FastAPI()
    app.include_router(create_state_router(store, ConfigStore(tmp_path, [])), prefix="/api")
    data = TestClient(app).get("/api/state/current").json()
    assert data["problems"] == [] and [w["short"] for w in data["warnings"]] == ["SIEĆ"]
    assert "routerem" in data["warnings"][0]["text"]
    with store.locked() as st:
        st.runtime["network"] = {"lan": True, "internet": False, "down": "internet", "down_since": 1794725752.0}
    assert [w["short"] for w in TestClient(app).get("/api/state/current").json()["warnings"]] == ["INTERNET"]
