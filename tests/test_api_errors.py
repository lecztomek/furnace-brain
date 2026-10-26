"""An unexpected API error lands on the logs page with its cause (no system journal on the Pi)."""
import importlib
import sys

from fastapi.testclient import TestClient


def test_unhandled_error_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("FURNACE_BRAIN_HW_RPI", "0")
    monkeypatch.setenv("FURNACE_BRAIN_DATA_ROOT", str(tmp_path))
    sys.modules.pop("backend.main", None)
    main = importlib.import_module("backend.main")

    @main.app.get("/api/test-broken")
    def broken():
        raise ValueError("zly plik CSV")

    client = TestClient(main.app, raise_server_exceptions=False)
    for _ in range(3):
        r = client.get("/api/test-broken")
        assert r.status_code == 500
        assert r.json() == {"detail": "ValueError: zly plik CSV"}

    events, _, _ = main.store.events_since(0)
    api = [e for e in events if e.source == "api"]
    assert len(api) == 1  # the same error is not repeated on every request
    assert api[0].message == "Błąd API GET /api/test-broken: ValueError: zly plik CSV"
    assert "raise ValueError" in api[0].data["traceback"]
