"""After a restart the statistics page gets the last saved report at once, then a fresh one."""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import analytics_api
from backend.core.config_store import ConfigStore


def _client(tmp_path, monkeypatch):
    # the background warm-up starts 5 s after start - the requests below come first
    (tmp_path / "modules" / "stats" / "data").mkdir(parents=True, exist_ok=True)
    app = FastAPI()
    app.include_router(analytics_api.create_analytics_router(tmp_path, ConfigStore(tmp_path / "cfg", [])), prefix="/api")
    return TestClient(app)


def _wait_fresh(client, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get("/api/analytics/report").json()
        if not body.get("stale"):
            return body
        time.sleep(0.05)
    raise AssertionError("no fresh report")


def test_report_survives_restart(tmp_path, monkeypatch):
    first = _client(tmp_path, monkeypatch)
    report = _wait_fresh(first)
    assert (tmp_path / "modules/stats/data/analytics_report.json").exists()
    assert report["generated_at"] > 0

    # restart: a new process has no report in memory
    second = _client(tmp_path, monkeypatch)
    body = second.get("/api/analytics/report").json()
    assert body["generated_at"] == report["generated_at"]   # the saved one, right away
    fresh = _wait_fresh(second)
    assert fresh["generated_at"] > report["generated_at"]   # then a new one
