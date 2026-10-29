"""
Powiadomienia: chosen events of the controller sent to a phone through ntfy (the ntfy
app subscribes to a topic - a random name that works as a password, generated on the
first start). Only what is ticked in the settings goes out; at first only the flame-out.
Sent in a background thread with a few retries - the control never waits for the
internet. Controls nothing. If the Pi itself is down nothing can be sent - that has to
be watched from outside.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import string
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

log = logging.getLogger(__name__)


@dataclass
class NotifyConfig:
    enabled: bool = True
    server: str = "https://ntfy.sh"
    topic: str = ""                     # generated on the first start
    send_flameout: bool = True          # the fire went out - the furnace stopped
    send_fading: bool = False           # the fire fades at full power (check the hopper)
    send_overheat: bool = False         # boiler / hopper overheating
    send_sensor: bool = False           # a sensor stopped reading
    send_module_error: bool = False     # a module failed
    send_test: bool = False             # set in the settings: one test message, then back to off


# event type -> (setting, title, ntfy priority 1-5, tags)
KINDS: Dict[str, Tuple[str, str, int, str]] = {
    "FLAMEOUT": ("send_flameout", "Piec wygasł", 5, "rotating_light,fire"),
    "FLAMEOUT_WARNING": ("send_fading", "Ogień słabnie", 4, "warning"),
    "BOILER_OVERHEAT_ON": ("send_overheat", "Przegrzanie kotła", 5, "rotating_light,hot_face"),
    "HOPPER_OVERHEAT_ON": ("send_overheat", "Przegrzanie podajnika", 5, "rotating_light,hot_face"),
    "MODULE_ERROR": ("send_module_error", "Błąd modułu", 3, "warning"),
    "AUX_MODULE_ERROR": ("send_module_error", "Błąd modułu", 3, "warning"),
}
SAME_KIND_S = 300.0                    # the same kind at most once in this long (a module failing every tick)
RETRY_S = (30.0, 60.0, 120.0, 300.0, 600.0)
BOOL_KEYS = ("enabled", "send_flameout", "send_fading", "send_overheat", "send_sensor", "send_module_error", "send_test")


def new_topic() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "piec-" + "".join(secrets.choice(alphabet) for _ in range(16))


def post_ntfy(server: str, payload: Dict[str, Any], timeout: float = 10.0) -> None:
    """JSON publishing - UTF-8 titles (ł, ę) don't fit in HTTP headers."""
    req = urllib.request.Request(server.rstrip("/"), data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def on_the_real_furnace() -> bool:
    """Only the Pi sends - the simulator (tests, the cloud test environment) never does."""
    return (os.getenv("FURNACE_BRAIN_HW_RPI") or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def log_only(server: str, payload: Dict[str, Any]) -> None:
    log.info("ntfy (simulator, not sent): %s", payload.get("title"))


class NotifyModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, config: Optional[NotifyConfig] = None,
                 data_root: Optional[Path] = None,
                 sender: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = config or NotifyConfig()
        self._load_config_from_file()
        if not self._config.topic:
            self._config.topic = new_topic()
            self._save()
        self._send = sender or (post_ntfy if on_the_real_furnace() else log_only)
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=50)
        self._last_kind: Dict[str, float] = {}
        self._results: List[Event] = []      # sent / failed - reported on the next tick
        self._lock = threading.Lock()
        self._last_sent: Optional[float] = None
        self._last_error: Optional[str] = None
        threading.Thread(target=self._worker, daemon=True, name="notify").start()

    @property
    def id(self) -> str:
        return "notify"

    # ---------- the events ----------

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        c = self._config
        if c.send_test:
            self._enqueue("TEST", "Test powiadomień", "Powiadomienia z pieca działają.", 3, "white_check_mark", now)
            c.send_test = False
            self._save()
        if c.enabled:
            for e in system_state.recent_events or []:
                kind = self._kind(e)
                if kind is None:
                    continue
                setting, title, prio, tags = kind
                if not getattr(c, setting, False):
                    continue
                key = e.type if not e.type.startswith("SENSOR_") else "SENSOR"
                if now - self._last_kind.get(key, -1e18) < SAME_KIND_S:
                    continue
                self._last_kind[key] = now
                self._enqueue(e.type, title, e.message, prio, tags, now)
        with self._lock:
            events, self._results = self._results, []
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        runtime = {"enabled": bool(c.enabled), "topic": c.topic, "server": c.server,
                   "last_sent": self._last_sent, "last_error": self._last_error}
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    @staticmethod
    def _kind(e: Event) -> Optional[Tuple[str, str, int, str]]:
        if e.type in KINDS:
            return KINDS[e.type]
        if e.type.startswith("SENSOR_") and e.type.endswith("_MISSING_ON"):
            return ("send_sensor", "Brak odczytu czujnika", 4, "warning")
        return None

    def _enqueue(self, etype: str, title: str, message: str, prio: int, tags: str, now: float) -> None:
        payload = {"topic": self._config.topic, "title": title, "message": message,
                   "priority": prio, "tags": [t for t in tags.split(",") if t]}
        try:
            self._queue.put_nowait({"payload": payload, "server": self._config.server, "type": etype, "at": now})
        except queue.Full:
            pass

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            for attempt, wait in enumerate((0.0,) + RETRY_S):
                if wait:
                    time.sleep(wait)
                try:
                    self._send(item["server"], item["payload"])
                    with self._lock:
                        self._last_sent, self._last_error = time.time(), None
                        self._results.append(Event(
                            ts=time.time(), source=self.id, level=EventLevel.INFO, type="NOTIFY_SENT",
                            message=f"Powiadomienie wysłane: {item['payload']['title']}",
                            data={"type": item["type"], "attempt": attempt + 1}))
                    break
                except Exception as exc:  # pylint: disable=broad-except
                    err = f"{type(exc).__name__}: {exc}"
                    with self._lock:
                        self._last_error = err
                    log.warning("ntfy: %s (attempt %d)", err, attempt + 1)
            else:
                with self._lock:
                    self._results.append(Event(
                        ts=time.time(), source=self.id, level=EventLevel.WARNING, type="NOTIFY_ERROR",
                        message=f"Nie udało się wysłać powiadomienia „{item['payload']['title']}”: {self._last_error}",
                        data={"type": item["type"]}))

    # ---------- settings ----------

    def _save(self) -> None:
        try:
            with self._config_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(asdict(self._config), f, sort_keys=True, allow_unicode=True)
        except OSError:
            pass

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
            self._save()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            self._apply(yaml.safe_load(f) or {})

    def _apply(self, values: Dict[str, Any]) -> None:
        for name in BOOL_KEYS:
            if name in values:
                setattr(self._config, name, bool(values[name]))
        for name in ("server", "topic"):
            if name in values and values[name] not in (None, ""):
                setattr(self._config, name, str(values[name]).strip())
