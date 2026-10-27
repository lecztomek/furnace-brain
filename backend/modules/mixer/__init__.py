from __future__ import annotations

from collections import deque
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
    Sensors,
    SystemState,
    PartialOutputs,
)

# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class MixerConfig:
    # CO
    target_temp: float = 40.0
    ok_band_degC: float = 2.0

    # Stabilize: czas impulsu zależny od błędu
    min_pulse_s: float = 0.5
    max_pulse_s: float = 3.0
    adjust_interval_s: float = 10.0

    # Ramp vs stabilize (ramp_threshold = ramp_error_factor * ok_band_degC)
    ramp_error_factor: float = 2.0

    # RAMP: prosta bramka kotła + stały impuls OPEN
    boiler_min_temp_for_open: float = 48.0    # >= tego: w ramp wolno OTWIERAĆ
    boiler_force_close_temp: float = 43.0     # <= tego: PROTECT -> zawsze ZAMYKAJ

    ramp_open_pulse_s: float = 2.0            # stały impuls OPEN w ramp
    ramp_adjust_interval_s: float = 60.0      # osobny interwał dla ramp

    # PROTECT: zawsze CLOSE z osobnym interwałem (ochrona powrotu / kotła)
    protect_interval_s: float = 15.0
    protect_pulse_s: float = 6.0

    # Preclose na IGNITION
    preclose_on_ignition_enabled: bool = True
    preclose_full_close_time_s: float = 120.0

    # Heating curve: the target follows the outside temperature (off = fixed target_temp).
    # Defaults fitted to how the target was set by hand in the 2025/26 season
    # (about 30 C at 0 C outside, 0.45 C more water per degree of frost).
    curve_enabled: bool = False
    curve_at_0: float = 30.0          # target at 0 C outside
    curve_slope: float = 0.45         # C of water per C of outside temperature
    curve_min: float = 22.0
    curve_max: float = 45.0
    curve_avg_h: float = 6.0          # outside temperature averaged over (house and water inertia)


MOVES_WINDOW_S = 30 * 60.0     # net valve movement shown on the main page

CURVE_SAMPLE_S = 60.0          # outside temperature sampled once a minute
CURVE_STALE_S = 3600.0         # no outside temperature for an hour -> fixed target_temp


class MixerModule(ModuleInterface):
    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[MixerConfig] = None,
    ) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        # debug timings (czas kontrolny - monotonic)
        self._move_start_ts: Optional[float] = None
        self._move_planned_s: Optional[float] = None

        self._config = config or MixerConfig()
        self._load_config_from_file()

        # Stan ruchu zaworu (czas kontrolny - monotonic)
        self._movement_until_ts: Optional[float] = None
        self._movement_direction: Optional[str] = None  # "open" / "close" / None

        # Interwały akcji (osobne)
        self._last_stabilize_action_ts: Optional[float] = None
        self._last_ramp_action_ts: Optional[float] = None
        self._last_protect_action_ts: Optional[float] = None

        # Ostatni tryb logiki mieszacza
        self._last_mode: Optional[str] = None  # off / manual / protect / ignition_preclose / ramp / stabilize

        # Do wykrywania przejść trybów kotła
        self._prev_boiler_mode: Optional[BoilerMode] = None

        # Jednorazowy pre-close na wejściu w IGNITION
        self._ignition_preclose_done: bool = False
        self._force_full_close: bool = False

        # Debug: pamięć ostatnich wyjść (żeby logować tylko zmiany)
        self._last_out_open: bool = False
        self._last_out_close: bool = False

        # heating curve: outside temperature samples (ts_mono, temp) and the target in use
        self._outside: deque = deque()
        self._target: float = self._config.target_temp
        self._curve_active: bool = False

        # net valve movement: minute -> seconds (+ opening, - closing)
        self._moves: Dict[int, float] = {}
        self._moves_last_ts: Optional[float] = None

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

        mode_enum = system_state.mode
        boiler_temp = sensors.boiler_temp
        rad_temp = sensors.radiators_temp

        self._update_target(now, now_ctrl, sensors.outside_temp, system_state, events)

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

        # ------------------------
        # MANUAL: nie dotykaj wyjść (None), tylko reset stanu
        # ------------------------
        if mode_enum == BoilerMode.MANUAL:
            self._force_full_close = False
            self._stop_movement()

            self._last_stabilize_action_ts = None
            self._last_ramp_action_ts = None
            self._last_protect_action_ts = None

            effective_mode = "manual"

            if prev_mode != effective_mode:
                events.append(Event(
                    ts=now, source=self.id, level=EventLevel.INFO,
                    type="MIXER_MODE_CHANGED",
                    message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                ))

            self._last_mode = effective_mode
            self._prev_boiler_mode = mode_enum

            # Nie logujemy zmian przekaźników, bo manual steruje poza modułem.
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # Od tego miejsca AUTO: ustawiamy jawnie OFF na przekaźniki na każdy tick
        outputs.mixer_open_on = False
        outputs.mixer_close_on = False

        # ------------------------
        # OFF: wyłącz wyjścia + reset timerów
        # ------------------------
        if mode_enum == BoilerMode.OFF:
            self._force_full_close = False
            self._stop_movement()

            self._last_stabilize_action_ts = None
            self._last_ramp_action_ts = None
            self._last_protect_action_ts = None

            effective_mode = "off"

            if prev_mode != effective_mode:
                events.append(Event(
                    ts=now, source=self.id, level=EventLevel.INFO,
                    type="MIXER_MODE_CHANGED",
                    message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                ))

            self._last_mode = effective_mode
            self._prev_boiler_mode = mode_enum

            events.extend(self._log_output_transition(
                now=now, now_ctrl=now_ctrl,
                out_open=bool(outputs.mixer_open_on),
                out_close=bool(outputs.mixer_close_on),
                effective_mode=effective_mode,
                rad_temp=rad_temp,
                boiler_temp=boiler_temp,
            ))

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # ------------------------
        # PROTECT: priorytet nad wszystkim (zawsze CLOSE poniżej progu)
        # ------------------------
        if boiler_temp is not None and boiler_temp <= self._config.boiler_force_close_temp:
            effective_mode = "protect"
            self._force_full_close = False

            # jeśli jedziemy OPEN -> przerwij natychmiast
            if self._movement_direction == "open":
                self._stop_movement()

            # jeśli trwa CLOSE i jeszcze czas nie minął -> trzymaj CLOSE
            if self._movement_until_ts is not None and now_ctrl < self._movement_until_ts:
                if self._movement_direction == "close":
                    outputs.mixer_close_on = True
            else:
                # ruch zakończony
                self._stop_movement()

                # impuls CLOSE co protect_interval_s
                if self._can_protect(now_ctrl):
                    pulse_s = float(self._config.protect_pulse_s)
                    self._start_movement(now_ctrl, "close", pulse_s)
                    self._last_protect_action_ts = now_ctrl
                    outputs.mixer_close_on = True

                    events.append(Event(
                        ts=now, source=self.id, level=EventLevel.INFO,
                        type="MIXER_PROTECT_CLOSE",
                        message=(
                            f"Mixer: PROTECT -> CLOSE {pulse_s:.1f}s "
                            f"(T_kotla={boiler_temp:.1f}°C <= {self._config.boiler_force_close_temp:.1f}°C)"
                        ),
                        data={
                            "pulse_s": pulse_s,
                            "boiler_temp": boiler_temp,
                            "threshold": self._config.boiler_force_close_temp,
                            "mode": effective_mode,
                        },
                    ))

            if prev_mode != effective_mode:
                events.append(Event(
                    ts=now, source=self.id, level=EventLevel.INFO,
                    type="MIXER_MODE_CHANGED",
                    message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                ))

            self._last_mode = effective_mode
            self._prev_boiler_mode = mode_enum

            events.extend(self._log_output_transition(
                now=now, now_ctrl=now_ctrl,
                out_open=bool(outputs.mixer_open_on),
                out_close=bool(outputs.mixer_close_on),
                effective_mode=effective_mode,
                rad_temp=rad_temp,
                boiler_temp=boiler_temp,
            ))

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # ------------------------
        # Pre-close na wejściu w IGNITION (opcjonalnie)
        # ------------------------
        if (
            entering_ignition
            and self._config.preclose_on_ignition_enabled
            and not self._ignition_preclose_done
            and self._is_far_below_setpoint(rad_temp)
        ):
            self._ignition_preclose_done = True
            self._force_full_close = True

            self._stop_movement()
            close_s = float(self._config.preclose_full_close_time_s)
            self._start_movement(now_ctrl, "close", close_s)
            outputs.mixer_close_on = True

            events.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO,
                type="MIXER_PRECLOSE_ON_IGNITION",
                message=(
                    f"Zawór mieszający: pełne ZAMKNIĘCIE {close_s:.1f}s "
                    f"przed rampowaniem (IGNITION, T_CO={rad_temp:.1f}°C, zadana={self._target:.1f}°C)"
                ),
                data={
                    "pulse_s": close_s,
                    "radiators_temp": rad_temp,
                    "target_temp": self._target,
                    "mode": "ignition_preclose",
                    "boiler_temp": boiler_temp,
                },
            ))

            effective_mode = "ignition_preclose"
            if prev_mode != effective_mode:
                events.append(Event(
                    ts=now, source=self.id, level=EventLevel.INFO,
                    type="MIXER_MODE_CHANGED",
                    message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                ))

            self._last_mode = effective_mode
            self._prev_boiler_mode = mode_enum

            events.extend(self._log_output_transition(
                now=now, now_ctrl=now_ctrl,
                out_open=bool(outputs.mixer_open_on),
                out_close=bool(outputs.mixer_close_on),
                effective_mode=effective_mode,
                rad_temp=rad_temp,
                boiler_temp=boiler_temp,
            ))

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # ------------------------
        # Wyznaczenie trybu: ramp (tylko od dołu) vs stabilize
        # ------------------------
        if self._force_full_close:
            effective_mode = "ignition_preclose"
        else:
            if rad_temp is None:
                effective_mode = "stabilize"
            else:
                far_err = self._config.ramp_error_factor * self._config.ok_band_degC
                effective_mode = (
                    "ramp" if rad_temp < (self._target - far_err) else "stabilize"
                )

        # ------------------------
        # Sterowanie ruchem
        # ------------------------
        if effective_mode == "ignition_preclose":
            if self._movement_until_ts is not None and now_ctrl < self._movement_until_ts:
                outputs.mixer_close_on = True
            else:
                self._stop_movement()
                self._force_full_close = False

        else:
            # jeśli ruch trwa – podtrzymaj
            if self._movement_until_ts is not None and now_ctrl < self._movement_until_ts:
                if self._movement_direction == "open":
                    outputs.mixer_open_on = True
                elif self._movement_direction == "close":
                    outputs.mixer_close_on = True

            else:
                # ruch zakończony
                self._stop_movement()

                if rad_temp is not None:
                    direction: Optional[str] = None
                    pulse_s: Optional[float] = None

                    if effective_mode == "ramp":
                        # RAMP: OPEN stałym impulsem tylko jeśli kocioł >= boiler_min_temp_for_open.
                        if boiler_temp is not None and boiler_temp >= self._config.boiler_min_temp_for_open:
                            if self._can_ramp(now_ctrl):
                                direction = "open"
                                pulse_s = float(self._config.ramp_open_pulse_s)
                                self._last_ramp_action_ts = now_ctrl

                    else:
                        # STABILIZE
                        if self._can_stabilize(now_ctrl):
                            direction = self._decide_direction_stabilize(mix_temp=rad_temp)
                            if direction is not None:
                                pulse_s = self._compute_pulse_duration(mix_temp=rad_temp)
                                self._last_stabilize_action_ts = now_ctrl

                    if direction is not None and pulse_s is not None:
                        self._start_movement(now_ctrl, direction, pulse_s)

                        if direction == "open":
                            outputs.mixer_open_on = True
                        else:
                            outputs.mixer_close_on = True

                        events.append(Event(
                            ts=now, source=self.id, level=EventLevel.INFO,
                            type="MIXER_MOVE",
                            message=(
                                f"Zawór mieszający: {direction.upper()} {pulse_s:.1f}s "
                                f"(T_CO={rad_temp:.1f}°C, zadana={self._target:.1f}°C, tryb={effective_mode})"
                            ),
                            data={
                                "direction": direction,
                                "pulse_s": pulse_s,
                                "radiators_temp": rad_temp,
                                "target_temp": self._target,
                                "mode": effective_mode,
                                "boiler_temp": boiler_temp,
                            },
                        ))

        # event zmiany trybu
        if prev_mode != effective_mode:
            events.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO,
                type="MIXER_MODE_CHANGED",
                message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                data={"prev_mode": prev_mode, "mode": effective_mode},
            ))

        self._last_mode = effective_mode
        self._prev_boiler_mode = mode_enum

        # loguj tylko zmiany przekaźników OPEN/CLOSE
        events.extend(self._log_output_transition(
            now=now, now_ctrl=now_ctrl,
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
        now: float,
        now_ctrl: float,
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
                    "until_ts": self._movement_until_ts,
                    "mode": effective_mode,
                    "radiators_temp": rad_temp,
                    "boiler_temp": boiler_temp,
                    "target_temp": self._target,
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
                    "target_temp": self._target,
                }
            ))
            self._move_start_ts = None
            self._move_planned_s = None

        # START CLOSE
        if out_close and not self._last_out_close:
            planned = (self._movement_until_ts - now_ctrl) if self._movement_until_ts else None
            self._move_start_ts = now_ctrl
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
                    "until_ts": self._movement_until_ts,
                    "mode": effective_mode,
                    "radiators_temp": rad_temp,
                    "boiler_temp": boiler_temp,
                    "target_temp": self._target,
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
                    "target_temp": self._target,
                }
            ))
            self._move_start_ts = None
            self._move_planned_s = None

        self._last_out_open = out_open
        self._last_out_close = out_close
        return evs

    def _is_far_below_setpoint(self, rad_temp: Optional[float]) -> bool:
        if rad_temp is None:
            return False
        far_err = self._config.ramp_error_factor * self._config.ok_band_degC
        return rad_temp < (self._target - far_err)

    def _stop_movement(self) -> None:
        self._movement_until_ts = None
        self._movement_direction = None

    def _can_stabilize(self, now_ctrl: float) -> bool:
        if self._last_stabilize_action_ts is None:
            return True
        return (now_ctrl - self._last_stabilize_action_ts) >= self._config.adjust_interval_s

    def _can_ramp(self, now_ctrl: float) -> bool:
        if self._last_ramp_action_ts is None:
            return True
        return (now_ctrl - self._last_ramp_action_ts) >= self._config.ramp_adjust_interval_s

    def _can_protect(self, now_ctrl: float) -> bool:
        if self._last_protect_action_ts is None:
            return True
        return (now_ctrl - self._last_protect_action_ts) >= self._config.protect_interval_s

    def _decide_direction_stabilize(self, mix_temp: float) -> Optional[str]:
        t_set = self._target
        band = self._config.ok_band_degC
        if mix_temp < t_set - band:
            return "open"
        if mix_temp > t_set + band:
            return "close"
        return None

    def _compute_pulse_duration(self, mix_temp: float) -> float:
        t_set = self._target
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

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    # ---------- heating curve ----------

    def _outside_avg(self, now_ctrl: float) -> Optional[float]:
        window = max(self._config.curve_avg_h, 0.25) * 3600.0
        while self._outside and self._outside[0][0] < now_ctrl - window:
            self._outside.popleft()
        if not self._outside or now_ctrl - self._outside[-1][0] > CURVE_STALE_S:
            return None
        return sum(v for _, v in self._outside) / len(self._outside)

    def _movement(self, now_ctrl: float) -> Dict[str, Any]:
        """Net opening of the valve in the last 30 min, from the outputs of the previous ticks."""
        last = self._moves_last_ts
        self._moves_last_ts = now_ctrl
        if last is not None and 0 < now_ctrl - last < 10:
            sign = 1 if self._last_out_open else -1 if self._last_out_close else 0
            if sign:
                minute = int(now_ctrl // 60)
                self._moves[minute] = self._moves.get(minute, 0.0) + sign * (now_ctrl - last)
        oldest = int((now_ctrl - MOVES_WINDOW_S) // 60)
        for minute in [m for m in self._moves if m <= oldest]:
            del self._moves[minute]
        net_s = sum(self._moves.values())
        travel = max(float(self._config.preclose_full_close_time_s), 1.0)
        return {
            "moves_30m_s": round(net_s, 1),
            # as % of the full stroke (full closing time from the settings)
            "moves_30m_pct": round(100.0 * net_s / travel),
            "moving": "open" if self._last_out_open else "close" if self._last_out_close else None,
        }

    def curve_target(self, outside: float) -> float:
        c = self._config
        t = c.curve_at_0 - c.curve_slope * outside
        return round(max(c.curve_min, min(c.curve_max, t)), 1)

    def _update_target(self, now: float, now_ctrl: float, outside: Optional[float],
                       system_state: SystemState, events: List[Event]) -> None:
        if outside is not None and (not self._outside or now_ctrl - self._outside[-1][0] >= CURVE_SAMPLE_S):
            self._outside.append((now_ctrl, float(outside)))
        avg = self._outside_avg(now_ctrl)
        active = bool(self._config.curve_enabled) and avg is not None
        self._target = self.curve_target(avg) if active else self._config.target_temp

        if self._config.curve_enabled and active != self._curve_active:
            events.append(Event(
                ts=now, source=self.id, level=EventLevel.INFO if active else EventLevel.WARNING,
                type="MIXER_CURVE_ON" if active else "MIXER_CURVE_NO_WEATHER",
                message=(f"Krzywa grzewcza: zadana {self._target:.1f}°C (na dworze średnio {avg:.1f}°C)" if active
                         else f"Krzywa grzewcza: brak temperatury zewnętrznej - stała zadana {self._target:.1f}°C"),
                data={"target": self._target, "outside_avg": avg},
            ))
        self._curve_active = active
        system_state.runtime["mixer"] = {
            **self._movement(now_ctrl),
            "target": self._target,
            "curve_enabled": bool(self._config.curve_enabled),
            "curve_active": active,
            "outside_avg": None if avg is None else round(avg, 1),
        }

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
        if "boiler_force_close_temp" in values:
            self._config.boiler_force_close_temp = float(values["boiler_force_close_temp"])

        if "ramp_open_pulse_s" in values:
            self._config.ramp_open_pulse_s = float(values["ramp_open_pulse_s"])
        if "ramp_adjust_interval_s" in values:
            self._config.ramp_adjust_interval_s = float(values["ramp_adjust_interval_s"])

        if "protect_interval_s" in values:
            self._config.protect_interval_s = float(values["protect_interval_s"])
        if "protect_pulse_s" in values:
            self._config.protect_pulse_s = float(values["protect_pulse_s"])

        if "preclose_on_ignition_enabled" in values:
            self._config.preclose_on_ignition_enabled = bool(values["preclose_on_ignition_enabled"])
        if "preclose_full_close_time_s" in values:
            self._config.preclose_full_close_time_s = float(values["preclose_full_close_time_s"])
        self._apply_curve(values)

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
            "boiler_force_close_temp",
            "ramp_open_pulse_s",
            "ramp_adjust_interval_s",
            "protect_interval_s",
            "protect_pulse_s",
            "preclose_on_ignition_enabled",
            "preclose_full_close_time_s",
        ):
            if field in data:
                if field == "preclose_on_ignition_enabled":
                    setattr(self._config, field, bool(data[field]))
                else:
                    setattr(self._config, field, float(data[field]))
        self._apply_curve(data)

    def _apply_curve(self, values: Dict[str, Any]) -> None:
        if "curve_enabled" in values:
            self._config.curve_enabled = bool(values["curve_enabled"])
        for name in ("curve_at_0", "curve_slope", "curve_min", "curve_max", "curve_avg_h"):
            if name in values:
                setattr(self._config, name, float(values[name]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

