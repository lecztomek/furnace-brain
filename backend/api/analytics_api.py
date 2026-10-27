from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

from fastapi import APIRouter

from backend.core.analytics import Analytics
from backend.core.config_store import ConfigStore


def create_analytics_router(data_root: Path, config_store: ConfigStore) -> APIRouter:
    router = APIRouter(prefix="/analytics", tags=["analytics"])
    modules = data_root / "modules"
    analytics = Analytics(
        history_dir=modules / "history" / "data",
        stats_dir=modules / "stats" / "data",
        events_dir=modules / "eventlog" / "data",
        cache_path=modules / "stats" / "data" / "analytics_days.json",
    )
    lock = threading.Lock()  # one report at a time (cache file)
    cached: dict = {"at": 0.0, "report": None}
    REUSE_S = 60  # a fresh report is reused for a minute
    # the last report survives restarts: shown at once (marked stale) while
    # a fresh one is built in the background
    report_path = modules / "stats" / "data" / "analytics_report.json"
    building = threading.Event()
    try:
        cached["report"] = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    def setting(module_id: str, key: str, default):
        try:
            return config_store.get_value(module_id, key)
        except (KeyError, ValueError, OSError):
            return default

    @router.get("/report")
    def get_report(fresh: bool = False):
        """Coal, averages, regulation quality, checks and the weather model."""
        with lock:
            report, age = cached["report"], time.time() - cached["at"]
        if not fresh and report is not None:
            if age < REUSE_S:
                return report
            # an older report right away, the new one is built in the background
            start_background_build()
            return {**report, "stale": True}
        return build_report()

    def start_background_build(pause_s: float = 0.0) -> None:
        if building.is_set():
            return
        building.set()

        def run():
            try:
                build_report(pause_s=pause_s)
            except Exception:  # pylint: disable=broad-except
                pass
            finally:
                building.clear()

        threading.Thread(target=run, daemon=True, name="analytics_build").start()

    def build_report(pause_s: float = 0.0):
        today = date.today()
        month = int(setting("stats", "season_start_month", 9))
        day = int(setting("stats", "season_start_day", 1))
        season = date(today.year if (today.month, today.day) >= (month, day) else today.year - 1, month, day)
        with lock:
            report = analytics.report(
                today=today,
                season_start=season,
                fallback_setpoint=setting("power_work", "boiler_set_temp", None),
                overheat_temp=float(setting("overheat", "boiler_trip_temp", 80.0)),
                pause_s=pause_s,
            )
        report["settings"] = {
            "feeder_kg_per_hour": setting("stats", "feeder_kg_per_hour", None),
            "calorific_mj_per_kg": setting("stats", "calorific_mj_per_kg", None),
            "boiler_set_temp": setting("power_work", "boiler_set_temp", None),
        }
        report["generated_at"] = time.time()
        with lock:
            cached["report"], cached["at"] = report, time.time()
        try:
            tmp = report_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(report, default=str), encoding="utf-8")
            tmp.replace(report_path)
        except OSError:
            pass
        return report

    def warm_up():
        # past days are read once in the background after start-up, so the
        # first visit of the statistics page doesn't wait for months of CSV;
        # pauses between days leave the CPU to the rest (Raspberry Pi)
        time.sleep(5)
        start_background_build(pause_s=0.5)

    threading.Thread(target=warm_up, daemon=True, name="analytics_warm_up").start()

    return router
