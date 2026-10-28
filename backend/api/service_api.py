"""
Service page: tuning advice (PID, feeder, blower) and the Raspberry Pi
diagnostics (temperature, power supply, disk, memory, control loop timing,
internet, data recording, sensors and modules).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter

from backend.core.config_store import ConfigStore
from backend.core.state_store import StateStore
from backend.core import timings
from backend.core.tuning import TuningReport

TUNING_MODULES = ("power_work", "feeder", "blower", "mixer", "mode", "pump_co", "overheat", "flameout")
TUNING_REUSE_S = 600   # rebuilt at most every 10 min (only today is read again - fast)

SENSOR_NAMES = {"boiler_temp": "Kocioł", "radiators_temp": "Grzejniki", "hopper_temp": "Podajnik",
                "flue_gas_temp": "Spaliny", "outside_temp": "Zewnętrzna"}

# vcgencmd get_throttled bits
THROTTLE_BITS = {
    0: "za niskie napięcie zasilania TERAZ",
    1: "ograniczona częstotliwość procesora TERAZ",
    2: "procesor zwolniony TERAZ",
    3: "limit temperatury TERAZ",
    16: "było za niskie napięcie zasilania",
    17: "było ograniczenie częstotliwości",
    18: "procesor był zwalniany",
    19: "był osiągnięty limit temperatury",
}


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


_run_cache: Dict[str, tuple] = {}
RUN_REUSE_S = 300.0


def _run(cmd: List[str]) -> Optional[str]:
    """A system command, its answer reused for 5 min."""
    key = " ".join(cmd)
    hit = _run_cache.get(key)
    if hit and time.monotonic() - hit[0] < RUN_REUSE_S:
        return hit[1]
    try:
        out: Optional[str] = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = None
    _run_cache[key] = (time.monotonic(), out)
    return out


def _item(status: str, label: str, value: str, note: str = "", gauge: Optional[float] = None) -> Dict[str, Any]:
    """gauge: how full (0-100) - drawn as a ring on the diagnostics tiles."""
    out: Dict[str, Any] = {"status": status, "label": label, "value": value, "note": note}
    if gauge is not None:
        out["gauge"] = round(max(0.0, min(100.0, gauge)))
    return out


def _ago(ts: Optional[float], now: float) -> str:
    if not ts:
        return "nigdy"
    s = max(0, int(now - ts))
    if s < 90:
        return f"{s} s temu"
    if s < 5400:
        return f"{s // 60} min temu"
    if s < 2 * 86400:
        return f"{s // 3600} h temu"
    return f"{s // 86400} dni temu"


def _duration(seconds: float) -> str:
    s = max(0, int(seconds))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d:
        return f"{d} d {h} h"
    return f"{h} h {m} min" if h else f"{m} min"


def _secs(s: float) -> str:
    if s < 1:
        return f"{s * 1000:.0f} ms"
    if s < 90:
        return f"{s:.1f} s".replace(".", ",")
    return _duration(s)


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f}".replace(".", ",") + " GB"


def _newest_mtime(directory: Path) -> Optional[float]:
    """Newest file of a data dir; names carry the date, so only the last few are checked (thousands of files)."""
    try:
        names = sorted(n for n in os.listdir(directory) if n.endswith(".csv"))[-3:]
        return max((os.stat(directory / n).st_mtime for n in names), default=None)
    except OSError:
        return None


def _tiles(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Top of the diagnostics tab: bars for what fills up (CPU temperature, RAM,
    SD card, USB stick) and small status tiles for the rest.
    """
    items = {i["label"]: i for g in groups for i in g["items"]}
    tiles: List[Dict[str, Any]] = []

    def status(label: str, icon: str, item: Optional[Dict[str, Any]], value: str) -> None:
        if item is not None:
            tiles.append({"kind": "status", "label": label, "icon": icon, "value": value, "status": item["status"]})

    # what fills up - one bar each: CPU temperature, RAM, the SD card, the USB stick with the data
    def bar(label: str, icon: str, item: Optional[Dict[str, Any]], big: str, small: str) -> None:
        if item is not None and "gauge" in item:
            tiles.append({"kind": "bar", "label": label, "icon": icon, "big": big, "small": small,
                          "pct": item["gauge"], "status": item["status"]})

    cpu = items.get("Temperatura procesora")
    bar("Procesor", "cpu", cpu, cpu["value"] if cpu else "", "temperatura, limit 85°C")
    ram = items.get("Pamięć RAM")
    if ram and " z " in ram["value"]:
        free_mb, total_mb = ram["value"].replace("wolne ", "").replace(" MB", "").split(" z ")
        bar("Pamięć RAM", "database", ram, f"{free_mb} MB", f"wolne z {total_mb} MB")
    for i in (x for g in groups for x in g["items"] if x.get("disk")):
        bar("Karta SD (system)" if "SD" in i["label"] else "Pendrive (dane)", "drive", i, i["free"], f"wolne z {i['total']}")

    power = items.get("Zasilanie")
    status("Zasilanie", "plug", power,
           {"ok": "OK", "warn": "były spadki", "bad": "za słabe"}.get(power["status"], "") if power else "")
    net = items.get("Podgląd zdalny (Firebase)") or items.get("Pogoda (internet)")
    status("Internet", "globe", net, {"ok": "OK", "info": "wyłączony"}.get(net["status"], "problem") if net else "")
    sensors = next((g["items"] for g in groups if g["title"] == "Czujniki"), [])
    if sensors:
        good = sum(1 for i in sensors if i["status"] == "ok")
        worst = "bad" if any(i["status"] == "bad" for i in sensors) else "warn" if good < len(sensors) else "ok"
        tiles.append({"kind": "status", "label": "Czujniki", "icon": "sensor", "value": f"{good} z {len(sensors)}",
                      "status": worst, "dots": [i["status"] for i in sensors]})
    up = items.get("Sterownik działa od")
    status("Działa od", "clock", up, up["value"] if up else "")
    return tiles


class LoopStats:
    """Duration of the control / aux loop steps over the last 10 min (filled by main.py)."""

    WINDOW_S = 600

    def __init__(self, interval_s: float) -> None:
        self.interval_s = interval_s
        self._lock = threading.Lock()
        self._samples: List[tuple] = []  # (wall ts, duration s)

    def add(self, duration_s: float) -> None:
        now = time.time()
        with self._lock:
            self._samples.append((now, duration_s))
            while self._samples and now - self._samples[0][0] > self.WINDOW_S:
                self._samples.pop(0)

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            d = [x for _, x in self._samples]
        if not d:
            return {"count": 0}
        return {"count": len(d), "avg_ms": 1000 * sum(d) / len(d), "max_ms": 1000 * max(d),
                "late": sum(1 for x in d if x > self.interval_s)}


def create_service_router(store: StateStore, config_store: ConfigStore, data_root: Path,
                          app_info: Dict[str, Any], loop_stats: Dict[str, LoopStats],
                          clock: Callable[[], float] = time.time,
                          warm_up_s: Optional[float] = None) -> APIRouter:
    router = APIRouter(prefix="/service", tags=["service"])
    modules_dir = data_root / "modules"
    tuning = TuningReport(history_dir=modules_dir / "history" / "data",
                          events_dir=modules_dir / "eventlog" / "data")
    # the advice is built in a background thread and a request never waits
    # for it (the GUI would freeze); nothing is read until the page is opened,
    # the first build reads 8 days of files with pauses (the control loop
    # keeps its CPU), later ones only today - at most every 10 min
    lock = threading.Lock()
    cached: Dict[str, Any] = {"at": 0.0, "report": None}
    building = threading.Event()

    def start_build(pause_s: float = 0.0) -> None:
        if building.is_set():
            return
        building.set()

        def run():
            try:
                with timings.measure("tuning"):
                    report = tuning.build(settings(), pause_s=pause_s)
                with lock:
                    cached["report"], cached["at"] = report, clock()
            except Exception:  # pylint: disable=broad-except
                pass
            finally:
                building.clear()

        threading.Thread(target=run, daemon=True, name="tuning_build").start()

    def warm_up():
        # past days read in the background a while after start, so the first
        # visit of the service page doesn't wait for them
        time.sleep(warm_up_s)
        start_build(pause_s=0.5)

    if warm_up_s is not None:
        threading.Thread(target=warm_up, daemon=True, name="tuning_warm_up").start()

    def settings() -> Dict[str, Dict[str, Any]]:
        out = {}
        for mid in TUNING_MODULES:
            try:
                out[mid] = config_store.get_values(mid)
            except (KeyError, ValueError, OSError):
                out[mid] = {}
        return out

    def live(s: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """The PID chain right now: deviation -> P + I -> power -> feeder rhythm and blower."""
        st = store.snapshot()
        pid = st.runtime.get("power_work") or {}
        power = st.outputs.power_percent
        fd, bl = s.get("feeder") or {}, s.get("blower") or {}
        feed = None
        try:
            if power is not None and power > float(fd.get("min_power_to_feed", 0)):
                off = float(fd["feed_off_base_s"]) * 100.0 / max(float(power), 0.1)
                off = max(float(fd.get("min_pause_s", 0)), min(float(fd.get("max_pause_s", 1e9)), off))
                feed = {"on_s": float(fd["feed_on_base_s"]), "off_s": off}
        except (KeyError, TypeError, ValueError):
            feed = None
        fan_on = None
        try:
            if power is not None and power > float(bl.get("min_power_to_blow", 0)):
                fan_on = min(100.0, float(power))
        except (TypeError, ValueError):
            pass
        return {
            "mode": st.mode.name,
            "boiler": st.sensors.boiler_temp,
            "setpoint": (s.get("power_work") or {}).get("boiler_set_temp"),
            "pid": pid if st.mode.name == "WORK" else {},
            "power": power,
            "feeder": feed,
            "fan_percent": bl.get("base_fan_percent"),
            "fan_duty_pct": fan_on,
            "fan_cycle_s": bl.get("cycle_time_s"),
            "fan_now": st.outputs.fan_power,
        }

    @router.get("/tuning")
    def get_tuning(fresh: bool = False):
        s = settings()
        with lock:
            report, age = cached["report"], clock() - cached["at"]
        if fresh or report is None or age > TUNING_REUSE_S:
            start_build(pause_s=0.2 if report is None else 0.0)
        if report is None:
            return {"building": True, "progress": dict(tuning.progress), "settings": s, "live": live(s)}
        return {**report, "building": building.is_set(), "settings": s, "live": live(s)}

    @router.get("/diagnostics")
    def get_diagnostics():
        now = clock()
        st = store.snapshot()
        groups: List[Dict[str, Any]] = []

        # --- Raspberry Pi ---
        pi: List[Dict[str, str]] = []
        raw = _read("/sys/class/thermal/thermal_zone0/temp")
        if raw:
            t = int(raw.strip()) / 1000.0
            pi.append(_item("ok" if t < 65 else "warn" if t < 80 else "bad", "Temperatura procesora",
                            f"{t:.0f}°C", "" if t < 65 else "Pi się grzeje - sprawdź wentylację obudowy",
                            gauge=100 * t / 85))
        thr = _run(["vcgencmd", "get_throttled"])
        if thr and "=" in thr:
            try:
                bits = int(thr.split("=")[1], 16)
                now_bits = [THROTTLE_BITS[b] for b in (0, 1, 2, 3) if bits & (1 << b)]
                past = [THROTTLE_BITS[b] for b in (16, 17, 18, 19) if bits & (1 << b)]
                if now_bits:
                    pi.append(_item("bad", "Zasilanie", ", ".join(now_bits), "Słaby zasilacz lub kabel USB - wymień"))
                elif past:
                    pi.append(_item("warn", "Zasilanie", ", ".join(past) + " (od startu Pi)",
                                    "Spadki napięcia psują kartę SD - rozważ lepszy zasilacz"))
                else:
                    pi.append(_item("ok", "Zasilanie", "bez spadków napięcia od startu Pi"))
            except (ValueError, IndexError):
                pass
        try:
            l1, l5, _ = os.getloadavg()
            cores = os.cpu_count() or 1
            pi.append(_item("ok" if l5 < cores else "warn", "Obciążenie procesora",
                            f"{l1:.2f} / {l5:.2f} (1 / 5 min, rdzeni: {cores})".replace(".", ",")))
        except OSError:
            pass
        mem = _read("/proc/meminfo")
        if mem:
            info = {ln.split(":")[0]: int(ln.split()[1]) for ln in mem.splitlines() if ln.split()[1:2]}
            total, avail = info.get("MemTotal", 0) * 1024, info.get("MemAvailable", 0) * 1024
            if total:
                pi.append(_item("ok" if avail > 100e6 else "warn" if avail > 40e6 else "bad", "Pamięć RAM",
                                f"wolne {avail / 1e6:.0f} z {total / 1e6:.0f} MB", gauge=100 * (1 - avail / total)))
        status = _read("/proc/self/status")
        if status:
            rss = next((ln.split()[1] for ln in status.splitlines() if ln.startswith("VmRSS")), None)
            if rss:
                pi.append(_item("ok", "Pamięć sterownika", f"{int(rss) / 1024:.0f} MB"))
        up = _read("/proc/uptime")
        if up:
            secs = float(up.split()[0])
            pi.append(_item("ok", "Pi działa od", _duration(secs)))
        ntp = _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
        if ntp in ("yes", "no"):
            pi.append(_item("ok" if ntp == "yes" else "warn", "Zegar",
                            "zsynchronizowany z internetem" if ntp == "yes" else "niezsynchronizowany",
                            "" if ntp == "yes" else "Godziny w statystykach mogą być przesunięte"))
        groups.append({"title": "Raspberry Pi", "items": pi})

        # --- disks ---
        disks: List[Dict[str, str]] = []
        seen = set()
        for label, path in (("Karta SD (system)", "/"), ("Dane (pendrive)", str(data_root))):
            try:
                u = shutil.disk_usage(path)
                dev = os.stat(path).st_dev
            except OSError:
                continue
            if dev in seen:
                continue
            seen.add(dev)
            free_pct = 100 * u.free / u.total if u.total else 0
            disk = _item("ok" if free_pct > 20 else "warn" if free_pct > 5 else "bad", label,
                         f"wolne {_gb(u.free)} z {_gb(u.total)} ({free_pct:.0f}%)", gauge=100 - free_pct)
            disk.update(disk=True, free=_gb(u.free), total=_gb(u.total))
            disks.append(disk)
        for label, sub in (("Zapis historii", "history"), ("Zapis logów", "eventlog"), ("Zapis statystyk", "stats")):
            m = _newest_mtime(modules_dir / sub / "data")
            # history every 30 s; logs only when something happens (compacted); statistics every 5 min
            limit = {"history": 180, "eventlog": 1800, "stats": 900}[sub]
            disks.append(_item("ok" if m and now - m < limit else "warn", label, f"ostatnio {_ago(m, now)}",
                               "" if m and now - m < limit else "Nic się nie zapisuje - sprawdź pendrive"))
        groups.append({"title": "Dyski i zapis danych", "items": disks})

        # --- controller ---
        ctl: List[Dict[str, str]] = []
        started = app_info.get("started_at")
        ctl.append(_item("ok", "Wersja", str(app_info.get("version") or "--")))
        ctl.append(_item("ok", "Sterownik działa od", _duration(now - started) if started else "--"))
        for key, label in (("control", "Pętla sterowania"), ("aux", "Pętla pomocnicza")):
            ls = loop_stats.get(key)
            if ls is None:
                continue
            sm = ls.summary()
            if not sm.get("count"):
                continue
            late = sm["late"]
            ctl.append(_item("ok" if late == 0 else "warn" if late < 20 else "bad", label,
                             f"średnio {sm['avg_ms']:.0f} ms, najdłużej {sm['max_ms']:.0f} ms"
                             + (f", spóźniona {late}× (10 min)" if late else ""),
                             "" if late == 0 else f"krok dłuższy niż {ls.interval_s:g} s - Pi nie wyrabia"))
        for mid, ms in sorted(st.modules.items()):
            if ms.health.name != "OK":
                ctl.append(_item("bad", f"Moduł {mid}", ms.health.name, ms.last_error or ""))
        if not any(i["label"].startswith("Moduł ") for i in ctl):
            ctl.append(_item("ok", "Moduły", f"{len(st.modules)} pracuje bez błędów"))
        groups.append({"title": "Sterownik", "items": ctl})

        # --- background analyses: when they ran and how long they took ---
        ana: List[Dict[str, str]] = []
        done = timings.snapshot()
        slow_s = {"analytics": 120.0, "tuning": 30.0, "weather": 10.0, "forecast": 15.0}
        for key, (label, how_often) in timings.KNOWN.items():
            e = done.get(key)
            if not e:
                ana.append(_item("info", label, "jeszcze nie liczone od startu", how_often))
                continue
            if e.get("running_since"):
                ana.append(_item("info", label, f"liczy teraz (od {_duration(now - e['running_since'])})"
                                 + (f", poprzednio {_secs(e['last_s'])}" if e.get("last_s") is not None else ""), how_often))
                continue
            took = e.get("last_s") or 0.0
            # the first statistics / tuning run reads every day from the files (with pauses) - slow by design
            slow = e["runs"] > 1 and took > slow_s.get(key, 30.0)
            status = "warn" if not e.get("ok") or slow else "ok"
            note = how_often + (" • ostatnio błąd - szczegóły w Logach" if not e.get("ok") else "")
            if slow:
                note += " • długo jak na ten Raspberry"
            ana.append(_item(status, label,
                             f"{_ago(e.get('last_at'), now)}, trwało {_secs(took)} (najdłużej {_secs(e['max_s'])}, "
                             f"razy: {e['runs']})", note))
        groups.append({"title": "Analizy w tle", "items": ana})

        # --- internet ---
        net: List[Dict[str, str]] = []
        w = st.runtime.get("weather") or {}
        if w:
            ok = w.get("fetched_at") and now - w["fetched_at"] < 3600
            net.append(_item("ok" if ok else "warn", "Pogoda (internet)",
                             f"pobrana {_ago(w.get('fetched_at'), now)}"
                             + (f", {w['temp']:.1f}°C".replace(".", ",") if w.get("temp") is not None else ""),
                             w.get("error") or ""))
        rv = st.runtime.get("remote_view") or {}
        if rv.get("enabled") and rv.get("configured"):
            ok = rv.get("last_ok") and now - rv["last_ok"] < 300
            problems = "; ".join(f"{k}: {v}" for k, v in (rv.get("problems") or {}).items())
            net.append(_item("ok" if ok and not problems else "warn", "Podgląd zdalny (Firebase)",
                             f"wysłane {_ago(rv.get('last_ok'), now)}", problems))
        elif rv:
            net.append(_item("info", "Podgląd zdalny (Firebase)", "wyłączony"))
        groups.append({"title": "Internet", "items": net})

        # --- sensors ---
        sens: List[Dict[str, str]] = []
        safety = st.runtime.get("safety") or {}
        missing = set(safety.get("missing") or [])
        last_missing = safety.get("last_missing") or {}
        episodes = safety.get("episodes") or {}
        for key, label in SENSOR_NAMES.items():
            v = getattr(st.sensors, key, None)
            n = episodes.get(key, 0)
            note = f"gubił odczyt {n}× od startu, ostatnio {_ago(last_missing.get(key), now)}" if n else ""
            if key in missing or (v is None and key != "outside_temp"):
                sens.append(_item("bad", label, "brak odczytu", note))
            elif v is None:
                sens.append(_item("warn", label, "brak (pogoda niepobrana)"))
            else:
                sens.append(_item("warn" if n else "ok", label, f"{v:.1f}°C".replace(".", ","), note))
        groups.append({"title": "Czujniki", "items": sens})

        worst = "ok"
        for g in groups:
            for i in g["items"]:
                if i["status"] == "bad":
                    worst = "bad"
                elif i["status"] == "warn" and worst == "ok":
                    worst = "warn"
        return {"generated_at": now, "status": worst, "groups": groups, "tiles": _tiles(groups)}

    return router
