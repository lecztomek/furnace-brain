from __future__ import annotations

import threading
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

    def setting(module_id: str, key: str, default):
        try:
            return config_store.get_value(module_id, key)
        except (KeyError, ValueError, OSError):
            return default

    @router.get("/report")
    def get_report():
        """Coal, averages, regulation quality, checks and the weather model."""
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
            )
        report["settings"] = {
            "feeder_kg_per_hour": setting("stats", "feeder_kg_per_hour", None),
            "calorific_mj_per_kg": setting("stats", "calorific_mj_per_kg", None),
            "boiler_set_temp": setting("power_work", "boiler_set_temp", None),
        }
        return report

    return router
