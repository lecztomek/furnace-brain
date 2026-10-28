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
from backend.core.problems import ProblemTracker, describe_error
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


SUMMARY_S = 15 * 60   # how often the log gets "what was sent"


def js(value: Any) -> str:
    """
    Every value is stored as a JSON string: Firebase drops empty lists/dicts,
    turns lists into objects and forbids some characters in keys - the GUI
    must get exactly what the local API returns.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Described(Exception):
    """An error whose message already says what failed and why."""


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
        self._problems = ProblemTracker(self.id, "Podgląd zdalny")
        self._last_ok: Optional[float] = None
        self._new_summary(time.time())

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
            events = self._problems.take_events()
            runtime = {"configured": self._configured, "enabled": self._config.enabled,
                       "last_ok": self._last_ok, "problems": self._problems.failing()}
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=events, status=status, runtime=runtime)

    # ---------- worker thread ----------

    def _run(self) -> None:
        if len(self._key) < 20:
            # Firebase rules (viewer/firebase-rules.json) don't allow reading short keys
            self._failed("konfiguracja", "FURNACE_BRAIN_VIEWER_KEY w /etc/furnace-brain.env musi mieć co najmniej 20 znaków")
            return
        cursors: Optional[Dict[str, Any]] = None
        next_state = next_history = next_slow = 0.0
        while True:
            now = time.time()
            if not self._config.enabled or now < next_state:
                time.sleep(1 if self._config.enabled else 5)
                continue
            # the state is the essential part - without it nothing is sent
            try:
                if cursors is None:
                    cursors = self._fb_get("cursors") or {}
                patch: Dict[str, Any] = {
                    "state": js(self._get("/api/state/current")),
                    "manual": js(self._get("/api/manual/current")),
                    "updated_at": now,
                }
                self._succeeded("stan pieca")
            except Exception as exc:  # pylint: disable=broad-except
                self._failed("stan pieca", self._describe(exc))
                cursors, next_state = None, now + 60
                continue

            # the other parts: a failing one is skipped and logged, the rest still goes
            if now >= next_history:
                new_cursors = dict(cursors)
                part = self._part("historia i logi", lambda: self._increments(new_cursors))
                if part is not None:
                    patch.update(part)
                    cursors = new_cursors
                next_history = now + self._config.history_every_s
            if now >= next_slow:
                for name, fn in (("statystyki", self._slow_report),
                                 ("serwis i prognoza", self._slow_service),
                                 ("ustawienia", self._slow_config),
                                 ("historia 5 min", self._slow_history5m),
                                 ("usuwanie starych danych", self._cleanup)):
                    part = self._part(name, fn)
                    if part:
                        patch.update(part)
                next_slow = now + self._config.slow_every_min * 60

            patch["cursors"] = cursors
            self._sum["attempts"] += 1
            try:
                self._fb_patch(patch)
                self._succeeded("wysyłanie do Firebase")
                self._ok()
                self._count_sent(patch)
                next_state = now + self._config.state_every_s
            except Exception as exc:  # pylint: disable=broad-except
                self._failed("wysyłanie do Firebase", self._describe(exc))
                cursors = None                 # re-read what Firebase really has
                next_state = now + 60          # back off
                next_history = next_slow = 0.0
            self._maybe_summary(time.time())

    # ---------- "what was sent" summary in the log ----------

    def _new_summary(self, now: float) -> None:
        self._sum: Dict[str, Any] = {"since": now, "attempts": 0, "ok": 0, "last_ok": None, "history": 0,
                                     "logs": 0, "bytes": 0, "parts": {}, "errors": {}}

    def _count_sent(self, patch: Dict[str, Any]) -> None:
        s = self._sum
        s["ok"] += 1
        s["last_ok"] = time.time()
        s["history"] += sum(1 for k in patch if k.startswith("history/"))
        s["logs"] += sum(1 for k in patch if k.startswith("logs/"))
        s["bytes"] += len(json.dumps(patch))
        for key, name in (("report", "statystyki"), ("service", "serwis"), ("config/modules", "ustawienia"),
                          ("history5m", "historia 5 min")):
            if any(k == key or k.startswith(key + "/") for k in patch):
                s["parts"][name] = s["parts"].get(name, 0) + 1

    def _maybe_summary(self, now: float) -> None:
        s = self._sum
        if now - s["since"] < SUMMARY_S:
            return
        mins = round((now - s["since"]) / 60)
        if s["attempts"] == 0 and not s["errors"]:
            msg = f"Podgląd zdalny ({mins} min): nic nie wysłano (wyłączony albo czeka na dane)"
        else:
            last = time.strftime("%H:%M:%S", time.localtime(s["last_ok"])) if s["last_ok"] else "brak"
            bits = [f"wysłano {s['ok']} z {s['attempts']} razy (ostatnio {last})",
                    f"historia +{s['history']} wierszy", f"logi +{s['logs']} wpisów"]
            bits += [f"{name} {n}×" for name, n in s["parts"].items()]
            bits.append(f"{s['bytes'] / 1024:.0f} kB")
            msg = f"Podgląd zdalny ({mins} min): " + ", ".join(bits)
            if s["errors"]:
                msg += "; błędy: " + ", ".join(f"{k} {n}×" for k, n in s["errors"].items())
        level = EventLevel.WARNING if s["errors"] or (s["attempts"] and not s["ok"]) else EventLevel.INFO
        data = {k: v for k, v in s.items() if k != "since"}
        with self._lock:
            self._problems.events.append(Event(ts=now, source=self.id, level=level,
                                               type="REMOTE_VIEW_SUMMARY", message=msg, data=data))
        self._new_summary(now)

    def _part(self, name: str, fn) -> Optional[Dict[str, Any]]:
        try:
            out = fn()
        except Exception as exc:  # pylint: disable=broad-except
            self._failed(name, self._describe(exc))
            return None
        self._succeeded(name)
        return out or {}

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
        # several entries often share one timestamp (a mode change logs from every
        # module in the same tick) - a sequence number keeps them all; text order
        # of the keys stays the time order
        seq: Dict[str, int] = {}
        for i in logs:
            ms = str(int(i["ts"] * 1000))
            n = seq[ms] = seq.get(ms, -1) + 1
            out[f"logs/{datetime.fromtimestamp(i['ts']).date().isoformat()}/{ms}_{n:02d}"] = js(i)
        if logs:
            cursors["logs"] = logs[-1]["ts"]
        return out

    def _slow_report(self) -> Dict[str, Any]:
        # The API hands out its last finished report as "stale" and starts a new build;
        # this push comes every few minutes, so it always gets the stale one. Its numbers
        # are from the build a few minutes ago (generated_at says when) - the remote view
        # must not show "still counting" forever.
        report = self._get("/api/analytics/report")
        if isinstance(report, dict):
            report = {k: v for k, v in report.items() if k not in ("stale", "progress")}
        return {"report": js(report)}

    def _slow_service(self) -> Dict[str, Any]:
        """Service page (tuning, Pi diagnostics) and the weather forecast - small, every slow cycle."""
        return {
            "service/tuning": js(self._get("/api/service/tuning")),
            "service/diagnostics": js(self._get("/api/service/diagnostics")),
            "service/forecast": js(self._get("/api/analytics/forecast")),
            "service/changelog": js(self._get("/api/service/changelog")),
        }

    def _slow_config(self) -> Dict[str, Any]:
        modules = self._get("/api/config/modules")
        out: Dict[str, Any] = {"config/modules": js(modules), "logs_meta": js(self._get("/api/logs/meta"))}
        for m in modules:
            mid = urllib.parse.quote(m["id"])
            out[f"config/schemas/{m['id']}"] = js(self._get(f"/api/config/schema/{mid}"))
            out[f"config/values/{m['id']}"] = js(self._get(f"/api/config/values/{mid}"))
        return out

    def _slow_history5m(self) -> Dict[str, Any]:
        # 5 min averages per day for long ranges (7 days = ~2000 points instead of ~20000)
        out: Dict[str, Any] = {}
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

    def _cleanup(self) -> Dict[str, Any]:
        oldest = (date.today() - timedelta(days=int(self._config.keep_days))).isoformat()
        for node in ("history", "history5m", "logs"):
            for day in (self._fb_get(node, shallow=True) or {}):
                if day < oldest:
                    self._fb_request("DELETE", f"{node}/{day}")
        return {}

    # ---------- HTTP ----------

    def _get(self, path: str) -> Any:
        try:
            with urllib.request.urlopen(self._local + path, timeout=60) as resp:
                return json.load(resp)
        except Exception as exc:  # pylint: disable=broad-except
            raise Described(describe_error(exc, f"lokalne API {path.split('?')[0]}")) from exc

    def _fb_url(self, path: str, **params: str) -> str:
        query = urllib.parse.urlencode({"auth": self._secret, **params})
        return f"{self._db}/furnace/{self._key}/{path}.json?{query}"

    def _fb_request(self, method: str, path: str, body: Any = None, **params: str) -> Any:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(self._fb_url(path, **params), data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except Exception as exc:  # pylint: disable=broad-except
            # never the URL itself - it carries the secret
            raise Described(describe_error(exc, f"Firebase {method} /{path}")) from exc

    def _fb_get(self, path: str, shallow: bool = False) -> Any:
        return self._fb_request("GET", path, **({"shallow": "true"} if shallow else {}))

    def _fb_patch(self, patch: Dict[str, Any]) -> None:
        # one multi-path update per cycle; root of this furnace's data
        req = urllib.request.Request(
            f"{self._db}/furnace/{self._key}.json?" + urllib.parse.urlencode({"auth": self._secret}),
            data=json.dumps(patch, separators=(",", ":")).encode("utf-8"),
            method="PATCH", headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except Exception as exc:  # pylint: disable=broad-except
            raise Described(describe_error(exc, "Firebase PATCH (zapis danych)")) from exc

    # ---------- status events ----------

    @staticmethod
    def _describe(exc: BaseException) -> str:
        return str(exc) if isinstance(exc, Described) else describe_error(exc)

    def _ok(self) -> None:
        with self._lock:
            if self._last_ok is None:
                self._problems.events.append(Event(
                    ts=time.time(), source=self.id, level=EventLevel.INFO, type="REMOTE_VIEW_OK",
                    message=f"Podgląd zdalny: wysyłam dane, link: {PAGES_URL}#k=<FURNACE_BRAIN_VIEWER_KEY>", data={}))
            self._last_ok = time.time()

    def _failed(self, part: str, message: str) -> None:
        errors = self._sum["errors"]
        errors[part] = errors.get(part, 0) + 1
        with self._lock:
            self._problems.failed(part, message, "REMOTE_VIEW_ERROR")

    def _succeeded(self, part: str) -> None:
        with self._lock:
            self._problems.ok(part, "REMOTE_VIEW_OK")

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
