from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    BoilerMode,
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,  # zostawiam jak było, choć nieużywane
    Sensors,
    SystemState,
    PartialOutputs,
)

logger = logging.getLogger(__name__)

# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class MixerConfig:
    target_temp: float = 40.0
    ok_band_degC: float = 2.0

    min_pulse_s: float = 0.5
    max_pulse_s: float = 3.0
    adjust_interval_s: float = 10.0

    ramp_error_factor: float = 2.0

    boiler_min_temp_for_open: float = 55.0
    boiler_max_drop_degC: float = 5.0
    boiler_recover_factor: float = 0.5

    preclose_on_ignition_enabled: bool = True
    preclose_full_close_time_s: float = 120.0


class MixerModule(ModuleInterface):
    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[MixerConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        # debug timings (czas kontrolny - monotonic)
        self._move_start_ts: Optional[float] = None
        self._move_direction_last: Optional[str] = None
        self._move_planned_s: Optional[float] = None

        self._config = config or MixerConfig()
        self._load_config_from_file()

        # Stan ruchu zaworu (czas kontrolny - monotonic)
        self._movement_until_ts: Optional[float] = None
        self._movement_direction: Optional[str] = None  # "open" / "close" / None
        self._last_action_ts: Optional[float] = None  # czas kontrolny - monotonic

        # Ochrona kotła – śledzenie wpływu OTWÓRZ na kocioł (tryb "ramp")
        self._last_open_start_boiler_temp: Optional[float] = None
        self._last_open_drop_too_big: bool = False

        # Ostatni tryb logiki mieszacza ("off" / "ramp" / "stabilize" / "ignition_preclose")
        self._last_mode: Optional[str] = None

        # Do wykrywania przejść trybów kotła
        self._prev_boiler_mode: Optional[BoilerMode] = None

        # Jednorazowy pre-close na wejściu w IGNITION
        self._ignition_preclose_done: bool = False
        self._force_full_close: bool = False

        # Debug: pamięć ostatnich wyjść (żeby logować tylko zmiany)
        self._last_out_open: bool = False
        self._last_out_close: bool = False

    @property
    def id(self) -> str:
        return "mixer"

    def tick(
        self,
        now: float,  # wall time (logi)
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()

        # czas kontrolny (monotonic) do wszelkich timerów/cykli
        now_ctrl = system_state.ts_mono

        # FIX: PartialOutputs jest deltą (None = nie zmieniaj)
        outputs.mixer_open_on = False
        outputs.mixer_close_on = False

        mode_enum = system_state.mode
        boiler_temp = sensors.boiler_temp
        rad_temp = sensors.radiators_temp

        prev_mode = self._last_mode

        entering_ignition = (
            mode_enum == BoilerMode.IGNITION
            and self._prev_boiler_mode != BoilerMode.IGNITION
        )
        leaving_ignition = (
            mode_enum != BoilerMode.IGNITION
            and self._prev_boiler_mode == BoilerMode.IGNITION
        )

        if leaving_ignition:
            self._ignition_preclose_done = False
            self._force_full_close = False

        # OFF/MANUAL zawsze wygrywa
        if mode_enum in (BoilerMode.OFF, BoilerMode.MANUAL):
            self._force_full_close = False
            self._stop_movement()
            effective_mode = "off"

            if prev_mode != effective_mode:
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="MIXER_MODE_CHANGED",
                        message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                        data={"prev_mode": prev_mode, "mode": effective_mode},
                    )
                )

            self._last_mode = effective_mode
            self._prev_boiler_mode = mode_enum

            # log przejścia wyjść (na końcu ticka)
            events.extend(self._log_output_transition(
                now=now,
                now_ctrl=now_ctrl,
                out_open=bool(outputs.mixer_open_on),
                out_close=bool(outputs.mixer_close_on),
                effective_mode=effective_mode,
                rad_temp=rad_temp,
                boiler_temp=boiler_temp,
            ))

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # Pre-close na wejściu w IGNITION (opcjonalnie)
        if (
            entering_ignition
            and self._config.preclose_on_ignition_enabled
            and not self._ignition_preclose_done
            and self._is_far_from_setpoint(rad_temp)
        ):
            self._ignition_preclose_done = True
            self._force_full_close = True

            self._stop_movement()
            close_s = float(self._config.preclose_full_close_time_s)
            self._start_movement(now_ctrl, "close", close_s)

            outputs.mixer_close_on = True

            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="MIXER_PRECLOSE_ON_IGNITION",
                    message=(
                        f"Zawór mieszający: pełne ZAMKNIĘCIE {close_s:.1f}s "
                        f"przed rampowaniem (wejście w IGNITION, "
                        f"T_CO={rad_temp:.1f}°C, zadana={self._config.target_temp:.1f}°C)"
                    ),
                    data={
                        "pulse_s": close_s,
                        "radiators_temp": rad_temp,
                        "target_temp": self._config.target_temp,
                        "mode": "ignition_preclose",
                        "boiler_temp": boiler_temp,
                    },
                )
            )

            effective_mode = "ignition_preclose"
            if prev_mode != effective_mode:
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="MIXER_MODE_CHANGED",
                        message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                        data={"prev_mode": prev_mode, "mode": effective_mode},
                    )
                )

            self._last_mode = effective_mode
            self._prev_boiler_mode = mode_enum

            events.extend(self._log_output_transition(
                now=now,
                now_ctrl=now_ctrl,
                out_open=bool(outputs.mixer_open_on),
                out_close=bool(outputs.mixer_close_on),
                effective_mode=effective_mode,
                rad_temp=rad_temp,
                boiler_temp=boiler_temp,
            ))

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # Wyznaczenie trybu logiki mieszacza
        if self._force_full_close:
            effective_mode = "ignition_preclose"
        else:
            if rad_temp is None:
                effective_mode = "stabilize"
            else:
                t_set = self._config.target_temp
                band = self._config.ok_band_degC
                error = abs(t_set - rad_temp)
                far_err = self._config.ramp_error_factor * band
                effective_mode = "ramp" if error > far_err else "stabilize"

        # Główna logika ruchu zaworu
        if effective_mode == "ignition_preclose":
            if self._movement_until_ts is not None and now_ctrl < self._movement_until_ts:
                outputs.mixer_close_on = True
            else:
                self._stop_movement()
                self._force_full_close = False
        else:
            if self._movement_until_ts is not None and now_ctrl < self._movement_until_ts:
                if self._movement_direction == "open":
                    outputs.mixer_open_on = True
                elif self._movement_direction == "close":
                    outputs.mixer_close_on = True
            else:
                finished_dir = self._movement_direction

                if finished_dir == "open":
                    self._update_boiler_drop(boiler_temp)

                self._stop_movement()

                if self._can_adjust(now_ctrl) and rad_temp is not None:
                    if effective_mode == "ramp":
                        direction = self._decide_direction_ramp(
                            mix_temp=rad_temp,
                            boiler_temp=boiler_temp,
                        )
                    else:
                        direction = self._decide_direction_work(mix_temp=rad_temp)

                    if direction is not None:
                        pulse_s = self._compute_pulse_duration(mix_temp=rad_temp)

                        if effective_mode == "ramp" and direction == "open":
                            self._last_open_start_boiler_temp = boiler_temp

                        self._start_movement(now_ctrl, direction, pulse_s)

                        if direction == "open":
                            outputs.mixer_open_on = True
                        else:
                            outputs.mixer_close_on = True

                        events.append(
                            Event(
                                ts=now,
                                source=self.id,
                                level=EventLevel.INFO,
                                type="MIXER_MOVE",
                                message=(
                                    f"Zawór mieszający: {direction.upper()} {pulse_s:.1f}s "
                                    f"(T_CO={rad_temp:.1f}°C, zadana={self._config.target_temp:.1f}°C, "
                                    f"tryb={effective_mode})"
                                ),
                                data={
                                    "direction": direction,
                                    "pulse_s": pulse_s,
                                    "radiators_temp": rad_temp,
                                    "target_temp": self._config.target_temp,
                                    "mode": effective_mode,
                                    "boiler_temp": boiler_temp,
                                },
                            )
                        )

        # Event zmiany trybu logiki mieszacza:
        if prev_mode != effective_mode:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="MIXER_MODE_CHANGED",
                    message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                )
            )

        self._last_mode = effective_mode
        self._prev_boiler_mode = mode_enum

        # DEBUG: loguj tylko zmiany przekaźników OPEN/CLOSE
        events.extend(self._log_output_transition(
            now=now,
            now_ctrl=now_ctrl,
            out_open=bool(outputs.mixer_open_on),
            out_close=bool(outputs.mixer_close_on),
            effective_mode=effective_mode,
            rad_temp=rad_temp,
            boiler_temp=boiler_temp,
        ))

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # ---------- LOGIKA POMOCNICZA ----------

    def _log_output_transition(
        self,
        now: float,          # wall time do event.ts
        now_ctrl: float,     # monotonic do liczenia "plan/ran"
        out_open: bool,
        out_close: bool,
        effective_mode: str,
        rad_temp: Optional[float],
        boiler_temp: Optional[float],
    ) -> List[Event]:
        evs: List[Event] = []

        # START OPEN
        if out_open and not self._last_out_open:
            planned = (self._movement_until_ts - now_ctrl) if self._movement_until_ts else None
            self._move_start_ts = now_ctrl
            self._move_direction_last = "open"
            self._move_planned_s = planned

            evs.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO,
                type="MIXER_MOVE_START",
                message=(
                    f"Mixer: START OPEN (plan={planned:.2f}s, mode={effective_mode})"
                    if planned is not None else f"Mixer: START OPEN (mode={effective_mode})"
                ),
                data={
                    "direction": "open",
                    "planned_pulse_s": planned,
                    "until_ts": self._movement_until_ts,  # monotonic timestamp
                    "mode": effective_mode,
                    "radiators_temp": rad_temp,
                    "boiler_temp": boiler_temp,
                    "target_temp": self._config.target_temp,
                }
            ))

        # STOP OPEN
        if (not out_open) and self._last_out_open:
            actual = (now_ctrl - self._move_start_ts) if self._move_start_ts is not None else None
            evs.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO,
                type="MIXER_MOVE_STOP",
                message=(
                    f"Mixer: STOP OPEN (ran={actual:.2f}s, plan={self._move_planned_s:.2f}s, mode={effective_mode})"
                    if actual is not None and self._move_planned_s is not None else "Mixer: STOP OPEN"
                ),
                data={
                    "direction": "open",
                    "actual_run_s": actual,
                    "planned_pulse_s": self._move_planned_s,
                    "mode": effective_mode,
                    "radiators_temp": rad_temp,
                    "boiler_temp": boiler_temp,
                    "target_temp": self._config.target_temp,
                }
            ))
            self._move_start_ts = None
            self._move_direction_last = None
            self._move_planned_s = None

        # START CLOSE
        if out_close and not self._last_out_close:
            planned = (self._movement_until_ts - now_ctrl) if self._movement_until_ts else None
            self._move_start_ts = now_ctrl
            self._move_direction_last = "close"
            self._move_planned_s = planned

            evs.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO,
                type="MIXER_MOVE_START",
                message=(
                    f"Mixer: START CLOSE (plan={planned:.2f}s, mode={effective_mode})"
                    if planned is not None else f"Mixer: START CLOSE (mode={effective_mode})"
                ),
                data={
                    "direction": "close",
                    "planned_pulse_s": planned,
                    "until_ts": self._movement_until_ts,  # monotonic timestamp
                    "mode": effective_mode,
                    "radiators_temp": rad_temp,
                    "boiler_temp": boiler_temp,
                    "target_temp": self._config.target_temp,
                }
            ))

        # STOP CLOSE
        if (not out_close) and self._last_out_close:
            actual = (now_ctrl - self._move_start_ts) if self._move_start_ts is not None else None
            evs.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO,
                type="MIXER_MOVE_STOP",
                message=(
                    f"Mixer: STOP CLOSE (ran={actual:.2f}s, plan={self._move_planned_s:.2f}s, mode={effective_mode})"
                    if actual is not None and self._move_planned_s is not None else "Mixer: STOP CLOSE"
                ),
                data={
                    "direction": "close",
                    "actual_run_s": actual,
                    "planned_pulse_s": self._move_planned_s,
                    "mode": effective_mode,
                    "radiators_temp": rad_temp,
                    "boiler_temp": boiler_temp,
                    "target_temp": self._config.target_temp,
                }
            ))
            self._move_start_ts = None
            self._move_direction_last = None
            self._move_planned_s = None

        self._last_out_open = out_open
        self._last_out_close = out_close
        return evs

    def _is_far_from_setpoint(self, rad_temp: Optional[float]) -> bool:
        if rad_temp is None:
            return False
        t_set = self._config.target_temp
        band = self._config.ok_band_degC
        far_err = self._config.ramp_error_factor * band
        return abs(t_set - rad_temp) > far_err

    def _stop_movement(self) -> None:
        self._movement_until_ts = None
        self._movement_direction = None

    def _can_adjust(self, now_ctrl: float) -> bool:
        if self._last_action_ts is None:
            return True
        return (now_ctrl - self._last_action_ts) >= self._config.adjust_interval_s

    def _decide_direction_work(self, mix_temp: float) -> Optional[str]:
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        if mix_temp < t_set - band:
            return "open"
        if mix_temp > t_set + band:
            return "close"
        return None

    def _decide_direction_ramp(
        self,
        mix_temp: float,
        boiler_temp: Optional[float],
    ) -> Optional[str]:
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        if mix_temp > t_set + band:
            return "close"

        if mix_temp < t_set - band:
            if boiler_temp is None:
                return None

            if boiler_temp < self._config.boiler_min_temp_for_open:
                return None

            if self._last_open_drop_too_big and self._last_open_start_boiler_temp is not None:
                max_drop = self._config.boiler_max_drop_degC
                recover_factor = self._config.boiler_recover_factor
                allowed_drop = max_drop * (1.0 - recover_factor)

                drop_now = self._last_open_start_boiler_temp - boiler_temp
                if drop_now > allowed_drop:
                    return None
                else:
                    self._last_open_drop_too_big = False

            return "open"

        return None

    def _update_boiler_drop(self, boiler_temp: Optional[float]) -> None:
        if boiler_temp is None:
            return
        if self._last_open_start_boiler_temp is None:
            return

        drop = self._last_open_start_boiler_temp - boiler_temp
        if drop > self._config.boiler_max_drop_degC:
            self._last_open_drop_too_big = True

    def _compute_pulse_duration(self, mix_temp: float) -> float:
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        error = abs(t_set - mix_temp)
        max_err = 10.0
        eff_err = max(0.0, min(error - band, max_err))
        k = eff_err / max_err  # 0..1

        pulse = self._config.min_pulse_s + k * (self._config.max_pulse_s - self._config.min_pulse_s)
        if pulse < self._config.min_pulse_s:
            pulse = self._config.min_pulse_s
        if pulse > self._config.max_pulse_s:
            pulse = self._config.max_pulse_s

        return pulse

    def _start_movement(self, now_ctrl: float, direction: str, pulse_s: float) -> None:
        self._movement_direction = direction
        self._movement_until_ts = now_ctrl + pulse_s
        self._last_action_ts = now_ctrl

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "target_temp" in values:
            self._config.target_temp = float(values["target_temp"])
        if "ok_band_degC" in values:
            self._config.ok_band_degC = float(values["ok_band_degC"])

        if "min_pulse_s" in values:
            self._config.min_pulse_s = float(values["min_pulse_s"])
        if "max_pulse_s" in values:
            self._config.max_pulse_s = float(values["max_pulse_s"])
        if "adjust_interval_s" in values:
            self._config.adjust_interval_s = float(values["adjust_interval_s"])

        if "ramp_error_factor" in values:
            self._config.ramp_error_factor = float(values["ramp_error_factor"])

        if "boiler_min_temp_for_open" in values:
            self._config.boiler_min_temp_for_open = float(values["boiler_min_temp_for_open"])
        if "boiler_max_drop_degC" in values:
            self._config.boiler_max_drop_degC = float(values["boiler_max_drop_degC"])
        if "boiler_recover_factor" in values:
            self._config.boiler_recover_factor = float(values["boiler_recover_factor"])

        if "preclose_on_ignition_enabled" in values:
            self._config.preclose_on_ignition_enabled = bool(values["preclose_on_ignition_enabled"])
        if "preclose_full_close_time_s" in values:
            self._config.preclose_full_close_time_s = float(values["preclose_full_close_time_s"])

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for field in (
            "target_temp",
            "ok_band_degC",
            "min_pulse_s",
            "max_pulse_s",
            "adjust_interval_s",
            "ramp_error_factor",
            "boiler_min_temp_for_open",
            "boiler_max_drop_degC",
            "boiler_recover_factor",
            "preclose_on_ignition_enabled",
            "preclose_full_close_time_s",
        ):
            if field in data:
                if field == "preclose_on_ignition_enabled":
                    setattr(self._config, field, bool(data[field]))
                else:
                    setattr(self._config, field, float(data[field]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

