"""Who changed the mode: written into MODE_CHANGED (the screen, a browser, a flame-out, a restart)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.state_api import create_state_router
from backend.core.config_store import ConfigStore
from backend.core.state import BoilerMode, Sensors, SystemState
from backend.core.state_store import StateStore
from backend.modules.mode import ModeModule


def _changes(m, st):
    res = m.tick(now=0.0, sensors=Sensors(boiler_temp=50.0), system_state=st)
    return [(e.message, e.data.get("source")) for e in res.events if e.type == "MODE_CHANGED"]


def test_the_source_is_logged_and_cleared(tmp_path):
    m = ModeModule(base_path=tmp_path)
    st = SystemState(mode=BoilerMode.OFF)
    assert _changes(m, st)[0][1] == "start programu"
    st.mode, st.mode_source = BoilerMode.IGNITION, "ręcznie z ekranu przy piecu"
    msg, src = _changes(m, st)[0]
    assert src == "ręcznie z ekranu przy piecu" and msg.endswith("(ręcznie z ekranu przy piecu)")
    assert st.mode_source == ""                                       # not reused for the next change
    st.mode = BoilerMode.OFF
    assert _changes(m, st)[0][1] == "nieznane"


def _api(tmp_path):
    store = StateStore()
    app = FastAPI()
    app.include_router(create_state_router(store, ConfigStore(tmp_path, [])), prefix="/api")
    return store, TestClient(app)


def test_the_screen_and_a_browser_on_the_network(tmp_path):
    store, client = _api(tmp_path)
    client.post("/api/state/mode/IGNITION")                                  # straight from the Pi
    assert store.snapshot().mode_source == "ręcznie z ekranu przy piecu"
    client.post("/api/state/mode/WORK", headers={"X-Forwarded-For": "127.0.0.1"})   # the screen through the gateway
    assert store.snapshot().mode_source == "ręcznie z ekranu przy piecu"
    client.post("/api/state/mode/OFF", headers={"X-Forwarded-For": "192.168.1.23"})  # a phone at home
    assert store.snapshot().mode_source == "ręcznie z przeglądarki (192.168.1.23)"
