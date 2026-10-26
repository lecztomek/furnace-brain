"""Logs filtered by source: files without the source are skipped without parsing."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import logs_api

HEADER = "data_czas;ts_epoch;level;source;type;message;data_json\n"


def _client(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    for h in range(10):
        lines = [f"2026-10-26T{h:02d}:00:{s:02d};{1793000000 + h * 3600 + s}.0;INFO;blower;X;m {h}/{s};{{}}\n" for s in range(5)]
        if h == 2:
            lines.append(f"2026-10-26T02:30:00;{1793000000 + 2 * 3600 + 1800}.0;WARNING;weather;W;\"a;b\";{{}}\n")
        (d / f"events_20261026_{h:02d}.csv").write_text(HEADER + "".join(lines))
    app = FastAPI()
    app.include_router(logs_api.create_logs_router(tmp_path), prefix="/api")
    return TestClient(app)


def test_filter_by_source_and_level(tmp_path):
    c = _client(tmp_path)
    items = c.get("/api/logs/recent?source=weather").json()["items"]
    assert [(i["source"], i["message"]) for i in items] == [("weather", "a;b")]
    assert c.get("/api/logs/recent?source=weather&level=INFO").json()["items"] == []
    recent = c.get("/api/logs/recent?limit=3").json()
    assert [i["message"] for i in recent["items"]] == ["m 9/4", "m 9/3", "m 9/2"]
    assert recent["partial"] is False


def test_time_budget(tmp_path, monkeypatch):
    c = _client(tmp_path)
    monkeypatch.setattr(logs_api, "SEARCH_BUDGET_S", -1.0)
    body = c.get("/api/logs/recent?source=nothing").json()
    assert body["items"] == [] and body["partial"] is True
