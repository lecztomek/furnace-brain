from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.problems import ProblemTracker, describe_error
from backend.core.state import BoilerMode, Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

PAGES_URL = "https://lecztomek.github.io/furnace-brain/"


@dataclass
class AlertsConfig:
    """
    Push notifications to the phone through ntfy (free app, no account).
    The topic comes from the environment (/etc/furnace-brain.env):
      FURNACE_BRAIN_NTFY_TOPIC   random topic name - subscribe to it in the ntfy app
      FURNACE_BRAIN_NTFY_SERVER  optional, default https://ntfy.sh
    """
    enabled: bool = False
    # cold flue gas while the boiler works hard = no coal reaches the fire
    # (stuck feeder / coal hanging in the hopper). On last season's data
    # this caught 10 of 11 fires going out, ~3 alarms a week.
    flue_alarm_enabled: bool = True
    flue_below_c: float = 40.0        # flue gas, 5 min average
    power_above_pct: float = 70.0     # boiler power, 10 min average
    repeat_min: float = 5.0           # reminders while it lasts
    forward_alarms: bool = True       # also send the controller's ALARM events (overheating, sensors)


FLUE_WINDOW_S = 300.0
POWER_WINDOW_S = 600.0
CLEAR_FLUE_MARGIN_C = 5.0      # alarm ends when the flue gas is this much above the threshold
CLEAR_POWER_MARGIN_PCT = 10.0  # ... or the power drops this much below it
FORWARD_REPEAT_S = 300.0       # the same ALARM event type at most every 5 min


def send_ntfy(server: str, topic: str, title: str, message: str, priority: int, tags: str,
              click: Optional[str] = None, timeout_s: float = 20.0) -> None:
    """JSON publish (UTF-8 safe, unlike headers): https://docs.ntfy.sh/publish/#publish-as-json"""
    body: Dict[str, Any] = {"topic": topic, "title": title, "message": message, "priority": priority,
                            "tags": [t for t in tags.split(",") if t]}
    if click:
        body["click"] = click
    req = urllib.request.Request(server.rstrip("/"), data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        resp.read()


def _avg(samples: Deque[Tuple[float, float]], now: float, window: float) -> Optional[float]:
    """Average of the window, None until at least 80 % of it is covered."""
    while samples and samples[0][0] < now - window:
        samples.popleft()
    if not samples or now - samples[0][0] < 0.8 * window:
        return None
    return sum(v for _, v in samples) / len(samples)


class AlertsModule(ModuleInterface):
    """
    Aux module: watches the state, never controls anything. Messages go out
    from a background thread, so a slow network never blocks the loop.
    """

    def __init__(self, base_path: Path | None = None, sender: Callable[..., None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = AlertsConfig()
        self._load_config_from_file()

        self._topic = os.getenv("FURNACE_BRAIN_NTFY_TOPIC", "").strip()
        self._server = os.getenv("FURNACE_BRAIN_NTFY_SERVER", "https://ntfy.sh").strip()
        viewer_key = os.getenv("FURNACE_BRAIN_VIEWER_KEY", "").strip()
        self._click = f"{PAGES_URL}#k={viewer_key}" if viewer_key else None
        self._sender = sender or send_ntfy
        self._clock = clock

        self._flue: Deque[Tuple[float, float]] = deque()
        self._power: Deque[Tuple[float, float]] = deque()
        self._flue_active = False
        self._flue_since = 0.0
        self._flue_last_sent = 0.0
        self._forwarded: Dict[str, float] = {}
        self._announced = False

        self._lock = threading.Lock()
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=50)
        self._problems = ProblemTracker(self.id, "Powiadomienia")
        self._thread: Optional[threading.Thread] = None

    @property
    def id(self) -> str:
        return "alerts"

    # ---------- tick (aux loop) ----------

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        events: List[Event] = []
        wall = self._clock()
        active = self._config.enabled and bool(self._topic)

        if active and self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="alerts_send")
            self._thread.start()
        if active and not self._announced:
            self._announced = True
            self._notify("Powiadomienia włączone",
                         "Ten telefon będzie dostawał alarmy pieca.", 3, "white_check_mark")
        if self._config.enabled and not self._topic and not self._announced:
            self._announced = True
            with self._lock:
                self._problems.failed("konfiguracja", "brak FURNACE_BRAIN_NTFY_TOPIC w /etc/furnace-brain.env", "ALERTS_ERROR")

        if active:
            if self._config.flue_alarm_enabled:
                events += self._check_flue(wall, sensors, system_state)
            if self._config.forward_alarms:
                self._forward(wall, system_state.recent_events or [])

        with self._lock:
            events += self._problems.take_events()
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status,
                                runtime={"configured": bool(self._topic), "flue_alarm": self._flue_active})

    def _check_flue(self, wall: float, sensors: Sensors, st: SystemState) -> List[Event]:
        in_work = st.mode == BoilerMode.WORK
        if not in_work:
            self._flue.clear()
            self._power.clear()
        else:
            if sensors.flue_gas_temp is not None:
                self._flue.append((wall, float(sensors.flue_gas_temp)))
            if st.outputs.power_percent is not None:
                self._power.append((wall, float(st.outputs.power_percent)))
        flue = _avg(self._flue, wall, FLUE_WINDOW_S)
        power = _avg(self._power, wall, POWER_WINDOW_S)
        cfg = self._config
        boiler = sensors.boiler_temp
        events: List[Event] = []

        if not self._flue_active:
            if in_work and flue is not None and power is not None \
                    and flue < cfg.flue_below_c and power >= cfg.power_above_pct:
                self._flue_active, self._flue_since, self._flue_last_sent = True, wall, wall
                text = (f"Spaliny {flue:.0f}°C przy mocy {power:.0f}% - węgiel nie dochodzi do paleniska? "
                        f"Sprawdź ślimak i zasobnik. Kocioł {self._fmt(boiler)}.")
                self._notify("⚠ Piec: zimne spaliny", text, 5, "warning,fire")
                events.append(self._event(EventLevel.ALARM, "ALERT_FLUE_LOW", f"Alarm wysłany na telefon: {text}",
                                          {"flue": flue, "power": power, "boiler": boiler}))
            return events

        cleared = (not in_work or (flue is not None and flue >= cfg.flue_below_c + CLEAR_FLUE_MARGIN_C)
                   or (power is not None and power < cfg.power_above_pct - CLEAR_POWER_MARGIN_PCT))
        mins = round((wall - self._flue_since) / 60)
        if cleared:
            self._flue_active = False
            why = "piec nie jest w trybie PRACA" if not in_work else f"spaliny {self._fmt(flue, 0)}, moc {self._fmt(power, 0, '%')}"
            text = f"Koniec alarmu po {mins} min: {why}. Kocioł {self._fmt(boiler)}."
            self._notify("✓ Piec: spaliny OK", text, 3, "white_check_mark")
            events.append(self._event(EventLevel.INFO, "ALERT_FLUE_OK", f"Powiadomienie: {text}", {"minutes": mins}))
        elif wall - self._flue_last_sent >= cfg.repeat_min * 60:
            self._flue_last_sent = wall
            text = (f"Nadal (od {mins} min): spaliny {self._fmt(flue, 0)} przy mocy {self._fmt(power, 0, '%')}. "
                    f"Kocioł {self._fmt(boiler)}.")
            self._notify("⚠ Piec: zimne spaliny", text, 5, "warning,fire")
        return events

    def _forward(self, wall: float, events: List[Event]) -> None:
        for ev in events:
            if ev.level != EventLevel.ALARM or ev.source == self.id:
                continue
            if wall - self._forwarded.get(ev.type, 0.0) < FORWARD_REPEAT_S:
                continue
            self._forwarded[ev.type] = wall
            self._notify(f"⚠ Piec: {ev.source}", ev.message, 5, "warning")

    # ---------- sending (background thread) ----------

    def _notify(self, title: str, message: str, priority: int, tags: str) -> None:
        try:
            self._queue.put_nowait({"title": title, "message": message, "priority": priority, "tags": tags})
        except queue.Full:
            pass

    def _run(self) -> None:
        while True:
            msg = self._queue.get()
            for attempt in range(3):
                try:
                    self._sender(self._server, self._topic, msg["title"], msg["message"],
                                 msg["priority"], msg["tags"], self._click)
                    with self._lock:
                        self._problems.ok("wysyłanie", "ALERTS_OK")
                    break
                except Exception as exc:  # pylint: disable=broad-except
                    with self._lock:
                        self._problems.failed("wysyłanie", describe_error(exc, f"ntfy ({self._server})"), "ALERTS_ERROR")
                    time.sleep(10 * (attempt + 1))

    # ---------- helpers ----------

    @staticmethod
    def _fmt(v: Optional[float], digits: int = 1, unit: str = "°C") -> str:
        return "--" if v is None else f"{v:.{digits}f}{unit}"

    def _event(self, level: EventLevel, type_: str, message: str, data: Dict[str, Any]) -> Event:
        return Event(ts=self._clock(), source=self.id, level=level, type=type_, message=message, data=data)

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
        for f in fields(AlertsConfig):
            if f.name in values:
                setattr(self._config, f.name, type(getattr(self._config, f.name))(values[f.name]))
