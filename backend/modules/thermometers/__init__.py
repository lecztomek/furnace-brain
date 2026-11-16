"""
Termometry WiFi: up to three thermometers (Tasmota with a DS18B20) read over the home network
in a background thread. Each has an address (its last number - the panel has no keyboard)
and a role; for now the only role is "w_domu" (the temperature in the house). Published as
runtime["thermometers"]; the history writes each role into its own column (w_domu -> temp_wewn).
Only recorded - nothing in the furnace control reads it.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.problems import ProblemTracker, describe_error
from backend.core.state import Event, EventLevel, ModuleStatus, PartialOutputs, Sensors, SystemState

SLOTS = 3
ROLES = {"w_domu": "w domu"}       # role -> how the logs name it ("brak" = not used)
TIMEOUT_S = 5.0
VALID_MIN_C, VALID_MAX_C = -40.0, 60.0     # a reading outside this is a broken sensor


@dataclass
class ThermometersConfig:
    enabled: bool = True
    # the last number of each thermometer's address; the first three are the controller's own
    # (192.168.1.x). 0 = no thermometer in this slot
    t1_address: float = 245.0
    t1_role: str = "w_domu"
    t2_address: float = 0.0
    t2_role: str = "brak"
    t3_address: float = 0.0
    t3_role: str = "brak"
    refresh_s: float = 60.0
    max_age_min: float = 10.0     # an older reading is not used


def reading_url(address: str) -> str:
    """The sensor status of a Tasmota device."""
    return f"http://{address.strip()}/cm?cmnd=Status%2010"


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


def fetch_temp(address: str, timeout_s: float = TIMEOUT_S) -> float:
    with urllib.request.urlopen(reading_url(address), timeout=timeout_s) as resp:
        return parse_temperature(json.load(resp))


def own_ip() -> str:
    """The controller's address in the home network (no packet is sent - only the route is looked up)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    finally:
        s.close()


def thermometer_address(last: int, ip: str) -> str:
    """The controller's first three numbers and the thermometer's last one: 192.168.1.17 + 50 -> 192.168.1.50."""
    parts = ip.split(".")
    if len(parts) != 4 or ip.startswith("127."):
        raise ValueError("sterownik nie ma adresu w sieci domowej")
    return ".".join(parts[:3] + [str(int(last))])


class ThermometersModule(ModuleInterface):
    def __init__(
        self,
        base_path: Optional[Path] = None,
        data_root: Optional[Path] = None,
        fetcher: Optional[Callable[[str], float]] = None,
        ip_finder: Optional[Callable[[], str]] = None,
    ) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"
        self._config = ThermometersConfig()
        self._load_config_from_file()

        self._fetcher = fetcher or fetch_temp
        self._own_ip = ip_finder or own_ip
        self._lock = threading.Lock()
        self._fetching = False
        self._next_attempt = 0.0          # wall clock
        # slot number -> {"address", "role", "temp", "fetched_at", "error"}
        self._readings: Dict[int, Dict[str, Any]] = {}
        self._pending_events: List[Event] = []
        self._problems = ProblemTracker(self.id, "Termometry")

    @property
    def id(self) -> str:
        return "thermometers"

    def _slots(self) -> List[Tuple[int, int, str]]:
        """(slot, last number of the address, role) of every thermometer that is set up."""
        cfg, out = self._config, []
        for n in range(1, SLOTS + 1):
            last, role = int(getattr(cfg, f"t{n}_address")), getattr(cfg, f"t{n}_role")
            if 1 <= last <= 254 and role in ROLES:
                out.append((n, last, role))
        return out

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        wall = time.time()
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        slots = self._slots() if self._config.enabled else []
        if not slots:
            # nothing set up - nothing to read, nothing to complain about
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=[], status=status, runtime={})

        max_age = self._config.max_age_min * 60.0
        with self._lock:
            start = not self._fetching and wall >= self._next_attempt
            if start:
                self._fetching = True
            events, self._pending_events = self._pending_events + self._problems.take_events(), []
            devices = []
            roles: Dict[str, Dict[str, Any]] = {}
            for n, _last, role in slots:
                r = self._readings.get(n)
                if not r or r["role"] != role:
                    continue
                dev = {"slot": n, "role": role, "address": r["address"], "temp": r["temp"],
                       "fetched_at": r["fetched_at"], "valid_until": (r["fetched_at"] or 0.0) + max_age,
                       "error": r["error"]}
                devices.append(dev)
                # two thermometers with one role: the first one with a fresh reading
                if role not in roles and dev["temp"] is not None and wall <= dev["valid_until"]:
                    roles[role] = dev
            runtime = {"devices": devices, "roles": roles}

        if start:
            threading.Thread(target=self._fetch, args=(slots,), daemon=True, name="thermometers_fetch").start()

        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    def _fetch(self, slots: List[Tuple[int, int, str]]) -> None:
        try:
            ip: Optional[str] = self._own_ip()
            ip_error = None
        except Exception as exc:  # pylint: disable=broad-except
            ip, ip_error = None, describe_error(exc, "adres sterownika")
        for n, last, role in slots:
            address = f"x.x.x.{last}"
            try:
                if ip is None:
                    raise ValueError(ip_error)
                address = thermometer_address(last, ip)
                temp = float(self._fetcher(address))
                if not VALID_MIN_C <= temp <= VALID_MAX_C:
                    raise ValueError(f"odczyt poza zakresem: {temp:.1f}°C")
                error = None
            except Exception as exc:  # pylint: disable=broad-except
                temp, error = None, describe_error(exc, f"termometr {address}")
            self._store(n, role, address, temp, error)
        with self._lock:
            self._next_attempt = time.time() + max(10.0, self._config.refresh_s)
            self._fetching = False

    def _store(self, n: int, role: str, address: str, temp: Optional[float], error: Optional[str]) -> None:
        wall = time.time()
        key = f"termometr {n} ({ROLES[role]})"
        with self._lock:
            old = self._readings.get(n) or {}
            same = old.get("address") == address and old.get("role") == role
            if error is None:
                if not same or old.get("fetched_at") is None:
                    self._pending_events.append(Event(
                        ts=wall, source=self.id, level=EventLevel.INFO, type="THERMOMETER_OK",
                        message=f"Termometr {n} ({ROLES[role]}, {address}): {temp:.1f}°C",
                        data={"slot": n, "role": role, "address": address, "temp": temp},
                    ))
                self._problems.ok(key, "THERMOMETER_OK")
                self._readings[n] = {"address": address, "role": role, "temp": round(temp, 2),
                                     "fetched_at": wall, "error": None}
            else:
                self._problems.failed(key, error, "THERMOMETER_ERROR")
                keep = same and old.get("fetched_at") is not None
                self._readings[n] = {"address": address, "role": role,
                                     "temp": old.get("temp") if keep else None,
                                     "fetched_at": old.get("fetched_at") if keep else None, "error": error}

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

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            self._apply(yaml.safe_load(f) or {})

    def _apply(self, values: Dict[str, Any]) -> None:
        for f in fields(ThermometersConfig):
            if f.name in values and values[f.name] is not None:
                cast = {bool: bool, float: float, str: str}[type(getattr(self._config, f.name))]
                setattr(self._config, f.name, cast(values[f.name]))
