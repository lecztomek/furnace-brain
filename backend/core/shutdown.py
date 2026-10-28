"""
Outputs left on the pins when the backend stops (an update restarts it every
few minutes at worst): the pins keep their last state until the new process
starts (~10-20 s on the Pi), so a feeder caught mid-dose would push coal the
whole time. Feeder, blower and the mixing valve stop; with a warm boiler the
pumps run, so the heat still in the fire has somewhere to go.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from backend.core.state import Outputs

PUMPS_ON_ABOVE_C = 45.0


def parking_outputs(last: Outputs, boiler_temp: Optional[float]) -> Outputs:
    warm = boiler_temp is None or boiler_temp >= PUMPS_ON_ABOVE_C   # unknown = assume warm
    return replace(
        last,
        feeder_on=False,
        fan_power=0,
        mixer_open_on=False,
        mixer_close_on=False,
        pump_co_on=last.pump_co_on or warm,
        pump_cwu_on=last.pump_cwu_on or warm,
    )
