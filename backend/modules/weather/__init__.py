from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    EventLevel,
    ModuleStatus,
    PartialOutputs,
    Sensors,
    SystemState,
)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class WeatherConfig:
    """
    Outside temperature from Open-Meteo (free, no API key) for the given
    location. Used as outside_temp when there is no outside sensor.
    """
    enabled: bool = True
    location_name: str = "Nowy Sącz"
    latitude: float = 49.62
    longitude: float = 20.69
    refresh_min: float = 15.0     # how often to fetch (Open-Meteo updates every 15 min)
    max_age_min: float = 60.0     # older data is not used
    retry_min: float = 5.0        # retry after a failed fetch


def fetch_open_meteo(latitude: float, longitude: float, timeout_s: float = 10.0) -> float:
    query = urllib.parse.urlencode(
        {"latitude": latitude, "longitude": longitude, "current": "temperature_2m"}
    )
    with urllib.request.urlopen(f"{OPEN_METEO_URL}?{query}", timeout=timeout_s) as resp:
        data = json.load(resp)
    return float(data["current"]["temperature_2m"])


class WeatherModule(ModuleInterface):
    """
    Aux module: fetches the outside temperature in a background thread (the
    network never blocks the loop) and publishes it as runtime["weather"];
    the kernel puts it into sensors.outside_temp when no sensor provides one.
    """

    def __init__(
        self,
        base_path: Path | None = None,
        fetcher: Callable[[float, float], float] | None = None,
    ) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = WeatherConfig()
        self._load_config_from_file()

        self._fetcher = fetcher or fetch_open_meteo
        self._lock = threading.Lock()
        self._fetching = False
        self._next_attempt = 0.0          # wall clock
        self._temp: Optional[float] = None
        self._fetched_at: Optional[float] = None
        self._error: Optional[str] = None
        self._pending_events: List[Event] = []

    @property
    def id(self) -> str:
        return "weather"

    # ---------- tick ----------

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        wall = time.time()
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        if not self._config.enabled:
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=[], status=status, runtime={})

        with self._lock:
            start = not self._fetching and wall >= self._next_attempt
            if start:
                self._fetching = True
            events, self._pending_events = self._pending_events, []
            runtime = {
                "temp": self._temp,
                "fetched_at": self._fetched_at,
                "valid_until": (self._fetched_at or 0.0) + self._config.max_age_min * 60.0,
                "location": self._config.location_name,
                "error": self._error,
            }

        if start:
            cfg = self._config
            threading.Thread(
                target=self._fetch, args=(cfg.latitude, cfg.longitude), daemon=True, name="weather_fetch"
            ).start()

        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    def _fetch(self, latitude: float, longitude: float) -> None:
        try:
            temp = self._fetcher(latitude, longitude)
            error = None
        except Exception as exc:  # pylint: disable=broad-except
            temp = None
            error = f"{type(exc).__name__}: {exc}"

        wall = time.time()
        with self._lock:
            self._fetching = False
            was_ok = self._error is None and self._fetched_at is not None
            if error is None:
                if not was_ok:
                    self._pending_events.append(self._event(
                        EventLevel.INFO, "WEATHER_OK",
                        f"Pogoda: {temp:.1f}°C ({self._config.location_name})", {"temp": temp},
                    ))
                self._temp = temp
                self._fetched_at = wall
                self._error = None
                self._next_attempt = wall + self._config.refresh_min * 60.0
            else:
                if self._error is None:
                    self._pending_events.append(self._event(
                        EventLevel.WARNING, "WEATHER_FETCH_ERROR",
                        f"Nie udało się pobrać pogody: {error}", {"error": error},
                    ))
                self._error = error
                self._next_attempt = wall + self._config.retry_min * 60.0

    def _event(self, level: EventLevel, type_: str, message: str, data: Dict[str, Any]) -> Event:
        return Event(ts=time.time(), source=self.id, level=level, type=type_, message=message, data=data)

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
            self._next_attempt = 0.0  # e.g. new location - fetch right away

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            self._apply(yaml.safe_load(f) or {})

    def _apply(self, values: Dict[str, Any]) -> None:
        for f in fields(WeatherConfig):
            if f.name in values:
                cast = {bool: bool, float: float, str: str}.get(type(getattr(self._config, f.name)), lambda v: v)
                setattr(self._config, f.name, cast(values[f.name]))
