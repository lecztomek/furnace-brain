from __future__ import annotations

import gzip
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState


@dataclass
class RemoteViewConfig:
    """
    Sends the furnace data to the remote read-only view (backend/viewer.py).
    Server address and token come from the environment (/etc/furnace-brain.env):
    FURNACE_BRAIN_VIEWER_URL, FURNACE_BRAIN_VIEWER_TOKEN.
    """
    enabled: bool = False
    state_every_s: float = 15.0      # current state (main page)
    history_every_s: float = 60.0    # new history rows and log entries
    slow_every_min: float = 10.0     # statistics report and settings
    first_history_days: float = 2.0  # how much history to send to an empty viewer


class RemoteViewModule(ModuleInterface):
    """
    Aux module. All network work happens in its own background thread that
    reads the local API (what the GUI sees) and posts it to the viewer - the
    control loop and the aux loop are never blocked. Only sends, never receives
    commands.
    """

    def __init__(self, base_path: Path | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = RemoteViewConfig()
        self._load_config_from_file()

        self._url = os.getenv("FURNACE_BRAIN_VIEWER_URL", "").rstrip("/")
        self._token = os.getenv("FURNACE_BRAIN_VIEWER_TOKEN", "")
        self._local = f"http://127.0.0.1:{os.getenv('BACKEND_PORT', '8000')}"

        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._events: List[Event] = []
        self._error: Optional[str] = None
        self._last_ok: Optional[float] = None

    @property
    def id(self) -> str:
        return "remote_view"

    # ---------- tick (aux loop) ----------

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        if self._config.enabled and self._url and self._token and self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="remote_view")
            self._thread.start()
        with self._lock:
            events, self._events = self._events, []
            runtime = {
                "configured": bool(self._url and self._token),
                "enabled": self._config.enabled,
                "last_ok": self._last_ok,
                "error": self._error,
            }
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    # ---------- worker thread ----------

    def _run(self) -> None:
        cursors: Dict[str, Any] = {"history": None, "logs": None}
        next_state = next_history = next_slow = 0.0
        while True:
            if not self._config.enabled:
                time.sleep(5)
                continue
            now = time.time()
            try:
                if now >= next_state:
                    body: Dict[str, Any] = {"state": self._get("/api/state/current"),
                                            "manual": self._get("/api/manual/current")}
                    if now >= next_history:
                        body.update(self._increments(cursors))
                        next_history = now + self._config.history_every_s
                    if now >= next_slow:
                        body.update(self._slow())
                        next_slow = now + self._config.slow_every_min * 60
                    reply = self._push(body)
                    cursors["history"] = reply.get("history_last")
                    cursors["logs"] = reply.get("logs_last")
                    next_state = now + self._config.state_every_s
                    self._ok()
                time.sleep(1)
            except Exception as exc:  # pylint: disable=broad-except
                self._fail(exc)
                next_state = time.time() + 60  # back off
                next_history = next_slow = 0.0  # resend after recovery
                time.sleep(5)

    def _increments(self, cursors: Dict[str, Any]) -> Dict[str, Any]:
        start = cursors["history"] or (datetime.now() - timedelta(days=self._config.first_history_days)).isoformat(timespec="seconds")
        # upper bound in the future: tolerates clock skew (and the simulator's faster clock)
        end = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
        query = urllib.parse.urlencode({"from_ts": start, "to_ts": end})
        rows = self._get(f"/api/history/data?{query}").get("items", [])
        if cursors["history"]:
            rows = [r for r in rows if r.get("data_czas", "") > cursors["history"]]

        logs = self._get("/api/logs/recent?limit=2000").get("items", [])
        if cursors["logs"] is not None:
            logs = [i for i in logs if (i.get("ts") or 0) > cursors["logs"]]
        return {"history_rows": rows[:5000], "log_items": logs}

    def _slow(self) -> Dict[str, Any]:
        modules = self._get("/api/config/modules")
        config = {"modules": modules, "schemas": {}, "values": {}}
        for m in modules:
            mid = urllib.parse.quote(m["id"])
            config["schemas"][m["id"]] = self._get(f"/api/config/schema/{mid}")
            config["values"][m["id"]] = self._get(f"/api/config/values/{mid}")
        return {"config": config, "logs_meta": self._get("/api/logs/meta"),
                "report": self._get("/api/analytics/report")}

    def _get(self, path: str) -> Any:
        with urllib.request.urlopen(self._local + path, timeout=30) as resp:
            return json.load(resp)

    def _push(self, body: Dict[str, Any]) -> Dict[str, Any]:
        # gzip: the JSON is repetitive, ~10x smaller on the wire
        req = urllib.request.Request(
            f"{self._url}/api/viewer/push",
            data=gzip.compress(json.dumps(body, separators=(",", ":")).encode("utf-8")),
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip",
                     "Authorization": f"Bearer {self._token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

    def _ok(self) -> None:
        with self._lock:
            if self._error is not None or self._last_ok is None:
                self._events.append(Event(ts=time.time(), source=self.id, level=EventLevel.INFO,
                                          type="REMOTE_VIEW_OK", message=f"Podgląd zdalny: wysyłam dane do {self._url}", data={}))
            self._error = None
            self._last_ok = time.time()

    def _fail(self, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"
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
