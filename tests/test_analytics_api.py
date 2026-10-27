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
        res = client.get("/api/analytics/report")
        body = res.json()
        if res.status_code == 200 and not body.get("stale"):
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


def test_requests_never_wait_for_a_build(tmp_path, monkeypatch):
    """A long build (the first one after an update on the Pi) must not hold the requests."""
    from backend.core.analytics import Analytics
    client = _client(tmp_path, monkeypatch)
    first = _wait_fresh(client)                 # a report exists

    real = Analytics.report

    def slow(self, *a, **k):
        time.sleep(2)
        return real(self, *a, **k)

    monkeypatch.setattr(Analytics, "report", slow)
    t0 = time.time()
    res = client.get("/api/analytics/report?fresh=1")   # refresh button: starts a build, doesn't wait
    assert res.status_code == 200 and res.json()["stale"] and "progress" in res.json()
    for _ in range(5):
        assert client.get("/api/analytics/report").json()["generated_at"] == first["generated_at"]
    assert time.time() - t0 < 1.0
    deadline = time.time() + 10                                          # and then the new one
    while client.get("/api/analytics/report").json()["generated_at"] == first["generated_at"]:
        assert time.time() < deadline
        time.sleep(0.1)
