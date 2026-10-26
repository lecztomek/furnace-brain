"""
Remote view: the Pi module writes to (fake) Firebase, the GitHub Pages shim
reads it back - the GUI must get the same answers as from the local API.
"""
import json
import shutil
import subprocess
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from backend.modules.remote_view import RemoteViewModule
from tests.fake_firebase import FakeFirebase

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now().replace(microsecond=0)


def _rows():
    rows = []
    for i in range(240):  # last 2 hours, every 30 s
        t = NOW - timedelta(seconds=30 * (239 - i))
        rows.append({"data_czas": t.isoformat(), "temp_pieca": 50 + i % 7, "tryb_pracy": "WORK", "power": ""})
    return rows


ROWS = _rows()
LOGS = [{"ts": NOW.timestamp() - 60 * i, "level": "INFO" if i % 2 else "WARNING", "source": "mixer",
         "type": "MOVE", "message": f"ruch {i}", "data": {}} for i in range(30)]
STATE = {"mode": "WORK", "power_regulators": [], "sensors": {"boiler_temp": 55.5}, "app": {"version": "9.9.9"}}
VALUES = {"enabled": True, "boiler_set_temp": 60, "list": [], "nested": {}}
SCHEMA = {"id": "power_pid", "fields": [{"key": "boiler_set_temp", "type": "number"}]}


class LocalApi(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        p = url.path
        if p == "/api/state/current":
            body = STATE
        elif p == "/api/manual/current":
            body = {"feeder": False}
        elif p == "/api/history/data":
            lo, hi = q["from_ts"][0], q["to_ts"][0]
            items = [r for r in ROWS if lo <= r["data_czas"] <= hi]
            if "max_points" in q:
                items = items[::10]
            body = {"items": items, "count": len(items)}
        elif p == "/api/logs/recent":
            body = {"items": LOGS, "count": len(LOGS)}
        elif p == "/api/logs/meta":
            body = {"levels": ["INFO", "WARNING"], "sources": ["mixer"]}
        elif p == "/api/analytics/report":
            body = {"periods": {"d7": {"coal_kg": 12.5}}, "daily": [], "checks": []}
        elif p == "/api/config/modules":
            body = [{"id": "power_pid", "name": "PID"}]
        elif p == "/api/config/schema/power_pid":
            body = SCHEMA
        elif p == "/api/config/values/power_pid":
            body = VALUES
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def setup(monkeypatch, tmp_path):
    local = ThreadingHTTPServer(("127.0.0.1", 0), LocalApi)
    threading.Thread(target=local.serve_forever, daemon=True).start()
    fb = FakeFirebase("tajne")
    fb_srv = fb.serve(0)
    db_url = f"http://127.0.0.1:{fb_srv.server_address[1]}"
    monkeypatch.setenv("BACKEND_PORT", str(local.server_address[1]))
    monkeypatch.setenv("FURNACE_BRAIN_VIEWER_FIREBASE_URL", db_url)
    monkeypatch.setenv("FURNACE_BRAIN_VIEWER_FIREBASE_SECRET", "tajne")
    monkeypatch.setenv("FURNACE_BRAIN_VIEWER_KEY", "klucz123")
    shutil.copy(ROOT / "backend/modules/remote_view/schema.yaml", tmp_path / "schema.yaml")
    mod = RemoteViewModule(base_path=tmp_path)
    yield mod, fb, db_url
    local.shutdown()
    fb_srv.shutdown()


def _push_cycle(mod):
    cursors = mod._fb_get("cursors") or {}
    patch = {"state": json.dumps(mod._get("/api/state/current")), "manual": json.dumps(mod._get("/api/manual/current"))}
    patch.update(mod._increments(cursors))
    patch.update(mod._slow_report())
    patch.update(mod._slow_config())
    patch.update(mod._slow_history5m())
    patch["cursors"] = cursors
    mod._fb_patch(patch)
    mod._cleanup()


def test_push_and_increments(setup):
    mod, fb, _ = setup
    _push_cycle(mod)
    data = fb.root["furnace"]["klucz123"]
    assert json.loads(data["state"]) == STATE  # empty list survives
    assert json.loads(data["config"]["values"]["power_pid"]) == VALUES
    stored = [json.loads(v) for day in data["history"].values() for v in day.values()]
    assert sorted(stored, key=lambda r: r["data_czas"]) == ROWS
    assert sum(len(d) for d in data["logs"].values()) == len(LOGS)
    assert data["cursors"]["history"] == ROWS[-1]["data_czas"]

    # second cycle sends nothing old again
    patch = mod._increments(dict(data["cursors"]))
    assert not any(k.startswith(("history/", "logs/")) for k in patch)


def test_reads_need_no_secret_writes_do(setup):
    mod, fb, db_url = setup
    _push_cycle(mod)
    import urllib.error
    import urllib.request
    with urllib.request.urlopen(f"{db_url}/furnace/klucz123/state.json") as r:
        assert json.loads(json.load(r)) == STATE
    req = urllib.request.Request(f"{db_url}/furnace/klucz123/state.json", data=b'"x"', method="PUT")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(req)
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"{db_url}/furnace.json?shallow=true")  # keys can't be listed


def test_cleanup_removes_old_days(setup):
    mod, fb, _ = setup
    _push_cycle(mod)
    fb.root["furnace"]["klucz123"]["history"]["2000-01-01"] = {"000000": "{}"}
    mod._cleanup()
    assert "2000-01-01" not in fb.root["furnace"]["klucz123"]["history"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_shim_returns_local_api_shapes(setup):
    mod, _, db_url = setup
    _push_cycle(mod)
    lo = (NOW - timedelta(hours=1)).isoformat()
    hi = NOW.isoformat()
    calls = {
        "state": "/api/state/current",
        "values": "/api/config/values/power_pid",
        "value": "/api/config/value/power_pid/boiler_set_temp",
        "schema": "/api/config/schema/power_pid",
        "modules": "/api/config/modules",
        "fields": "/api/history/fields",
        "hist": f"/api/history/data?from_ts={lo}&to_ts={hi}&fields=temp_pieca",
        "hist_avg": f"/api/history/data?from_ts={lo}&to_ts={hi}&max_points=12",
        "logs": "/api/logs/recent?limit=5&level=WARNING",
        "report": "/api/analytics/report",
        "missing": "/api/nothing",
    }
    script = """
      const calls = %s;
      global.window = { FURNACE_VIEWER_DB: %s, location: { hash: "#k=klucz123", href: "https://x.github.io/furnace-brain/index.html", origin: "https://x.github.io" }, fetch: fetch };
      global.document = { addEventListener() {} };
      global.localStorage = { getItem() { return null; }, setItem() {} };
      require(%s);
      (async () => {
        const out = {};
        for (const [k, u] of Object.entries(calls)) {
          const r = await window.fetch(u);
          out[k] = { status: r.status, body: await r.json() };
        }
        const w = await window.fetch("/api/config/value/power_pid/boiler_set_temp", { method: "PUT", body: '{"value": 99}' });
        out.write = await w.json();
        const m = await window.fetch("/api/state/mode/OFF", { method: "POST" });
        out.mode = await m.json();
        console.log(JSON.stringify(out));
      })();
    """ % (json.dumps(calls), json.dumps(db_url), json.dumps(str(ROOT / "viewer/viewer-shim.js")))
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    assert out["state"]["body"] == STATE
    assert out["values"]["body"] == VALUES
    assert out["value"]["body"] == {"key": "boiler_set_temp", "value": 60}
    assert out["schema"]["body"] == SCHEMA
    assert out["modules"]["body"] == [{"id": "power_pid", "name": "PID"}]
    assert out["fields"]["body"]["fields"][0] == "data_czas"
    hist = out["hist"]["body"]["items"]
    assert hist == [{"data_czas": r["data_czas"], "temp_pieca": r["temp_pieca"]} for r in ROWS if lo <= r["data_czas"] <= hi]
    assert out["hist_avg"]["body"]["count"] <= 12
    assert out["hist_avg"]["body"]["raw_count"] == len(hist)
    assert all(i["level"] == "WARNING" for i in out["logs"]["body"]["items"])
    assert len(out["logs"]["body"]["items"]) == 5
    assert out["logs"]["body"]["items"][0]["ts"] > out["logs"]["body"]["items"][-1]["ts"]  # newest first
    assert out["report"]["body"]["periods"]["d7"]["coal_kg"] == 12.5
    assert out["missing"]["status"] == 404
    # writes ignored, real values returned
    assert out["write"] == {"key": "boiler_set_temp", "value": 60}
    assert out["mode"] == STATE


def test_failing_part_is_logged_and_skipped(setup, monkeypatch):
    """A broken endpoint is named in the log with the server's answer; the state still goes out."""
    import threading as th
    mod, fb, _ = setup
    real_do_get = LocalApi.do_GET

    def broken(self):
        if self.path.startswith("/api/analytics/report"):
            body = b'{"detail": "ValueError: zly plik"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        real_do_get(self)

    monkeypatch.setattr(LocalApi, "do_GET", broken)
    key = "k" * 24  # the worker needs a key of 20+ characters
    mod._key = key
    mod._config.enabled = True
    mod._config.state_every_s = 1
    t = th.Thread(target=mod._run, daemon=True)
    t.start()
    import time as tm
    deadline = tm.time() + 15
    while tm.time() < deadline and "config" not in (fb.root.get("furnace", {}).get(key) or {}):
        tm.sleep(0.2)
    mod._config.enabled = False
    data = fb.root["furnace"][key]
    assert json.loads(data["state"]) == STATE           # the state went out
    assert "config" in data and "report" not in data   # other parts too, only the report is missing
    msgs = [e.message for e in mod._problems.take_events()]
    assert any("statystyki" in m and "/api/analytics/report" in m and "HTTP 500" in m and "zly plik" in m for m in msgs), msgs
