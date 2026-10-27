# backend/api/state_api.py
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from backend.core.config_store import ConfigStore
from backend.core.state_store import StateStore
from ..core.state import SystemState, BoilerMode


MODE_DISPLAY = {
    BoilerMode.IGNITION: "rozpalanie",
    BoilerMode.WORK: "praca",
    BoilerMode.OFF: "off",
    BoilerMode.MANUAL: "ręczne",
}


# WORK power regulators, shown next to the mode when enabled
POWER_REGULATORS = ("power_work",)


def create_state_router(
    store: StateStore,
    config_store: ConfigStore | None = None,
    app_info: dict | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/state", tags=["state"])

    regulators_cache = {"at": 0.0, "value": []}

    def _enabled_power_regulators() -> list[str]:
        # the GUI polls the state every second - settings are read at most every few seconds
        if config_store is None:
            return []
        if time.monotonic() - regulators_cache["at"] < 5.0:
            return list(regulators_cache["value"])
        loaded = set(config_store.module_ids)
        enabled = []
        for mid in POWER_REGULATORS:
            if mid not in loaded:
                continue
            try:
                if config_store.get_value(mid, "enabled"):
                    enabled.append(mid)
            except (KeyError, ValueError):
                enabled.append(mid)  # no "enabled" field = always on
        regulators_cache.update(at=time.monotonic(), value=enabled)
        return list(enabled)

    def _serialize_state(s: SystemState) -> dict:
        sens = s.sensors
        out = s.outputs

        return {
            "ts": s.ts,
            "mode": s.mode.name,
            "mode_display": MODE_DISPLAY.get(s.mode, s.mode.name.lower()),
            "power_regulators": _enabled_power_regulators(),
            "app": app_info or {},
            "alarm_active": s.alarm_active,
            "alarm_message": s.alarm_message,
            "sensors": {
                "boiler_temp": sens.boiler_temp,
                "return_temp": sens.return_temp,
                "radiators_temp": sens.radiators_temp,
                "cwu_temp": sens.cwu_temp,
                "flue_gas_temp": sens.flue_gas_temp,
                "hopper_temp": sens.hopper_temp,
                "outside_temp": sens.outside_temp,
                "mixer_temp": sens.mixer_temp,
            },
            "outputs": {
                "fan_power": out.fan_power,
                "feeder_on": out.feeder_on,
                "pump_co_on": out.pump_co_on,
                "pump_cwu_on": out.pump_cwu_on,
                "pump_circ_on": out.pump_circ_on,
                "mixer_open_on": out.mixer_open_on,
                "mixer_close_on": out.mixer_close_on,
                "alarm_buzzer_on": out.alarm_buzzer_on,
                "alarm_relay_on": out.alarm_relay_on,
                "power_percent": out.power_percent,
            },
            # heat output from the coal fed in the last 10 min (stats module), None = unknown
            "power_kw": (s.runtime.get("stats") or {}).get("power_kw_now"),
            # coal burned [kg/h], same 10 min window
            "coal_kgph": (s.runtime.get("stats") or {}).get("coal_kgph_now"),
            # radiators target in use: fixed, or from the heating curve (mixer module)
            "radiators": s.runtime.get("mixer") or {},
            "modules": {
                mid: {
                    "health": status.health.name,
                    "last_error": status.last_error,
                    "last_tick_duration": status.last_tick_duration,
                    "last_updated": status.last_updated,
                }
                for mid, status in s.modules.items()
            },
        }

    @router.get("/current")
    def get_current():
        s: SystemState = store.snapshot()
        return _serialize_state(s)

    @router.post("/mode/{mode_name}")
    def set_mode(mode_name: str):
        """
        Zmiana trybu pracy kotła.

        Oczekuje nazwy enuma:
        - OFF
        - IGNITION
        - WORK
        - MANUAL
        """
        try:
            enum_value = BoilerMode[mode_name.upper()]
        except KeyError:
            valid = [m.name for m in BoilerMode]
            raise HTTPException(
                status_code=422,
                detail={
                    "msg": f"Nieznany tryb '{mode_name}'. Dozwolone: {valid}",
                    "allowed": valid,
                },
            )

        # UWAGA: modyfikujemy ŹRÓDŁO PRAWDY (store), nie snapshot
        with store.locked() as s:
            s.mode = enum_value

        # zwracamy świeży snapshot po zmianie
        return _serialize_state(store.snapshot())

    return router
