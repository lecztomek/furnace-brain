# backend/api/state_api.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.core.state_store import StateStore
from ..core.state import SystemState, BoilerMode


MODE_DISPLAY = {
    BoilerMode.IGNITION: "rozpalanie",
    BoilerMode.WORK: "praca",
    BoilerMode.OFF: "off",
    BoilerMode.MANUAL: "ręczne",
}


def create_state_router(store: StateStore) -> APIRouter:
    router = APIRouter(prefix="/state", tags=["state"])

    def _serialize_state(s: SystemState) -> dict:
        sens = s.sensors
        out = s.outputs

        return {
            "ts": s.ts,
            "mode": s.mode.name,
            "mode_display": MODE_DISPLAY.get(s.mode, s.mode.name.lower()),
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
