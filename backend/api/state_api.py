# backend/api/state_api.py
from __future__ import annotations

from dataclasses import asdict  # nadal opcjonalne

from fastapi import APIRouter

from ..core.kernel import Kernel
from ..core.state import SystemState, BoilerMode


# Mapowanie trybu pracy na ładny string do GUI
MODE_DISPLAY = {
    BoilerMode.IGNITION: "rozpalanie",
    BoilerMode.WORK: "praca",
    BoilerMode.OFF: "off",
    BoilerMode.MANUAL: "ręczne",
}


def create_state_router(kernel: Kernel) -> APIRouter:
    """
    Router HTTP z endpointami stanu (/current).
    """
    router = APIRouter(prefix="/state", tags=["state"])

    @router.get("/current")
    def get_current():
        """
        Główny endpoint dla GUI – aktualny stan kotła, temperatury, wyjścia, statusy modułów.
        """
        s: SystemState = kernel.state
        sens = s.sensors
        out = s.outputs

        return {
            "ts": s.ts,
            # surowa nazwa enuma (np. "OFF", "IGNITION", "WORK", "MANUAL")
            "mode": s.mode.name,
            # ładny string do wyświetlania w GUI (PL)
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
                "mixer_temp": sens.mixer_temp,  # temp. za zaworem mieszającym
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
                # NOWE: moc wyliczona przez PowerModule
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

    return router

