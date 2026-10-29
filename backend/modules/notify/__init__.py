"""
Powiadomienia: chosen events of the controller sent to a phone through ntfy (the ntfy
app subscribes to a topic - a name that works as a password: on the Pi made from the
remote view key, so it is known before the first start and never in this public
repository; random without that key). Only what is ticked in the settings goes out; at first only the flame-out.
Sent in a background thread with a few retries - the control never waits for the
internet. Controls nothing. If the Pi itself is down nothing can be sent - that has to
be watched from outside.
"""
from __future__ import annotations

import hashlib
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
    send_test: bool = False             # set in the settings: one test message, then back to off
    # each event: "nie" (not sent) or how loud on the phone (LEVELS)
    flameout: str = "alarm"             # the fire went out - the furnace stopped
    fading: str = "nie"                 # the fire fades at full power (check the hopper)
    overheat: str = "nie"               # boiler / hopper overheating
    sensor: str = "nie"                 # a sensor stopped reading
    module_error: str = "nie"           # a module failed


# the loudness chosen in the settings -> ntfy priority (2 no sound ... 5 urgent: long, also at night)
LEVELS = {"cicho": 2, "zwykle": 3, "głośno": 4, "alarm": 5}
WHAT = ("flameout", "fading", "overheat", "sensor", "module_error")
# up to 1.54: send_<what> on/off - "on" becomes this loudness (once, then the old keys are dropped)
OLD_SEND_LEVEL = {"flameout": "alarm", "fading": "głośno", "overheat": "alarm", "sensor": "głośno",
                  "module_error": "zwykle"}
# event type -> (what: the setting, title, tags)
KINDS: Dict[str, Tuple[str, str, str]] = {
    "FLAMEOUT": ("flameout", "Piec wygasł", "rotating_light,fire"),
    "FLAMEOUT_WARNING": ("fading", "Ogień słabnie", "warning"),
    "BOILER_OVERHEAT_ON": ("overheat", "Przegrzanie kotła", "rotating_light,hot_face"),
    "HOPPER_OVERHEAT_ON": ("overheat", "Przegrzanie podajnika", "rotating_light,hot_face"),
    "MODULE_ERROR": ("module_error", "Błąd modułu", "warning"),
    "AUX_MODULE_ERROR": ("module_error", "Błąd modułu", "warning"),
}
# the settings in words, for the diagnostics
LABELS = {"flameout": "wygaśnięcie", "fading": "ogień słabnie", "overheat": "przegrzanie",
          "sensor": "brak czujnika", "module_error": "błąd modułu"}
SAME_KIND_S = 300.0                    # the same kind at most once in this long (a module failing every tick)
RETRY_S = (30.0, 60.0, 120.0, 300.0, 600.0)
BOOL_KEYS = ("enabled", "send_test")


def new_topic() -> str:
    """From the remote view key when there is one (a secret that lives only on the Pi), else random."""
    key = (os.getenv("FURNACE_BRAIN_VIEWER_KEY") or "").strip()
    if len(key) >= 20:
        return "piec-" + hashlib.sha256(("ntfy:" + key).encode("utf-8")).hexdigest()[:16]
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
        self._simulated = sender is None and not on_the_real_furnace()
        self._send = sender or (post_ntfy if on_the_real_furnace() else log_only)
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=50)
        self._last_kind: Dict[str, float] = {}
        self._results: List[Event] = []      # sent / failed - reported on the next tick
        self._lock = threading.Lock()
        self._last_sent: Optional[float] = None
        self._last_title: Optional[str] = None
        self._last_error: Optional[str] = None      # the last attempt failed (retrying, or given up)
        self._failed: Optional[Dict[str, Any]] = None   # the last message given up on, until one goes through
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
                what, title, tags = kind
                prio = LEVELS.get(getattr(c, what, "nie"))
                if prio is None:
                    continue                    # "nie"
                key = e.type if not e.type.startswith("SENSOR_") else "SENSOR"
                if now - self._last_kind.get(key, -1e18) < SAME_KIND_S:
                    continue
                self._last_kind[key] = now
                self._enqueue(e.type, title, e.message, prio, tags, now)
        with self._lock:
            events, self._results = self._results, []
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        runtime = {"enabled": bool(c.enabled), "topic": c.topic, "server": c.server,
                   "sends": [f"{label} ({getattr(c, key)})" for key, label in LABELS.items()
                             if getattr(c, key, "nie") in LEVELS],
                   "simulated": self._simulated, "pending": self._queue.qsize(),
                   "last_sent": self._last_sent, "last_title": self._last_title,
                   "last_error": self._last_error, "failed": dict(self._failed) if self._failed else None}
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    @staticmethod
    def _kind(e: Event) -> Optional[Tuple[str, str, str]]:
        if e.type in KINDS:
            return KINDS[e.type]
        if e.type.startswith("SENSOR_") and e.type.endswith("_MISSING_ON"):
            return ("sensor", "Brak odczytu czujnika", "warning")
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
                        self._last_sent, self._last_error, self._failed = time.time(), None, None
                        self._last_title = item["payload"]["title"]
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
                    self._failed = {"at": time.time(), "title": item["payload"]["title"], "error": self._last_error}
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
            values = yaml.safe_load(f) or {}
        old = [w for w in WHAT if "send_" + w in values]
        for w in old:                           # up to 1.54: on/off - kept as the user had it
            values[w] = OLD_SEND_LEVEL[w] if values["send_" + w] else "nie"
        self._apply(values)
        if old:
            self._save()                        # written without the old keys - taken over once

    def _apply(self, values: Dict[str, Any]) -> None:
        for name in BOOL_KEYS:
            if name in values:
                setattr(self._config, name, bool(values[name]))
        for name in ("server", "topic"):
            if name in values and values[name] not in (None, ""):
                setattr(self._config, name, str(values[name]).strip())
        for name in WHAT:
            if values.get(name) == "nie" or values.get(name) in LEVELS:
                setattr(self._config, name, values[name])
