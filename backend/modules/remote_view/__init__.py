from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

PAGES_URL = "https://lecztomek.github.io/furnace-brain/"


@dataclass
class RemoteViewConfig:
    """
    Sends the furnace data to Firebase Realtime Database for the remote
    read-only view (GUI on GitHub Pages). Connection data comes from the
    environment (/etc/furnace-brain.env):
      FURNACE_BRAIN_VIEWER_FIREBASE_URL     https://<project>-default-rtdb.<region>.firebasedatabase.app
      FURNACE_BRAIN_VIEWER_FIREBASE_SECRET  database secret (write access)
      FURNACE_BRAIN_VIEWER_KEY              random read key - part of the view link
    """
    enabled: bool = False
    state_every_s: float = 15.0      # current state (main page)
    history_every_s: float = 60.0    # new history rows and log entries
    slow_every_min: float = 10.0     # statistics, settings, 5 min history
    first_history_days: float = 2.0  # raw history sent to an empty database
    keep_days: float = 14.0          # older data is deleted from Firebase


def js(value: Any) -> str:
    """
    Every value is stored as a JSON string: Firebase drops empty lists/dicts,
    turns lists into objects and forbids some characters in keys - the GUI
    must get exactly what the local API returns.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def time_key(data_czas: str) -> str:
    return data_czas[11:19].replace(":", "")  # "2026-10-26T10:30:00" -> "103000"


class RemoteViewModule(ModuleInterface):
    """
    Aux module. All network work happens in its own background thread that
    reads the local API (what the GUI sees) and writes it to Firebase in one
    PATCH per cycle - the control loop and the aux loop are never blocked.
    Only sends, never receives commands.
    """

    def __init__(self, base_path: Path | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = RemoteViewConfig()
        self._load_config_from_file()

        self._db = os.getenv("FURNACE_BRAIN_VIEWER_FIREBASE_URL", "").rstrip("/")
        self._secret = os.getenv("FURNACE_BRAIN_VIEWER_FIREBASE_SECRET", "")
        self._key = os.getenv("FURNACE_BRAIN_VIEWER_KEY", "")
        self._local = f"http://127.0.0.1:{os.getenv('BACKEND_PORT', '8000')}"

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._events: List[Event] = []
        self._error: Optional[str] = None
        self._last_ok: Optional[float] = None

    @property
    def id(self) -> str:
        return "remote_view"

    @property
    def _configured(self) -> bool:
        return bool(self._db and self._secret and self._key)

    # ---------- tick (aux loop) ----------

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        if self._config.enabled and self._configured and self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="remote_view")
            self._thread.start()
        with self._lock:
            events, self._events = self._events, []
            runtime = {"configured": self._configured, "enabled": self._config.enabled,
                       "last_ok": self._last_ok, "error": self._error}
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    # ---------- worker thread ----------

    def _run(self) -> None:
        if len(self._key) < 20:
            # Firebase rules (viewer/firebase-rules.json) don't allow reading short keys
            self._fail(ValueError("FURNACE_BRAIN_VIEWER_KEY musi mieć co najmniej 20 znaków"))
            return
        cursors: Optional[Dict[str, Any]] = None
        next_state = next_history = next_slow = 0.0
        while True:
            if not self._config.enabled:
                time.sleep(5)
                continue
            now = time.time()
            try:
                if cursors is None:
                    cursors = self._fb_get("cursors") or {}
                if now >= next_state:
                    patch: Dict[str, Any] = {
                        "state": js(self._get("/api/state/current")),
                        "manual": js(self._get("/api/manual/current")),
                        "updated_at": now,
                    }
                    if now >= next_history:
                        patch.update(self._increments(cursors))
                        next_history = now + self._config.history_every_s
                    if now >= next_slow:
                        patch.update(self._slow())
                        self._cleanup()
                        next_slow = now + self._config.slow_every_min * 60
                    patch["cursors"] = cursors
                    self._fb_patch(patch)
                    next_state = now + self._config.state_every_s
                    self._ok()
                time.sleep(1)
            except Exception as exc:  # pylint: disable=broad-except
                self._fail(exc)
                cursors = None          # re-read what Firebase really has
                next_state = time.time() + 60  # back off
                next_history = next_slow = 0.0
                time.sleep(5)

    def _increments(self, cursors: Dict[str, Any]) -> Dict[str, Any]:
        """New history rows and log entries since the cursors (updated in place)."""
        out: Dict[str, Any] = {}
        start = cursors.get("history") or (datetime.now() - timedelta(days=self._config.first_history_days)).isoformat(timespec="seconds")
        # upper bound in the future: tolerates clock skew (and the simulator's faster clock)
        end = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
        rows = self._get("/api/history/data?" + urllib.parse.urlencode({"from_ts": start, "to_ts": end})).get("items", [])
        rows = [r for r in rows if r.get("data_czas", "") > (cursors.get("history") or "")][:3000]
        for r in rows:
            out[f"history/{r['data_czas'][:10]}/{time_key(r['data_czas'])}"] = js(r)
        if rows:
            cursors["history"] = rows[-1]["data_czas"]
            fields: List[str] = []
            for r in rows:
                fields.extend(k for k in r if k not in fields)
            out["history_fields"] = js(fields)

        logs = self._get("/api/logs/recent?limit=2000").get("items", [])
        last_ts = cursors.get("logs") or 0.0
        logs = sorted((i for i in logs if (i.get("ts") or 0) > last_ts), key=lambda i: i["ts"])
        for i in logs:
            out[f"logs/{datetime.fromtimestamp(i['ts']).date().isoformat()}/{int(i['ts'] * 1000)}"] = js(i)
        if logs:
            cursors["logs"] = logs[-1]["ts"]
        return out

    def _slow(self) -> Dict[str, Any]:
        modules = self._get("/api/config/modules")
        out: Dict[str, Any] = {
            "config/modules": js(modules),
            "logs_meta": js(self._get("/api/logs/meta")),
            "report": js(self._get("/api/analytics/report")),
        }
        for m in modules:
            mid = urllib.parse.quote(m["id"])
            out[f"config/schemas/{m['id']}"] = js(self._get(f"/api/config/schema/{mid}"))
            out[f"config/values/{m['id']}"] = js(self._get(f"/api/config/values/{mid}"))

        # 5 min averages per day for long ranges (7 days = ~2000 points instead of ~20000)
        have = self._fb_get("history5m", shallow=True) or {}
        today = date.today()
        for back in range(int(self._config.keep_days)):
            d = today - timedelta(days=back)
            if back > 0 and d.isoformat() in have:
                continue  # past days don't change
            query = urllib.parse.urlencode({"from_ts": f"{d}T00:00:00", "to_ts": f"{d}T23:59:59", "max_points": 288})
            items = self._get(f"/api/history/data?{query}").get("items", [])
            if items:
                out[f"history5m/{d}"] = {time_key(r["data_czas"]): js(r) for r in items}
        return out

    def _cleanup(self) -> None:
        oldest = (date.today() - timedelta(days=int(self._config.keep_days))).isoformat()
        for node in ("history", "history5m", "logs"):
            for day in (self._fb_get(node, shallow=True) or {}):
                if day < oldest:
                    self._fb_request("DELETE", f"{node}/{day}")

    # ---------- HTTP ----------

    def _get(self, path: str) -> Any:
        with urllib.request.urlopen(self._local + path, timeout=30) as resp:
            return json.load(resp)

    def _fb_url(self, path: str, **params: str) -> str:
        query = urllib.parse.urlencode({"auth": self._secret, **params})
        return f"{self._db}/furnace/{self._key}/{path}.json?{query}"

    def _fb_request(self, method: str, path: str, body: Any = None, **params: str) -> Any:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(self._fb_url(path, **params), data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)

    def _fb_get(self, path: str, shallow: bool = False) -> Any:
        return self._fb_request("GET", path, **({"shallow": "true"} if shallow else {}))

    def _fb_patch(self, patch: Dict[str, Any]) -> None:
        # one multi-path update per cycle; root of this furnace's data
        req = urllib.request.Request(
            f"{self._db}/furnace/{self._key}.json?" + urllib.parse.urlencode({"auth": self._secret}),
            data=json.dumps(patch, separators=(",", ":")).encode("utf-8"),
            method="PATCH", headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()

    # ---------- status events ----------

    def _ok(self) -> None:
        with self._lock:
            if self._error is not None or self._last_ok is None:
                self._events.append(Event(
                    ts=time.time(), source=self.id, level=EventLevel.INFO, type="REMOTE_VIEW_OK",
                    message=f"Podgląd zdalny: wysyłam dane, link: {PAGES_URL}#k=<FURNACE_BRAIN_VIEWER_KEY>", data={}))
            self._error = None
            self._last_ok = time.time()

    def _fail(self, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}".replace(self._secret, "***") if self._secret else str(exc)
        with self._lock:
            if self._error is None:
                self._events.append(Event(ts=time.time(), source=self.id, level=EventLevel.WARNING,
                                          type="REMOTE_VIEW_ERROR", message=f"Podgląd zdalny: błąd wysyłania: {error}",
                                          data={"error": error}))
            self._error = error

    # ---------- config ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        self._apply(values)
        if persist:
            with self._config_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(asdict(self._config), f, sort_keys=True, allow_unicode=True)

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()

    def _load_config_from_file(self) -> None:
        if self._config_path.exists():
            with self._config_path.open("r", encoding="utf-8") as f:
                self._apply(yaml.safe_load(f) or {})

    def _apply(self, values: Dict[str, Any]) -> None:
        for f in fields(RemoteViewConfig):
            if f.name in values:
                setattr(self._config, f.name, type(getattr(self._config, f.name))(values[f.name]))
