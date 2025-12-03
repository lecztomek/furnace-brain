# backend/api/http_api.py
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from ..core.kernel import Kernel
from ..core.state import SystemState


def create_api_router(kernel: Kernel) -> APIRouter:
    """
    Tworzy router HTTP z endpointami dla GUI.
    Kernel jest przekazywany jako zależność (closure) – nie używamy globali.
    """
    router = APIRouter()

    @router.get("/state")
    def get_state():
        """
        Główny endpoint dla GUI – aktualny stan kotła, temperatury, wyjścia, statusy modułów.
        """
        s: SystemState = kernel.state
        sens = s.sensors
        out = s.outputs

        return {
            "ts": s.ts,
            "mode": s.mode,
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

    @router.get("/state/raw")
    def get_state_raw():
        """
        Surowy stan – bardziej do debugowania niż do GUI.
        """
        return asdict(kernel.state)

    # Tu później dodasz:
    # - /config/schema
    # - /config/values
    # - /history
    # - /events
    # - /emergency
    # itd.

    return router
