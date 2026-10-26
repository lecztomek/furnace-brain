import gzip
import importlib
import json
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FURNACE_BRAIN_VIEWER_TOKEN", "sekret")
    monkeypatch.delenv("FURNACE_BRAIN_VIEWER_PASSWORD", raising=False)
    import backend.viewer as viewer
    viewer = importlib.reload(viewer)
    return TestClient(viewer.app)


NOW = time.time()
DAY = time.strftime("%Y-%m-%d")


def push(client, body, token="sekret"):
    return client.post("/api/viewer/push", content=gzip.compress(json.dumps(body).encode()),
                       headers={"Authorization": f"Bearer {token}", "Content-Encoding": "gzip",
                                "Content-Type": "application/json"})


def test_push_and_read(client):
    rows = [{"data_czas": f"{DAY}T10:{m:02d}:00", "temp_pieca": "55"} for m in range(10)]
    reply = push(client, {
        "state": {"mode": "WORK", "sensors": {"boiler_temp": 55}},
        "history_rows": rows,
        "log_items": [{"ts": NOW, "level": "INFO", "source": "mixer", "type": "X", "message": "a"}],
        "config": {"modules": [{"id": "mixer"}], "schemas": {"mixer": {"fields": []}}, "values": {"mixer": {"target_temp": 30.0}}},
    }).json()
    assert reply["history_last"] == f"{DAY}T10:09:00" and reply["logs_last"] == NOW

    assert client.get("/api/state/current").json()["mode"] == "WORK"
    data = client.get("/api/history/data", params={"from_ts": f"{DAY}T10:05:00", "to_ts": f"{DAY}T11:00:00"}).json()
    assert data["count"] == 5
    assert client.get("/api/logs/recent", params={"source": "mixer"}).json()["count"] == 1
    assert client.get("/api/config/value/mixer/target_temp").json() == {"key": "target_temp", "value": 30.0}

    # history increments are appended without duplicates
    push(client, {"history_rows": rows[-2:] + [{"data_czas": f"{DAY}T10:10:00"}]})
    assert client.get("/api/history/data", params={"from_ts": f"{DAY}T00:00:00", "to_ts": f"{DAY}T23:59:59"}).json()["count"] == 11


def test_bad_token_rejected(client):
    assert push(client, {"state": {"mode": "OFF"}}, token="zly").status_code == 401
    assert client.get("/api/state/current").status_code == 503


def test_every_change_is_ignored(client):
    push(client, {"state": {"mode": "WORK"}, "config": {"modules": [], "schemas": {}, "values": {"mixer": {"target_temp": 30.0}}}})
    assert client.put("/api/config/value/mixer/target_temp", json=55).json()["value"] == 30.0
    assert client.put("/api/config/values/mixer", json={"target_temp": 55}).json() == {"target_temp": 30.0}
    assert client.post("/api/state/mode/OFF").json()["mode"] == "WORK"
    assert client.post("/api/manual/outputs", json={"feeder_on": True}).json()["ignored"] is True
    assert client.get("/api/state/current").json()["mode"] == "WORK"


def test_gzip_makes_history_much_smaller():
    rows = [{"data_czas": f"2026-10-26T{h:02d}:{m:02d}:{s:02d}", "temp_pieca": "56.91", "power": "45.2",
             "temp_grzejnikow": "31.5", "temp_spalin": "118.3", "tryb_pracy": "WORK", "temp_zewn": "4.3",
             "dmuchawa": "42.0", "slimak": "7.1", "zadana": "57.0"}
            for h in range(24) for m in range(60) for s in (0, 30)][:5000]
    raw = json.dumps({"history_rows": rows}, separators=(",", ":")).encode()
    assert len(gzip.compress(raw)) * 8 < len(raw)
