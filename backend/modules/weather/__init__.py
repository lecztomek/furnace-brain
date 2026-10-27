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
from backend.core import timings
from backend.core.problems import ProblemTracker, describe_error
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


FORECAST_REFRESH_S = 3 * 3600.0   # the forecast changes slowly - every 3 h is plenty
FORECAST_DAYS = 3


def fetch_open_meteo_forecast(latitude: float, longitude: float, timeout_s: float = 15.0) -> Dict[str, Any]:
    """Hourly and daily forecast for today and the next days (local time)."""
    query = urllib.parse.urlencode({
        "latitude": latitude, "longitude": longitude,
        "hourly": "temperature_2m,weather_code,wind_speed_10m,precipitation_probability",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "forecast_days": FORECAST_DAYS, "timezone": "Europe/Warsaw",
    })
    with urllib.request.urlopen(f"{OPEN_METEO_URL}?{query}", timeout=timeout_s) as resp:
        data = json.load(resp)
    h, d = data["hourly"], data["daily"]

    def col(block: Dict[str, Any], key: str, i: int) -> Any:
        values = block.get(key) or []
        return values[i] if i < len(values) else None

    return {
        "hourly": [{"time": t, "temp": col(h, "temperature_2m", i), "code": col(h, "weather_code", i),
                    "wind": col(h, "wind_speed_10m", i), "rain_pct": col(h, "precipitation_probability", i)}
                   for i, t in enumerate(h["time"])],
        "daily": [{"date": t, "tmax": col(d, "temperature_2m_max", i), "tmin": col(d, "temperature_2m_min", i),
                   "code": col(d, "weather_code", i)} for i, t in enumerate(d["time"])],
    }


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
        forecast_fetcher: Callable[[float, float], Dict[str, Any]] | None = None,
    ) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = WeatherConfig()
        self._load_config_from_file()

        self._fetcher = fetcher or fetch_open_meteo
        # with a test fetcher and no forecast fetcher there is no forecast
        self._forecast_fetcher = forecast_fetcher or (None if fetcher else fetch_open_meteo_forecast)
        self._forecast: Optional[Dict[str, Any]] = None
        self._forecast_next = 0.0         # wall clock
        self._lock = threading.Lock()
        self._fetching = False
        self._next_attempt = 0.0          # wall clock
        self._temp: Optional[float] = None
        self._fetched_at: Optional[float] = None
        self._error: Optional[str] = None
        self._pending_events: List[Event] = []
        self._problems = ProblemTracker(self.id, "Pogoda")

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
            events, self._pending_events = self._pending_events + self._problems.take_events(), []
            runtime = {
                "temp": self._temp,
                "fetched_at": self._fetched_at,
                "valid_until": (self._fetched_at or 0.0) + self._config.max_age_min * 60.0,
                "location": self._config.location_name,
                "error": self._error,
                "forecast": self._forecast,
            }

        if start:
            cfg = self._config
            threading.Thread(
                target=self._fetch, args=(cfg.latitude, cfg.longitude), daemon=True, name="weather_fetch"
            ).start()

        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    def _fetch(self, latitude: float, longitude: float) -> None:
        try:
            with timings.measure("weather"):
                temp = self._fetcher(latitude, longitude)
            error = None
        except Exception as exc:  # pylint: disable=broad-except
            temp = None
            error = describe_error(exc, "Open-Meteo (api.open-meteo.com)")

        wall = time.time()
        with self._lock:
            first = self._fetched_at is None
            if error is None:
                if first:
                    self._pending_events.append(self._event(
                        EventLevel.INFO, "WEATHER_OK",
                        f"Pogoda: {temp:.1f}°C ({self._config.location_name})", {"temp": temp},
                    ))
                self._problems.ok("pobieranie temperatury", "WEATHER_OK")
                self._temp = temp
                self._fetched_at = wall
                self._error = None
                self._next_attempt = wall + self._config.refresh_min * 60.0
            else:
                # what failed and why; repeated every 30 min while it lasts
                self._problems.failed("pobieranie temperatury", error, "WEATHER_FETCH_ERROR")
                self._error = error
                self._next_attempt = wall + self._config.retry_min * 60.0
            forecast_due = self._forecast_fetcher is not None and wall >= self._forecast_next
        if forecast_due:
            self._fetch_forecast(latitude, longitude)
        with self._lock:
            self._fetching = False

    def _fetch_forecast(self, latitude: float, longitude: float) -> None:
        try:
            with timings.measure("forecast"):
                forecast = self._forecast_fetcher(latitude, longitude)
            error = None
        except Exception as exc:  # pylint: disable=broad-except
            forecast, error = None, describe_error(exc, "prognoza Open-Meteo")
        wall = time.time()
        with self._lock:
            if error is None:
                self._problems.ok("prognoza", "WEATHER_OK")
                self._forecast = {**forecast, "fetched_at": wall, "location": self._config.location_name}
                self._forecast_next = wall + FORECAST_REFRESH_S
            else:
                self._problems.failed("prognoza", error, "WEATHER_FETCH_ERROR")
                self._forecast_next = wall + self._config.retry_min * 60.0

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
            self._forecast_next = 0.0

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
