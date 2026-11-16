"""
Sieć: is the controller connected to the router (home network) and to the internet. Checked
in a background thread; a failure that lasts longer than a few minutes goes to the main
screen (yellow sign) - without the network there is no weather, remote view or updates,
the furnace itself keeps working. Drives nothing.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.problems import ProblemTracker
from backend.core.state import Event, ModuleStatus, PartialOutputs, Sensors, SystemState

DEFAULTS = {"enabled": True, "check_s": 30.0, "alarm_after_min": 3.0}
TIMEOUT_S = 3.0
INTERNET_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 443))
DNS_NAME = "api.open-meteo.com"


def default_gateway() -> Optional[str]:
    """The router's address from the routing table (Linux); None = no route out of the Pi."""
    try:
        with open("/proc/net/route", encoding="ascii") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000" and int(parts[3], 16) & 2:
                    g = bytes.fromhex(parts[2])[::-1]
                    return ".".join(str(b) for b in g)
    except (OSError, ValueError, IndexError):
        pass
    return None


def reachable(host: str, port: int, timeout_s: float = TIMEOUT_S) -> bool:
    """Something answers at this address - a refused connection is an answer too."""
    try:
        socket.create_connection((host, port), timeout=timeout_s).close()
        return True
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def check_network() -> Dict[str, Any]:
    """{"lan": the router answers, "internet": outside addresses answer, "dns": names resolve}."""
    gw = default_gateway()
    lan = gw is not None and any(reachable(gw, p) for p in (53, 80))
    internet = any(reachable(h, p) for h, p in INTERNET_HOSTS)
    try:
        socket.getaddrinfo(DNS_NAME, 443)
        dns = True
    except OSError:
        dns = False
    return {"lan": lan, "internet": internet, "dns": dns, "gateway": gw}


class NetworkModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, data_root: Optional[Path] = None,
                 checker: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._values: Dict[str, Any] = dict(DEFAULTS)
        self.reload_config_from_file()
        self._checker = checker or check_network
        self._lock = threading.Lock()
        self._checking = False
        self._next = 0.0
        self._result: Optional[Dict[str, Any]] = None
        self._down_since: Optional[float] = None     # first failed check of this outage
        self._down_what: Optional[str] = None
        self._problems = ProblemTracker(self.id, "Sieć")

    @property
    def id(self) -> str:
        return "network"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        if not self._values.get("enabled", True):
            return ModuleTickResult(partial_outputs=PartialOutputs(), events=[], status=status, runtime={})
        wall = time.time()
        with self._lock:
            start = not self._checking and wall >= self._next
            if start:
                self._checking = True
            events: List[Event] = self._problems.take_events()
            alarm_after = float(self._values.get("alarm_after_min", 3.0)) * 60.0
            down = self._down_since is not None and wall - self._down_since >= alarm_after
            runtime = {
                "lan": None if self._result is None else self._result["lan"],
                "internet": None if self._result is None else self._result["internet"] and self._result["dns"],
                # what the main screen shows: None, "lan" (no home network) or "internet"
                "down": self._down_what if down else None,
                "down_since": self._down_since if down else None,
            }
        if start:
            threading.Thread(target=self._check, daemon=True, name="network_check").start()
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    def _check(self) -> None:
        try:
            r = self._checker()
        except Exception:  # pylint: disable=broad-except
            r = None
        wall = time.time()
        with self._lock:
            if r is not None:
                self._result = r
                what = "lan" if not r["lan"] else "internet" if not (r["internet"] and r["dns"]) else None
                if what is None:
                    self._down_since, self._down_what = None, None
                    self._problems.ok("połączenie", "NETWORK_OK")
                else:
                    if self._down_since is None or what != self._down_what:
                        self._down_since = self._down_since or wall
                        self._down_what = what
                    msg = ("brak połączenia z routerem" if what == "lan"
                           else "router działa, ale nie ma internetu" if not r["internet"]
                           else "nie działa DNS (nazwy serwerów)")
                    self._problems.failed("połączenie", msg, "NETWORK_DOWN")
            self._next = wall + max(10.0, float(self._values.get("check_s", 30.0)))
            self._checking = False

    # ---------- config ----------

    def get_config_schema(self) -> Dict[str, Any]:
        with (self._base_path / "schema.yaml").open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return dict(self._values)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        self._values.update(values)
        if persist:
            with (self._base_path / "values.yaml").open("w", encoding="utf-8") as f:
                yaml.safe_dump(self._values, f, allow_unicode=True)

    def reload_config_from_file(self) -> None:
        path = self._base_path / "values.yaml"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                self._values = {**DEFAULTS, **(yaml.safe_load(f) or {})}
