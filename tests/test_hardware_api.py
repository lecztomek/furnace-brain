"""Ustawienia -> Sprzęt through the settings API, and the 1-Wire list in Serwis -> Diagnostyka Pi."""
import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.config_api import create_config_router
from backend.api.service_api import LoopStats, create_service_router
from backend.hw import wiring as H
from backend.core.config_store import ConfigStore
from backend.core.state_store import StateStore

MODULES = Path(__file__).resolve().parents[1] / "backend" / "modules"


def test_settings_api_refuses_a_wrong_setup_and_applies_a_swap(tmp_path):
    shutil.copytree(MODULES / "hardware", tmp_path / "hardware", ignore=shutil.ignore_patterns("values.yaml", "__pycache__"))
    store = ConfigStore(tmp_path, ["hardware"], validators={"hardware": H.validate})
    applied = []
    app = FastAPI()
    app.include_router(create_config_router(store, reload_module_config=applied.append), prefix="/api")
    c = TestClient(app)
    r = c.put("/api/config/values/hardware", json={"gpio20": "pompa CO"})
    assert r.status_code == 400 and "kilku wyjściach" in r.json()["detail"] and applied == []   # nothing applied
    r = c.put("/api/config/values/hardware", json={"gpio20": "pompa CO", "gpio21": "pompa CWU"})
    assert r.status_code == 200 and applied == ["hardware"]
    assert H.pins(store.get_values("hardware"))["pump_co"] == 20


def test_diagnostics_lists_every_sensor_with_its_role(tmp_path):
    inventory = [{"rom": "28-000000506777", "field": "boiler_temp", "temp": 56.25},
                 {"rom": "28-0000aaaa0001", "field": None, "temp": 48.0},
                 {"rom": "28-00000000dead", "field": "cwu_temp", "temp": None, "missing": True}]
    store = StateStore()
    app = FastAPI()
    app.include_router(create_service_router(store=store, config_store=ConfigStore(tmp_path, []), data_root=tmp_path,
                                             app_info={"version": "1"}, loop_stats={"control": LoopStats(0.5)},
                                             sensor_inventory=lambda: inventory, hardware_problems=[]), prefix="/api")
    client = TestClient(app)
    assert client.get("/api/service/w1").json() == inventory                     # the list in Ustawienia -> Sprzęt
    d = client.get("/api/service/diagnostics").json()
    wire = next(g for g in d["groups"] if g["title"] == "Czujniki 1-Wire")["items"]
    assert wire[0]["label"] == "Kocioł" and wire[0]["value"] == "56,2°C" and wire[0]["note"] == "28-000000506777"
    assert wire[1]["label"] == "Nieprzypisany" and "48,0" in wire[1]["value"]
    assert wire[2]["status"] == "bad" and wire[2]["label"] == "Zasobnik CWU" and "nie ma go" in wire[2]["value"]
