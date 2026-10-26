"""Phone alerts: cold flue gas at high power, reminders, all-clear, forwarded ALARM events."""
import shutil
import time
from pathlib import Path

from backend.core.state import BoilerMode, Event, EventLevel, Outputs, Sensors, SystemState
from backend.modules.alerts import AlertsModule

ROOT = Path(__file__).resolve().parents[1]


def _module(tmp_path, monkeypatch, sent, now):
    monkeypatch.setenv("FURNACE_BRAIN_NTFY_TOPIC", "piec-test")
    monkeypatch.setenv("FURNACE_BRAIN_VIEWER_KEY", "k" * 24)
    shutil.copy(ROOT / "backend/modules/alerts/schema.yaml", tmp_path / "schema.yaml")
    m = AlertsModule(base_path=tmp_path, sender=lambda *a: sent.append(a), clock=lambda: now[0])
    m._config.enabled = True
    m._run = lambda: None  # messages stay in the queue - read them directly
    return m


def _drain(m):
    out = []
    while not m._queue.empty():
        out.append(m._queue.get_nowait())
    return out


def _tick(m, now, flue, power, mode=BoilerMode.WORK, events=None, seconds=2):
    st = SystemState()
    st.mode = mode
    st.outputs = Outputs(power_percent=power)
    st.recent_events = events or []
    return m.tick(now[0], Sensors(flue_gas_temp=flue, boiler_temp=52.0), st)


def test_cold_flue_at_high_power(tmp_path, monkeypatch):
    sent, now = [], [1000.0]
    m = _module(tmp_path, monkeypatch, sent, now)
    _tick(m, now, 55, 80)
    assert [x["title"] for x in _drain(m)] == ["Powiadomienia włączone"]
    for _ in range(400):                       # ~13 min: hot flue, then cold
        now[0] += 2
        _tick(m, now, 55 if now[0] < 1400 else 35, 85)
    msgs = _drain(m)
    assert len(msgs) == 1 and msgs[0]["priority"] == 5 and "Spaliny" in msgs[0]["message"]
    for _ in range(150):                       # 5 more min: one reminder
        now[0] += 2
        _tick(m, now, 34, 90)
    msgs = _drain(m)
    assert len(msgs) == 1 and msgs[0]["message"].startswith("Nadal")
    for _ in range(200):                       # flue recovers
        now[0] += 2
        res = _tick(m, now, 60, 90)
    msgs = _drain(m)
    # the 5 min average climbs slowly - a last reminder may come before the all-clear
    assert "Koniec alarmu" in msgs[-1]["message"] and msgs[-1]["priority"] == 3
    assert all(x["message"].startswith("Nadal") for x in msgs[:-1])


def test_cold_flue_at_low_power_is_normal(tmp_path, monkeypatch):
    sent, now = [], [1000.0]
    m = _module(tmp_path, monkeypatch, sent, now)
    _drain(m)
    for _ in range(600):
        now[0] += 2
        _tick(m, now, 35, 40)
    assert [x for x in _drain(m) if x["title"] != "Powiadomienia włączone"] == []


def test_forwards_alarm_events_once_per_5_min(tmp_path, monkeypatch):
    sent, now = [], [1000.0]
    m = _module(tmp_path, monkeypatch, sent, now)
    ev = Event(ts=now[0], source="overheat", level=EventLevel.ALARM, type="BOILER_OVERHEAT_ON",
               message="Przegrzanie kotła: AKTYWNE (T=86°C)", data={})
    info = Event(ts=now[0], source="mixer", level=EventLevel.INFO, type="MIXER_MOVE", message="x", data={})
    _tick(m, now, None, 0, BoilerMode.OFF, [ev, info])
    now[0] += 60
    _tick(m, now, None, 0, BoilerMode.OFF, [ev])
    msgs = [x for x in _drain(m) if x["title"] != "Powiadomienia włączone"]
    assert len(msgs) == 1 and "Przegrzanie" in msgs[0]["message"]


def test_missing_topic_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("FURNACE_BRAIN_NTFY_TOPIC", raising=False)
    m = AlertsModule(base_path=tmp_path, sender=lambda *a: None)
    m._config.enabled = True
    res = m.tick(0, Sensors(), SystemState())
    assert any("FURNACE_BRAIN_NTFY_TOPIC" in e.message for e in res.events)


def test_send_ntfy_json_with_polish_text():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from backend.modules.alerts import send_ntfy
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got["path"] = self.path
            got["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    send_ntfy(f"http://127.0.0.1:{srv.server_address[1]}/", "piec-x", "⚠ Piec: zimne spaliny",
              "Spaliny 35°C - sprawdź ślimak", 5, "warning,fire", "https://example.com/#k=abc")
    assert got["path"] == "/"
    assert got["body"] == {"topic": "piec-x", "title": "⚠ Piec: zimne spaliny", "message": "Spaliny 35°C - sprawdź ślimak",
                           "priority": 5, "tags": ["warning", "fire"], "click": "https://example.com/#k=abc"}
