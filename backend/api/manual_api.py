# backend/api/manual_api.py
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, conint

from ..core.kernel import Kernel
from ..core.state import SystemState, BoilerMode


class ManualOutputsPatch(BaseModel):
    fan_power: Optional[conint(ge=0, le=100)] = None

    feeder_on: Optional[bool] = None
    pump_co_on: Optional[bool] = None
    pump_cwu_on: Optional[bool] = None

    mixer_open_on: Optional[bool] = None
    mixer_close_on: Optional[bool] = None


def create_manual_router(kernel: Kernel) -> APIRouter:
    router = APIRouter(prefix="/manual", tags=["manual"])

    @router.get("/current")
    def get_manual_current():
        """
        Widok MANUAL czyta TYLKO te wartości.
        """
        s: SystemState = kernel.state
        m = s.manual

        return {
            "ts": s.ts,
            "mode": s.mode.name,
            "manual": {
                "fan_power": int(m.fan_power),
                "feeder_on": bool(m.feeder_on),
                "pump_co_on": bool(m.pump_co_on),
                "pump_cwu_on": bool(m.pump_cwu_on),
                "mixer_open_on": bool(m.mixer_open_on),
                "mixer_close_on": bool(m.mixer_close_on),
                "last_update_ts": float(m.last_update_ts),
            },
        }

    @router.post("/outputs")
    def set_manual_outputs(patch: ManualOutputsPatch):
        """
        Zapisuje stan ręczny (SystemState.manual) — tylko w trybie MANUAL.
        """
        s: SystemState = kernel.state
        if s.mode != BoilerMode.MANUAL:
            raise HTTPException(
                status_code=409,
                detail={"msg": "Wyjścia można zmieniać tylko w trybie MANUAL."},
            )

        # Nie pozwalamy na dwa kierunki naraz
        if patch.mixer_open_on is True and patch.mixer_close_on is True:
            raise HTTPException(
                status_code=422,
                detail={"msg": "mixer_open_on i mixer_close_on nie mogą być jednocześnie TRUE."},
            )

        m = s.manual
        m.last_update_ts = time.time()

        if patch.fan_power is not None:
            m.fan_power = int(patch.fan_power)

        if patch.feeder_on is not None:
            m.feeder_on = bool(patch.feeder_on)

        if patch.pump_co_on is not None:
            m.pump_co_on = bool(patch.pump_co_on)

        if patch.pump_cwu_on is not None:
            m.pump_cwu_on = bool(patch.pump_cwu_on)

        # mixer z mutual exclusion
        if patch.mixer_open_on is not None:
            m.mixer_open_on = bool(patch.mixer_open_on)
            if m.mixer_open_on:
                m.mixer_close_on = False

        if patch.mixer_close_on is not None:
            m.mixer_close_on = bool(patch.mixer_close_on)
            if m.mixer_close_on:
                m.mixer_open_on = False

        return {"ok": True}

    return router
