"""
Temperatura w domu: reads the room thermometer over WiFi (Tasmota with a DS18B20, or an
ESPHome sensor page) in a background thread and publishes it as runtime["room"]; the
history writes it (temp_wewn). Only recorded - nothing in the furnace control reads it.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.problems import ProblemTracker, describe_error
from backend.core.state import Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

TIMEOUT_S = 5.0
VALID_MIN_C, VALID_MAX_C = -20.0, 50.0     # a reading outside this is a broken sensor


@dataclass
class RoomConfig:
    enabled: bool = True
    address: str = ""             # e.g. 192.168.1.50 (Tasmota) or a full http:// address
    refresh_s: float = 60.0
    max_age_min: float = 10.0     # an older reading is not used


def reading_url(address: str) -> str:
    """A bare address is a Tasmota device (its sensor status); a full http:// address is used as is."""
    address = address.strip()
    if address.startswith(("http://", "https://")):
        return address
    return f"http://{address.rstrip('/')}/cm?cmnd=Status%2010"


def parse_temperature(data: Any) -> float:
    """The temperature from Tasmota's status (StatusSNS / DS18B20...) or an ESPHome sensor ("value")."""
    if isinstance(data, dict):
        sns = data.get("StatusSNS", data)
        if isinstance(sns, dict):
            fahrenheit = str(sns.get("TempUnit", "C")).upper() == "F"
            for value in sns.values():
                if isinstance(value, dict) and value.get("Temperature") is not None:
                    t = float(value["Temperature"])
                    return (t - 32.0) / 1.8 if fahrenheit else t
        if isinstance(data.get("value"), (int, float)):
            return float(data["value"])
    raise ValueError("brak temperatury w odpowiedzi termometru")


def fetch_room_temp(address: str, timeout_s: float = TIMEOUT_S) -> float:
    with urllib.request.urlopen(reading_url(address), timeout=timeout_s) as resp:
        return parse_temperature(json.load(resp))


class RoomModule(ModuleInterface):
    def __init__(
        self,
        base_path: Optional[Path] = None,
        data_root: Optional[Path] = None,
        fetcher: Optional[Callable[[str], float]] = None,
    ) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = RoomConfig()
        self._load_config_from_file()

        self._fetcher = fetcher or fetch_room_temp
        self._lock = threading.Lock()
        self._fetching = False
        self._next_attempt = 0.0          # wall clock
        self._temp: Optional[float] = None
        self._fetched_at: Optional[float] = None
        self._error: Optional[str] = None
        self._pending_events: List[Event] = []
        self._problems = ProblemTracker(self.id, "Temperatura w domu")

    @property
    def id(self) -> str:
        return "room"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        wall = time.time()
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        cfg = self._config
        if not cfg.enabled or not cfg.address.strip():
            # not set up yet - nothing to read, nothing to complain about
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=[], status=status, runtime={})

        with self._lock:
            start = not self._fetching and wall >= self._next_attempt
            if start:
                self._fetching = True
            events, self._pending_events = self._pending_events + self._problems.take_events(), []
            runtime = {
                "temp": self._temp,
                "fetched_at": self._fetched_at,
                "valid_until": (self._fetched_at or 0.0) + cfg.max_age_min * 60.0,
                "error": self._error,
            }

        if start:
            threading.Thread(target=self._fetch, args=(cfg.address,), daemon=True, name="room_fetch").start()

        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    def _fetch(self, address: str) -> None:
        try:
            temp = float(self._fetcher(address))
            if not VALID_MIN_C <= temp <= VALID_MAX_C:
                raise ValueError(f"odczyt poza zakresem: {temp:.1f}°C")
            error = None
        except Exception as exc:  # pylint: disable=broad-except
            temp, error = None, describe_error(exc, f"termometr ({address})")

        wall = time.time()
        with self._lock:
            if error is None:
                if self._fetched_at is None:
                    self._pending_events.append(Event(
                        ts=wall, source=self.id, level=EventLevel.INFO, type="ROOM_OK",
                        message=f"Temperatura w domu: {temp:.1f}°C", data={"temp": temp},
                    ))
                self._problems.ok("odczyt", "ROOM_OK")
                self._temp, self._fetched_at, self._error = round(temp, 2), wall, None
            else:
                self._problems.failed("odczyt", error, "ROOM_FETCH_ERROR")
                self._error = error
            self._next_attempt = wall + max(10.0, self._config.refresh_s)
            self._fetching = False

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
        with self._lock:
            self._next_attempt = 0.0          # e.g. a new address - read right away
            self._temp, self._fetched_at = None, None

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            self._apply(yaml.safe_load(f) or {})

    def _apply(self, values: Dict[str, Any]) -> None:
        for f in fields(RoomConfig):
            if f.name in values and values[f.name] is not None:
                cast = {bool: bool, float: float, str: str}[type(getattr(self._config, f.name))]
                setattr(self._config, f.name, cast(values[f.name]))
