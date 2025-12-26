from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import time
import math
from collections import deque

import yaml  # pip install pyyaml

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


@dataclass
class WorkNeuroFuzzyPowerConfig:
    # --- Base behaviour (Mamdani) ---
    enabled: bool = True
    boiler_set_temp: float = 56.0

    min_power: float = 15.0
    max_power: float = 100.0

    max_slew_rate_percent_per_min: float = 10.0  # 0.0 = off

    boiler_tau_s: float = 180.0

    # ZMIANA: rozdzielamy filtr spalin na FAST i BASE.
    # - flue_base_tau_s: wolny filtr (do sterowania fuzzy / learning / autotune)
    # - flue_fast_tau_s: szybki filtr (do diagnostyki i ewentualnie wykrywania pików)
    #
    # Kompatybilność:
    # - zachowujemy flue_tau_s jako alias "base", żeby stare values.yaml działało.
    flue_tau_s: float = 60.0  # traktowane jako BASE (dla kompatybilności)
    flue_fast_tau_s: Optional[float] = None  # jeśli None -> wyliczamy z flue_tau_s

    # Flue weight vs |error|
    flue_weight_band_C: float = 8.0
    flue_weight_near: float = 1.2
    flue_weight_far: float = 0.1

    # Error e = T_set - T_boiler_f [°C]
    e_nb_a: float = -12.0
    e_nb_b: float = -8.0
    e_nb_c: float = -4.0
    e_nb_d: float = -2.0

    e_ns_a: float = -4.0
    e_ns_b: float = -1.8
    e_ns_c: float = -0.3

    e_ze_a: float = -1.0
    e_ze_b: float = 0.0
    e_ze_c: float = 1.0

    e_ps_a: float = 0.3
    e_ps_b: float = 1.8
    e_ps_c: float = 4.0

    e_pb_a: float = 2.5
    e_pb_b: float = 4.5
    e_pb_c: float = 8.0
    e_pb_d: float = 12.0

    # Boiler rate r = dT/dt [°C/min]
    r_fall_a: float = -1.0
    r_fall_b: float = -0.4
    r_fall_c: float = -0.12
    r_fall_d: float = -0.03

    r_stable_a: float = -0.06
    r_stable_b: float = 0.0
    r_stable_c: float = 0.06

    r_rise_a: float = 0.03
    r_rise_b: float = 0.10
    r_rise_c: float = 0.25
    r_rise_d: float = 1.0

    # Flue thresholds (auto tuned)
    flue_min_C: float = 110.0
    flue_mid_C: float = 160.0
    flue_max_C: float = 260.0
    flue_overlap_ratio: float = 0.20
    flue_vhigh_margin_C: float = 20.0

    # ΔP universe
    delta_universe_min: float = -6.0
    delta_universe_max: float = 6.0
    delta_universe_step: float = 0.05
    delta_scale: float = 0.6

    # --- Neuro learning (weights of rules) ---
    neuro_enabled: bool = True
    learning_delay_s: float = 120.0
    learning_min_update_interval_s: float = 5.0
    learning_deadzone_C: float = 0.3
    learning_freeze_on_saturation: bool = True
    learning_rate: float = 0.05
    learning_reg: float = 0.01
    rule_weight_min: float = 0.5
    rule_weight_max: float = 1.8
    learning_buffer_max: int = 900
    learning_event_delta_threshold: float = 0.25
    learning_event_min_interval_s: float = 120.0

    # --- Reward shaping (anti-jerk / anti-flue-spikes) ---
    reward_temp_gain: float = 1.0   # weight of temperature objective
    reward_k_dp: float = 0.03       # penalty for |ΔP| (power movement)
    reward_k_ddp: float = 0.08      # penalty for |ΔΔP| (zig-zag / jerk)
    reward_k_tf: float = 0.004      # penalty for (flue - flue_mid)+
    reward_k_dtf: float = 0.02      # penalty for (dFlue/dt)+ [°C/min]
    reward_clip: float = 2.0        # clamp reward

    # --- Auto-calibration (self-tuning flue thresholds) ---
    auto_flue_enabled: bool = True
    auto_flue_window_s: float = 20 * 60.0
    auto_flue_update_interval_s: float = 30.0
    auto_flue_stable_abs_err_C: float = 0.8
    auto_flue_stable_rate_C_per_min: float = 0.15
    auto_flue_min_bound_C: float = 60.0
    auto_flue_max_bound_C: float = 400.0
    auto_flue_ema_alpha: float = 0.05
    auto_flue_q_low: float = 0.20
    auto_flue_q_mid: float = 0.50
    auto_flue_q_high: float = 0.80
    auto_flue_min_span_C: float = 10.0
    auto_flue_event_min_interval_s: float = 300.0  # log changes every 5 min

    # --- Online adaptation (self-tuning stability knobs) ---
    adapt_enabled: bool = True
    adapt_window_s: float = 20 * 60.0
    adapt_update_interval_s: float = 60.0
    adapt_event_min_interval_s: float = 300.0

    # "temp OK" gate
    adapt_err_ok_C: float = 0.6          # below -> we consider temperature held
    adapt_err_bad_C: float = 1.5         # above -> allow recovering aggressiveness
    adapt_rate_ok_C_per_min: float = 0.25

    # power/flue jitter thresholds
    adapt_power_std_high: float = 6.0        # pkt%
    adapt_power_absdp_mean_high: float = 4.0 # pkt% per tick
    adapt_flue_std_high: float = 12.0        # °C
    adapt_flue_rate_max_high: float = 25.0   # °C/min (positive spikes)

    # adaptation steps (small, slow)
    adapt_delta_scale_step: float = 0.02         # absolute
    adapt_flue_weight_near_step: float = 0.05    # absolute
    adapt_flue_weight_band_step_C: float = 0.5   # absolute

    # bounds for adapted params
    adapt_delta_scale_min: float = 0.25
    adapt_delta_scale_max: float = 1.50

    adapt_flue_weight_near_min: float = 0.4
    adapt_flue_weight_near_max: float = 3.0

    adapt_flue_weight_band_min_C: float = 3.0
    adapt_flue_weight_band_max_C: float = 20.0

    # Persist
    state_dir: str = "data"
    state_file: str = "power_work_neuro_fuzzy_state.yaml"
    state_save_interval_s: float = 30.0
    state_max_age_s: float = 15 * 60.0
    state_max_boiler_temp_delta_C: float = 5.0
    state_max_flue_temp_delta_C: float = 30.0


@dataclass
class _LearningSample:
    ts_ctrl: float
    abs_err: float
    phi: List[float]  # normalized base firing strengths


@dataclass
class _RuleSpec:
    name: str
    out_label: str


@dataclass
class _AdaptSample:
    ts_ctrl: float
    abs_err: float
    rate_c_per_min: float
    power: float
    flue_base: float


class WorkNeuroFuzzyPowerModule(ModuleInterface):
    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[WorkNeuroFuzzyPowerConfig] = None,
        data_root: Optional[Path] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or WorkNeuroFuzzyPowerConfig()

        if data_root is not None:
            self._persist_root = (Path(data_root).resolve() / "modules" / self.id).resolve()
        else:
            self._persist_root = self._base_path.resolve()

        # paths before load
        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        # load values.yaml
        self._load_config_from_file()

        # paths after load
        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        # runtime
        self._power: float = 0.0
        self._last_in_work: bool = False
        self._last_enabled: bool = bool(self._config.enabled)

        self._last_tick_ts: Optional[float] = None
        self._last_power_ts: Optional[float] = None

        self._boiler_f: Optional[float] = None

        # ZMIANA: dwa filtry spalin
        self._flue_fast: Optional[float] = None
        self._flue_base: Optional[float] = None

        self._last_boiler_f: Optional[float] = None
        self._last_rate_ts: Optional[float] = None

        self._last_state_save_wall_ts: Optional[float] = None
        self._restored_state_meta: Optional[Dict[str, Any]] = None

        # rules + weights
        self._rules: List[_RuleSpec] = self._build_rules()
        self._rule_weights: List[float] = [1.0] * len(self._rules)

        # learning buffer
        self._learning_buffer: List[_LearningSample] = []
        self._last_learning_update_ts: Optional[float] = None
        self._last_learning_event_ts: Optional[float] = None
        self._last_reported_max_weight_delta: float = 0.0

        # auto-flue history (ZMIANA: trzymamy base)
        self._flue_hist = deque()  # deque[(ts_ctrl, flue_base)]
        self._last_flue_auto_ts: Optional[float] = None
        self._last_flue_event_ts: Optional[float] = None

        # reward shaping runtime
        self._prev_dP: float = 0.0
        self._last_ctrl_ts: Optional[float] = None

        # flue rate (ZMIANA: liczone na base)
        self._last_flue_base_for_rate: Optional[float] = None
        self._last_flue_rate_ts: Optional[float] = None
        self._flue_rate_c_per_min: float = 0.0

        # adaptation history (ZMIANA: trzymamy base)
        self._adapt_hist: deque[_AdaptSample] = deque()
        self._last_adapt_update_ts: Optional[float] = None
        self._last_adapt_event_ts: Optional[float] = None

        self._try_restore_state_from_disk()

        self._delta_universe = self._build_universe(
            self._config.delta_universe_min,
            self._config.delta_universe_max,
            self._config.delta_universe_step,
        )

    @property
    def id(self) -> str:
        return "power_work_neuro_fuzzy"

    # ----------------------------
    # Main tick
    # ----------------------------

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()

        in_work = (system_state.mode == BoilerMode.WORK)
        enabled = bool(getattr(self._config, "enabled", True))

        prev_power = self._power
        prev_in_work = self._last_in_work
        prev_enabled = self._last_enabled

        now_ctrl = float(getattr(system_state, "ts_mono", now))

        boiler_temp = getattr(sensors, "boiler_temp", None)
        flue_temp = getattr(sensors, "flue_gas_temp", None)

        if prev_in_work != in_work:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_NEURO_FUZZY_MODE_CHANGED",
                    message=f"{self.id}: {'ENTER' if in_work else 'LEAVE'} WORK",
                    data={"in_work": in_work},
                )
            )

        if prev_enabled != enabled:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_NEURO_FUZZY_ENABLED_CHANGED",
                    message=f"{self.id}: {'ENABLED' if enabled else 'DISABLED'}",
                    data={"enabled": enabled},
                )
            )

        if (not prev_in_work and in_work) or (not prev_enabled and enabled):
            self._last_power_ts = None

        # validate restore once we have sensors
        if boiler_temp is not None and flue_temp is not None and self._restored_state_meta is not None:
            if not self._validate_restored_state(
                current_boiler_temp=float(boiler_temp),
                current_flue_temp=float(flue_temp),
                now_wall=now,
                events=events,
            ):
                self._reset_state(keep_learning=True, keep_auto_flue=True, keep_adapt=True)
            self._restored_state_meta = None

        # update filters
        self._update_filters(now_ctrl=now_ctrl, boiler_temp=boiler_temp, flue_temp=flue_temp)

        # if not in work or disabled -> do not control
        if (not in_work) or (not enabled):
            self._last_in_work = in_work
            self._last_enabled = enabled

            actual_power = getattr(system_state.outputs, "power_percent", None)
            if actual_power is not None:
                try:
                    self._power = float(actual_power)
                except Exception:
                    pass

            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, flue_temp=flue_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # require filtered signals
        if self._boiler_f is None or self._flue_base is None:
            outputs.power_percent = self._power  # type: ignore[attr-defined]
            self._last_in_work = in_work
            self._last_enabled = enabled
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, flue_temp=flue_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        err = float(self._config.boiler_set_temp) - float(self._boiler_f)
        abs_err = abs(err)
        rate = self._boiler_rate_degC_per_min(now_ctrl=now_ctrl)

        flue_base = float(self._flue_base)
        flue_fast = float(self._flue_fast) if self._flue_fast is not None else flue_base

        # auto-calibration of flue thresholds (ZMIANA: na flue_base)
        self._auto_calibrate_flue(
            now_wall=now,
            now_ctrl=now_ctrl,
            flue_base=flue_base,
            abs_err=abs_err,
            rate=rate,
            events=events,
        )

        # fuzzy strengths -> delta (ZMIANA: flue_base)
        strengths, phi = self._rule_strengths_and_phi(err=err, rate=rate, flue=flue_base)
        base_delta = self._mamdani_delta_from_strengths(strengths)
        delta = base_delta * float(self._config.delta_scale)

        # slew limiter
        max_slew_per_min = max(float(self._config.max_slew_rate_percent_per_min), 0.0)
        if max_slew_per_min > 0.0 and self._last_power_ts is not None and prev_in_work and prev_enabled:
            dt = now_ctrl - self._last_power_ts
            if dt > 0:
                max_delta = max_slew_per_min * dt / 60.0
                if delta > max_delta:
                    delta = max_delta
                elif delta < -max_delta:
                    delta = -max_delta

        power = self._power + float(delta)
        power = max(float(self._config.min_power), min(power, float(self._config.max_power)))

        self._power = power
        self._last_power_ts = now_ctrl

        # dt for step (optional)
        if self._last_ctrl_ts is None:
            dt_s = None
        else:
            dt_s = now_ctrl - self._last_ctrl_ts
            if dt_s is not None and dt_s <= 0:
                dt_s = None
        self._last_ctrl_ts = now_ctrl

        # power step and jerk terms
        dP_prev = float(self._prev_dP)
        dP_now = float(self._power - prev_power)
        self._prev_dP = dP_now

        # flue rate (ZMIANA: liczone na flue_base)
        if self._last_flue_base_for_rate is None or self._last_flue_rate_ts is None:
            self._last_flue_base_for_rate = float(flue_base)
            self._last_flue_rate_ts = now_ctrl
            flue_rate = 0.0
        else:
            dtf_s = now_ctrl - self._last_flue_rate_ts
            if dtf_s <= 0:
                flue_rate = float(self._flue_rate_c_per_min)
            else:
                flue_rate = (float(flue_base) - float(self._last_flue_base_for_rate)) / (dtf_s / 60.0)
                self._last_flue_base_for_rate = float(flue_base)
                self._last_flue_rate_ts = now_ctrl
                self._flue_rate_c_per_min = float(flue_rate)

        # neuro learning update (rule weights) (ZMIANA: flue_base + base rate)
        self._learning_step(
            now_wall=now,
            now_ctrl=now_ctrl,
            abs_err=abs_err,
            phi=phi,
            power=float(power),
            dP_now=float(dP_now),
            dP_prev=float(dP_prev),
            flue_base=float(flue_base),
            flue_rate=float(flue_rate),
            events=events,
        )

        # online adaptation: delta_scale / flue_weight_near / flue_weight_band_C (ZMIANA: flue_base)
        self._adaptation_step(
            now_wall=now,
            now_ctrl=now_ctrl,
            abs_err=abs_err,
            rate=rate,
            power=float(power),
            flue_base=float(flue_base),
            flue_rate=float(flue_rate),
            events=events,
        )

        if abs(self._power - prev_power) >= 5.0:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_NEURO_FUZZY_POWER_CHANGED",
                    message=(
                        f"{self.id}: {prev_power:.1f}% → {self._power:.1f}% "
                        f"(T={float(self._boiler_f):.2f}°C, e={err:+.2f}°C, "
                        f"Tf_base={flue_base:.1f}°C, Tf_fast={flue_fast:.1f}°C, "
                        f"rate={rate:+.3f}°C/min)"
                    ),
                    data={
                        "prev_power": prev_power,
                        "power": self._power,
                        "boiler_f": float(self._boiler_f),
                        "err": err,
                        "flue_base": flue_base,
                        "flue_fast": flue_fast,
                        "flue_rate_c_per_min": float(flue_rate),
                        "rate_degC_per_min": rate,
                        "delta": float(delta),
                        "delta_scale": float(self._config.delta_scale),
                        "flue_weight_near": float(self._config.flue_weight_near),
                        "flue_weight_band_C": float(self._config.flue_weight_band_C),
                        "flue_weight": self._flue_weight(abs_err),
                        "flue_thresholds": {
                            "min": float(self._config.flue_min_C),
                            "mid": float(self._config.flue_mid_C),
                            "max": float(self._config.flue_max_C),
                        },
                    },
                )
            )

        outputs.power_percent = self._power  # type: ignore[attr-defined]
        self._last_in_work = in_work
        self._last_enabled = enabled

        self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, flue_temp=flue_temp, events=events)

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # ----------------------------
    # Online adaptation: stability knobs
    # ----------------------------

    def _adaptation_step(
        self,
        now_wall: float,
        now_ctrl: float,
        abs_err: float,
        rate: float,
        power: float,
        flue_base: float,
        flue_rate: float,
        events: List[Event],
    ) -> None:
        c = self._config
        if not bool(getattr(c, "adapt_enabled", True)):
            return

        self._adapt_hist.append(
            _AdaptSample(
                ts_ctrl=now_ctrl,
                abs_err=float(abs_err),
                rate_c_per_min=float(rate),
                power=float(power),
                flue_base=float(flue_base),
            )
        )

        win = max(120.0, float(getattr(c, "adapt_window_s", 1200.0)))
        while self._adapt_hist and (now_ctrl - self._adapt_hist[0].ts_ctrl) > win:
            self._adapt_hist.popleft()

        if len(self._adapt_hist) < 20:
            return

        interval = max(5.0, float(getattr(c, "adapt_update_interval_s", 60.0)))
        if self._last_adapt_update_ts is not None and (now_ctrl - self._last_adapt_update_ts) < interval:
            return

        if self._is_saturated(power):
            return

        abs_err_mean = sum(s.abs_err for s in self._adapt_hist) / len(self._adapt_hist)
        rate_abs_mean = sum(abs(s.rate_c_per_min) for s in self._adapt_hist) / len(self._adapt_hist)

        pw = [s.power for s in self._adapt_hist]
        tf = [s.flue_base for s in self._adapt_hist]

        p_mean = sum(pw) / len(pw)
        f_mean = sum(tf) / len(tf)

        p_var = sum((x - p_mean) ** 2 for x in pw) / max(1, (len(pw) - 1))
        f_var = sum((x - f_mean) ** 2 for x in tf) / max(1, (len(tf) - 1))

        p_std = math.sqrt(max(0.0, p_var))
        f_std = math.sqrt(max(0.0, f_var))

        abs_dps: List[float] = []
        flue_rates: List[float] = []
        prev: Optional[_AdaptSample] = None
        for s in self._adapt_hist:
            if prev is not None:
                abs_dps.append(abs(float(s.power - prev.power)))
                dt_s = float(s.ts_ctrl - prev.ts_ctrl)
                if dt_s > 0.0:
                    r = (float(s.flue_base - prev.flue_base)) / (dt_s / 60.0)
                    flue_rates.append(float(r))
            prev = s

        absdp_mean = (sum(abs_dps) / len(abs_dps)) if abs_dps else 0.0
        flue_rate_max_pos = max([0.0] + [r for r in flue_rates if r > 0.0])

        temp_ok = (abs_err_mean <= float(c.adapt_err_ok_C)) and (rate_abs_mean <= float(c.adapt_rate_ok_C_per_min))
        temp_bad = abs_err_mean >= float(c.adapt_err_bad_C)

        power_jitter = (p_std >= float(c.adapt_power_std_high)) or (absdp_mean >= float(c.adapt_power_absdp_mean_high))
        flue_jitter = (f_std >= float(c.adapt_flue_std_high)) or (flue_rate_max_pos >= float(c.adapt_flue_rate_max_high))

        ds = float(c.delta_scale)
        fwn = float(c.flue_weight_near)
        band = float(c.flue_weight_band_C)

        ds0, fwn0, band0 = ds, fwn, band

        # 1) delta_scale: DOWN if temp OK and power jitter
        if temp_ok and power_jitter:
            ds -= float(c.adapt_delta_scale_step)

        # recovery (temp bad) -> allow small UP
        if temp_bad and not power_jitter:
            ds += 0.5 * float(c.adapt_delta_scale_step)

        # 2) flue_weight_near: DOWN if temp OK and flue jitter
        if temp_ok and flue_jitter:
            fwn -= float(c.adapt_flue_weight_near_step)

        # 3) flue_weight_band_C: UP if temp OK and jitter
        if temp_ok and (power_jitter or flue_jitter):
            band += float(c.adapt_flue_weight_band_step_C)

        ds = max(float(c.adapt_delta_scale_min), min(ds, float(c.adapt_delta_scale_max)))
        fwn = max(float(c.adapt_flue_weight_near_min), min(fwn, float(c.adapt_flue_weight_near_max)))
        band = max(float(c.adapt_flue_weight_band_min_C), min(band, float(c.adapt_flue_weight_band_max_C)))

        changed = (abs(ds - ds0) > 1e-9) or (abs(fwn - fwn0) > 1e-9) or (abs(band - band0) > 1e-9)
        if changed:
            c.delta_scale = ds
            c.flue_weight_near = fwn
            c.flue_weight_band_C = band

        self._last_adapt_update_ts = now_ctrl

        ev_int = max(0.0, float(getattr(c, "adapt_event_min_interval_s", 300.0)))
        if ev_int > 0.0:
            if self._last_adapt_event_ts is None or (now_ctrl - self._last_adapt_event_ts) >= ev_int:
                if changed:
                    events.append(
                        Event(
                            ts=now_wall,
                            source=self.id,
                            level=EventLevel.INFO,
                            type="WORK_NEURO_FUZZY_ADAPT_PARAMS",
                            message=(
                                f"{self.id}: adapt params "
                                f"(delta_scale {ds0:.3f}->{ds:.3f}, "
                                f"flue_weight_near {fwn0:.2f}->{fwn:.2f}, "
                                f"band {band0:.1f}->{band:.1f}°C)"
                            ),
                            data={
                                "window_s": win,
                                "stats": {
                                    "abs_err_mean": abs_err_mean,
                                    "rate_abs_mean": rate_abs_mean,
                                    "power_std": p_std,
                                    "power_absdp_mean": absdp_mean,
                                    "flue_std_base": f_std,
                                    "flue_rate_max_pos_base": flue_rate_max_pos,
                                },
                                "flags": {
                                    "temp_ok": temp_ok,
                                    "temp_bad": temp_bad,
                                    "power_jitter": power_jitter,
                                    "flue_jitter": flue_jitter,
                                },
                                "params": {
                                    "delta_scale": ds,
                                    "flue_weight_near": fwn,
                                    "flue_weight_band_C": band,
                                    "flue_weight_far": float(c.flue_weight_far),
                                },
                            },
                        )
                    )
                    self._last_adapt_event_ts = now_ctrl

    # ----------------------------
    # Auto-calibration: flue thresholds (self-tuning)
    # ----------------------------

    def _auto_calibrate_flue(
        self,
        now_wall: float,
        now_ctrl: float,
        flue_base: float,
        abs_err: float,
        rate: float,
        events: List[Event],
    ) -> None:
        c = self._config
        if not bool(getattr(c, "auto_flue_enabled", True)):
            return

        if abs_err > float(c.auto_flue_stable_abs_err_C):
            return
        if abs(rate) > float(c.auto_flue_stable_rate_C_per_min):
            return

        # ZMIANA: history na flue_base
        self._flue_hist.append((now_ctrl, float(flue_base)))

        window_s = max(60.0, float(c.auto_flue_window_s))
        while self._flue_hist and (now_ctrl - self._flue_hist[0][0]) > window_s:
            self._flue_hist.popleft()

        interval = max(5.0, float(c.auto_flue_update_interval_s))
        if self._last_flue_auto_ts is not None and (now_ctrl - self._last_flue_auto_ts) < interval:
            return

        if len(self._flue_hist) < 30:
            return

        vals = sorted(v for _, v in self._flue_hist)

        def q(p: float) -> float:
            p = min(max(p, 0.0), 1.0)
            idx = int(round(p * (len(vals) - 1)))
            return float(vals[idx])

        new_min = q(float(c.auto_flue_q_low))
        new_mid = q(float(c.auto_flue_q_mid))
        new_max = q(float(c.auto_flue_q_high))

        if not (new_min < new_mid < new_max):
            return

        if (new_max - new_min) < float(c.auto_flue_min_span_C):
            return

        bmin = float(c.auto_flue_min_bound_C)
        bmax = float(c.auto_flue_max_bound_C)

        new_min = max(bmin, min(new_min, bmax))
        new_mid = max(bmin, min(new_mid, bmax))
        new_max = max(bmin, min(new_max, bmax))

        alpha = min(max(float(c.auto_flue_ema_alpha), 0.001), 0.2)

        old = (float(c.flue_min_C), float(c.flue_mid_C), float(c.flue_max_C))

        c.flue_min_C = (1 - alpha) * float(c.flue_min_C) + alpha * new_min
        c.flue_mid_C = (1 - alpha) * float(c.flue_mid_C) + alpha * new_mid
        c.flue_max_C = (1 - alpha) * float(c.flue_max_C) + alpha * new_max

        if not (c.flue_min_C < c.flue_mid_C < c.flue_max_C):
            c.flue_min_C, c.flue_mid_C, c.flue_max_C = old
            return

        self._last_flue_auto_ts = now_ctrl

        ev_int = max(0.0, float(c.auto_flue_event_min_interval_s))
        if ev_int > 0.0:
            if self._last_flue_event_ts is None or (now_ctrl - self._last_flue_event_ts) >= ev_int:
                dmin = float(c.flue_min_C) - old[0]
                dmid = float(c.flue_mid_C) - old[1]
                dmax = float(c.flue_max_C) - old[2]
                events.append(
                    Event(
                        ts=now_wall,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="WORK_NEURO_FUZZY_FLUE_AUTOTUNE",
                        message=(
                            f"{self.id}: auto-tune flue thresholds "
                            f"(min/mid/max={c.flue_min_C:.1f}/{c.flue_mid_C:.1f}/{c.flue_max_C:.1f}°C)"
                        ),
                        data={
                            "old": {"min": old[0], "mid": old[1], "max": old[2]},
                            "new": {"min": float(c.flue_min_C), "mid": float(c.flue_mid_C), "max": float(c.flue_max_C)},
                            "delta": {"min": dmin, "mid": dmid, "max": dmax},
                            "window_samples": len(vals),
                            "signal": "flue_base",
                        },
                    )
                )
                self._last_flue_event_ts = now_ctrl

    # ----------------------------
    # Neuro learning (weights)
    # ----------------------------

    def _learning_step(
        self,
        now_wall: float,
        now_ctrl: float,
        abs_err: float,
        phi: List[float],
        power: float,
        dP_now: float,
        dP_prev: float,
        flue_base: float,
        flue_rate: float,
        events: List[Event],
    ) -> None:
        c = self._config
        if not bool(getattr(c, "neuro_enabled", True)):
            return
        if len(phi) != len(self._rules):
            return

        if abs_err < float(c.learning_deadzone_C):
            return

        if bool(c.learning_freeze_on_saturation) and self._is_saturated(power):
            return

        self._learning_buffer.append(_LearningSample(ts_ctrl=now_ctrl, abs_err=float(abs_err), phi=list(phi)))
        max_buf = int(max(50, getattr(c, "learning_buffer_max", 600)))
        if len(self._learning_buffer) > max_buf:
            self._learning_buffer = self._learning_buffer[-max_buf:]

        delay = max(0.0, float(c.learning_delay_s))
        if delay <= 0.0:
            return

        min_upd = max(0.0, float(c.learning_min_update_interval_s))
        if self._last_learning_update_ts is not None and (now_ctrl - self._last_learning_update_ts) < min_upd:
            return

        processed = 0
        new_buf: List[_LearningSample] = []
        for s in self._learning_buffer:
            if (now_ctrl - s.ts_ctrl) >= delay and processed < 3:
                self._apply_learning_update(
                    sample=s,
                    abs_err_now=abs_err,
                    dP_now=dP_now,
                    dP_prev=dP_prev,
                    flue_base=flue_base,
                    flue_rate=flue_rate,
                )
                processed += 1
            else:
                new_buf.append(s)
        self._learning_buffer = new_buf

        if processed > 0:
            self._last_learning_update_ts = now_ctrl
            self._maybe_emit_learning_event(now_wall=now_wall, now_ctrl=now_ctrl, events=events)

    def _apply_learning_update(
        self,
        sample: _LearningSample,
        abs_err_now: float,
        dP_now: float,
        dP_prev: float,
        flue_base: float,
        flue_rate: float,
    ) -> None:
        c = self._config

        old = max(float(sample.abs_err), 0.5)
        new = float(abs_err_now)
        r_temp = (old - new) / old

        k_dp = float(getattr(c, "reward_k_dp", 0.03))
        k_ddp = float(getattr(c, "reward_k_ddp", 0.08))
        k_tf = float(getattr(c, "reward_k_tf", 0.004))
        k_dtf = float(getattr(c, "reward_k_dtf", 0.02))
        temp_gain = float(getattr(c, "reward_temp_gain", 1.0))

        flue_ref = float(getattr(c, "flue_mid_C", 0.0))

        r_smooth = -k_dp * abs(float(dP_now)) - k_ddp * abs(float(dP_now) - float(dP_prev))
        r_flue = -k_tf * max(0.0, float(flue_base) - flue_ref) - k_dtf * max(0.0, float(flue_rate))

        reward = temp_gain * r_temp + r_smooth + r_flue

        clip = float(getattr(c, "reward_clip", 2.0))
        if reward > clip:
            reward = clip
        elif reward < -clip:
            reward = -clip

        lr = float(c.learning_rate)
        reg = float(c.learning_reg)
        wmin = float(c.rule_weight_min)
        wmax = float(c.rule_weight_max)

        for i in range(len(self._rule_weights)):
            wi = float(self._rule_weights[i])
            dwi = lr * (reward * float(sample.phi[i]) - reg * (wi - 1.0))
            wi2 = wi + dwi
            if wi2 < wmin:
                wi2 = wmin
            elif wi2 > wmax:
                wi2 = wmax
            self._rule_weights[i] = wi2

    def _maybe_emit_learning_event(self, now_wall: float, now_ctrl: float, events: List[Event]) -> None:
        c = self._config
        interval = float(c.learning_event_min_interval_s)
        if interval <= 0:
            return
        if self._last_learning_event_ts is not None and (now_ctrl - self._last_learning_event_ts) < interval:
            return

        max_dev = 0.0
        for w in self._rule_weights:
            d = abs(float(w) - 1.0)
            if d > max_dev:
                max_dev = d

        thr = float(c.learning_event_delta_threshold)
        if max_dev >= thr and abs(max_dev - self._last_reported_max_weight_delta) >= 0.05:
            diffs: List[Tuple[float, int]] = []
            for i, w in enumerate(self._rule_weights):
                diffs.append((abs(float(w) - 1.0), i))
            diffs.sort(reverse=True)
            top = diffs[:6]
            top_rules = [
                {"rule": self._rules[i].name, "out": self._rules[i].out_label, "weight": float(self._rule_weights[i])}
                for _, i in top
            ]

            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_NEURO_FUZZY_LEARNING_UPDATE",
                    message=f"{self.id}: adaptacja wag reguł (max |w-1|={max_dev:.2f})",
                    data={
                        "max_abs_weight_delta": max_dev,
                        "top_rules": top_rules,
                    },
                )
            )
            self._last_learning_event_ts = now_ctrl
            self._last_reported_max_weight_delta = max_dev

    def _is_saturated(self, power: float) -> bool:
        c = self._config
        eps = 1e-6
        if power <= float(c.min_power) + eps:
            return True
        if power >= float(c.max_power) - eps:
            return True
        return False

    # ----------------------------
    # Rules
    # ----------------------------

    def _build_rules(self) -> List[_RuleSpec]:
        return [
            _RuleSpec("E_PB -> UB", "UB"),
            _RuleSpec("E_PS -> UM", "UM"),
            _RuleSpec("E_ZE -> Z", "Z"),
            _RuleSpec("E_NS -> DS", "DS"),
            _RuleSpec("E_NB -> DB", "DB"),
            _RuleSpec("E_PS & R_RISE -> DS", "DS"),
            _RuleSpec("E_ZE & R_RISE -> DM", "DM"),
            _RuleSpec("E_NS & R_RISE -> DB", "DB"),
            _RuleSpec("E_ZE & R_FALL -> US", "US"),
            _RuleSpec("E_PS & R_FALL -> UM", "UM"),
            _RuleSpec("E_NS & R_FALL -> Z", "Z"),
            _RuleSpec("F_VHIGH -> DB", "DB"),
            _RuleSpec("F_HIGH & (E_ZE or E_NS) -> DB", "DB"),
            _RuleSpec("F_HIGH & E_PS -> DS", "DS"),
            _RuleSpec("F_LOW & E_PB -> UB", "UB"),
            _RuleSpec("F_LOW & E_PS -> UM", "UM"),
            _RuleSpec("F_MID & E_PB -> UM", "UM"),
            _RuleSpec("F_MID & E_PS -> US", "US"),
        ]

    def _rule_strengths_and_phi(self, err: float, rate: float, flue: float) -> Tuple[List[Tuple[float, str]], List[float]]:
        mu_e = self._fuzzify_error(err)
        mu_r = self._fuzzify_rate(rate)
        mu_f = self._fuzzify_flue(flue)

        w_flue = self._flue_weight(abs(err))

        base_strengths: List[float] = [
            mu_e["PB"],
            mu_e["PS"],
            mu_e["ZE"],
            mu_e["NS"],
            mu_e["NB"],
            min(mu_e["PS"], mu_r["RISE"]),
            min(mu_e["ZE"], mu_r["RISE"]),
            min(mu_e["NS"], mu_r["RISE"]),
            min(mu_e["ZE"], mu_r["FALL"]),
            min(mu_e["PS"], mu_r["FALL"]),
            min(mu_e["NS"], mu_r["FALL"]),
            w_flue * mu_f["VHIGH"],
            w_flue * min(mu_f["HIGH"], max(mu_e["ZE"], mu_e["NS"])),
            w_flue * min(mu_f["HIGH"], mu_e["PS"]),
            w_flue * min(mu_f["LOW"], mu_e["PB"]),
            w_flue * min(mu_f["LOW"], mu_e["PS"]),
            w_flue * min(mu_f["MID"], mu_e["PB"]),
            w_flue * min(mu_f["MID"], mu_e["PS"]),
        ]

        weighted: List[Tuple[float, str]] = []
        for i, s in enumerate(base_strengths):
            w = float(self._rule_weights[i]) if i < len(self._rule_weights) else 1.0
            sw = float(s) * w
            if sw < 0.0:
                sw = 0.0
            weighted.append((sw, self._rules[i].out_label))

        ssum = sum(max(0.0, float(s)) for s in base_strengths)
        if ssum <= 1e-12:
            phi = [0.0] * len(base_strengths)
        else:
            phi = [max(0.0, float(s)) / ssum for s in base_strengths]

        return weighted, phi

    # ----------------------------
    # Mamdani defuzzification (centroid)
    # ----------------------------

    def _mamdani_delta_from_strengths(self, strengths_and_labels: List[Tuple[float, str]]) -> float:
        agg: List[float] = [0.0] * len(self._delta_universe)

        for strength, out_label in strengths_and_labels:
            if strength <= 0.0:
                continue
            for i, x in enumerate(self._delta_universe):
                mu_out = self._out_membership(out_label, x)
                v = strength if strength < mu_out else mu_out
                if v > agg[i]:
                    agg[i] = v

        num = 0.0
        den = 0.0
        for x, mu in zip(self._delta_universe, agg):
            num += x * mu
            den += mu

        if den <= 1e-9:
            return 0.0
        return num / den

    def _flue_weight(self, abs_err: float) -> float:
        c = self._config
        band = float(c.flue_weight_band_C)
        if band <= 0.0:
            return max(0.0, float(c.flue_weight_near))

        x = abs_err / band
        x = 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)
        s = x * x * (3.0 - 2.0 * x)

        near = float(c.flue_weight_near)
        far = float(c.flue_weight_far)

        w = near * (1.0 - s) + far * s
        w = 0.0 if w < 0.0 else (5.0 if w > 5.0 else w)
        return w

    def _fuzzify_error(self, e: float) -> Dict[str, float]:
        c = self._config
        return {
            "NB": self._trapmf(e, c.e_nb_a, c.e_nb_b, c.e_nb_c, c.e_nb_d),
            "NS": self._trimf(e, c.e_ns_a, c.e_ns_b, c.e_ns_c),
            "ZE": self._trimf(e, c.e_ze_a, c.e_ze_b, c.e_ze_c),
            "PS": self._trimf(e, c.e_ps_a, c.e_ps_b, c.e_ps_c),
            "PB": self._trapmf(e, c.e_pb_a, c.e_pb_b, c.e_pb_c, c.e_pb_d),
        }

    def _fuzzify_rate(self, r: float) -> Dict[str, float]:
        c = self._config
        return {
            "FALL": self._trapmf(r, c.r_fall_a, c.r_fall_b, c.r_fall_c, c.r_fall_d),
            "STABLE": self._trimf(r, c.r_stable_a, c.r_stable_b, c.r_stable_c),
            "RISE": self._trapmf(r, c.r_rise_a, c.r_rise_b, c.r_rise_c, c.r_rise_d),
        }

    def _fuzzify_flue(self, f: float) -> Dict[str, float]:
        c = self._config

        fmin = float(c.flue_min_C)
        fmid = float(c.flue_mid_C)
        fmax = float(c.flue_max_C)

        if fmax <= fmin:
            fmax = fmin + 1.0
        if not (fmin < fmid < fmax):
            fmid = 0.5 * (fmin + fmax)

        span = fmax - fmin
        ov = max(0.05, min(float(c.flue_overlap_ratio), 0.45))
        w = span * ov

        low_a = fmin - w
        low_b = fmin - w
        low_c = fmin
        low_d = fmid - w

        mid_a = fmin + w
        mid_b = fmid
        mid_c = fmax - w

        high_a = fmid + w
        high_b = fmax
        high_c = fmax + w

        vh_start = fmax + float(c.flue_vhigh_margin_C)
        vh_a = vh_start
        vh_b = vh_start + w
        vh_c = vh_b + 200.0
        vh_d = vh_c

        return {
            "LOW": self._trapmf(f, low_a, low_b, low_c, low_d),
            "MID": self._trimf(f, mid_a, mid_b, mid_c),
            "HIGH": self._trimf(f, high_a, high_b, high_c),
            "VHIGH": self._trapmf(f, vh_a, vh_b, vh_c, vh_d),
        }

    def _out_membership(self, label: str, x: float) -> float:
        if label == "DB":
            return self._trapmf(x, -6.0, -6.0, -4.8, -3.2)
        if label == "DM":
            return self._trimf(x, -5.0, -3.2, -1.6)
        if label == "DS":
            return self._trimf(x, -2.5, -1.2, 0.0)
        if label == "Z":
            return self._trimf(x, -0.6, 0.0, 0.6)
        if label == "US":
            return self._trimf(x, 0.0, 1.2, 2.5)
        if label == "UM":
            return self._trimf(x, 1.6, 3.2, 5.0)
        if label == "UB":
            return self._trapmf(x, 3.2, 4.8, 6.0, 6.0)
        return 0.0

    # ----------------------------
    # Filters / rate / primitives
    # ----------------------------

    def _effective_flue_fast_tau_s(self) -> float:
        """
        Jeśli flue_fast_tau_s nie ustawione w configu:
        - domyślnie bierzemy ~1/4 flue_tau_s (ale nie mniej niż 5s).
        """
        c = self._config
        if c.flue_fast_tau_s is not None:
            try:
                v = float(c.flue_fast_tau_s)
                if v > 0.0:
                    return v
            except Exception:
                pass
        base = float(getattr(c, "flue_tau_s", 60.0))
        return max(5.0, base / 4.0)

    def _effective_flue_base_tau_s(self) -> float:
        # Kompatybilność: flue_tau_s traktujemy jako BASE.
        return float(getattr(self._config, "flue_tau_s", 60.0))

    def _update_filters(self, now_ctrl: float, boiler_temp: Any, flue_temp: Any) -> None:
        if self._last_tick_ts is None:
            dt = None
        else:
            dt = now_ctrl - self._last_tick_ts
            if dt <= 0:
                dt = None
        self._last_tick_ts = now_ctrl

        if boiler_temp is not None:
            try:
                bt = float(boiler_temp)
                self._boiler_f = self._ema_update(self._boiler_f, bt, dt, float(self._config.boiler_tau_s))
            except Exception:
                pass

        if flue_temp is not None:
            try:
                ft = float(flue_temp)
                tau_fast = self._effective_flue_fast_tau_s()
                tau_base = self._effective_flue_base_tau_s()

                self._flue_fast = self._ema_update(self._flue_fast, ft, dt, tau_fast)
                self._flue_base = self._ema_update(self._flue_base, ft, dt, tau_base)
            except Exception:
                pass

    def _boiler_rate_degC_per_min(self, now_ctrl: float) -> float:
        if self._boiler_f is None:
            return 0.0

        if self._last_boiler_f is None or self._last_rate_ts is None:
            self._last_boiler_f = float(self._boiler_f)
            self._last_rate_ts = now_ctrl
            return 0.0

        dt_s = now_ctrl - self._last_rate_ts
        if dt_s <= 0.0:
            self._last_boiler_f = float(self._boiler_f)
            self._last_rate_ts = now_ctrl
            return 0.0

        dT = float(self._boiler_f) - float(self._last_boiler_f)
        self._last_boiler_f = float(self._boiler_f)
        self._last_rate_ts = now_ctrl

        return dT / (dt_s / 60.0)

    def _ema_update(self, prev: Optional[float], x: float, dt: Optional[float], tau_s: float) -> float:
        if prev is None or dt is None or dt <= 0.0 or tau_s <= 0.0:
            return float(x)
        alpha = 1.0 - math.exp(-dt / tau_s)
        return float(prev + alpha * (x - prev))

    def _reset_state(self, keep_learning: bool = True, keep_auto_flue: bool = True, keep_adapt: bool = True) -> None:
        self._power = 0.0
        self._last_tick_ts = None
        self._last_power_ts = None
        self._boiler_f = None

        # ZMIANA: reset obu filtrów spalin
        self._flue_fast = None
        self._flue_base = None

        self._last_boiler_f = None
        self._last_rate_ts = None

        self._prev_dP = 0.0
        self._last_ctrl_ts = None

        self._last_flue_base_for_rate = None
        self._last_flue_rate_ts = None
        self._flue_rate_c_per_min = 0.0

        if not keep_learning:
            self._learning_buffer = []
            self._rule_weights = [1.0] * len(self._rules)
            self._last_learning_update_ts = None
            self._last_learning_event_ts = None
            self._last_reported_max_weight_delta = 0.0

        if not keep_auto_flue:
            self._flue_hist.clear()
            self._last_flue_auto_ts = None
            self._last_flue_event_ts = None

        if not keep_adapt:
            self._adapt_hist.clear()
            self._last_adapt_update_ts = None
            self._last_adapt_event_ts = None

    def _trimf(self, x: float, a: float, b: float, c: float) -> float:
        if x <= a or x >= c:
            return 0.0
        if x == b:
            return 1.0
        if x < b:
            return (x - a) / (b - a) if b != a else 0.0
        return (c - x) / (c - b) if c != b else 0.0

    def _trapmf(self, x: float, a: float, b: float, c: float, d: float) -> float:
        if x <= a or x >= d:
            return 0.0
        if b <= x <= c:
            return 1.0
        if a < x < b:
            return (x - a) / (b - a) if b != a else 0.0
        return (d - x) / (d - c) if d != c else 0.0

    def _build_universe(self, umin: float, umax: float, step: float) -> List[float]:
        if step <= 0:
            step = 0.1
        n = int(round((umax - umin) / step)) + 1
        if n < 3:
            n = 3
        return [umin + i * step for i in range(n)]

    # ----------------------------
    # Persist
    # ----------------------------

    def _try_restore_state_from_disk(self) -> None:
        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        if not self._state_path.exists():
            return

        try:
            with self._state_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return

        saved_wall_ts = data.get("saved_wall_ts")
        if not isinstance(saved_wall_ts, (int, float)):
            return

        max_age = float(self._config.state_max_age_s)
        if max_age > 0:
            age = time.time() - float(saved_wall_ts)
            if age < 0:
                return
            if age > max_age:
                return

        power = data.get("power")
        boiler_f = data.get("boiler_f")

        # kompatybilność:
        # - nowe pola: flue_fast, flue_base
        # - stare pole: flue_f (traktujemy jako flue_base)
        flue_fast = data.get("flue_fast")
        flue_base = data.get("flue_base")
        flue_f_old = data.get("flue_f")

        if isinstance(power, (int, float)):
            self._power = float(power)

        if isinstance(boiler_f, (int, float)):
            self._boiler_f = float(boiler_f)
            self._last_boiler_f = float(boiler_f)

        # restore flue
        if isinstance(flue_base, (int, float)):
            self._flue_base = float(flue_base)
        elif isinstance(flue_f_old, (int, float)):
            self._flue_base = float(flue_f_old)

        if isinstance(flue_fast, (int, float)):
            self._flue_fast = float(flue_fast)
        else:
            # jeśli brak fast, ustaw fast = base (żeby logi miały sens)
            if self._flue_base is not None:
                self._flue_fast = float(self._flue_base)

        # restore weights if present
        w = data.get("rule_weights")
        if isinstance(w, list) and len(w) == len(self._rule_weights):
            ok = all(isinstance(x, (int, float)) for x in w)
            if ok:
                self._rule_weights = [float(x) for x in w]

        # restore flue thresholds if present
        fl = data.get("flue_thresholds") or {}
        if isinstance(fl, dict):
            try:
                fmin = float(fl.get("min", self._config.flue_min_C))
                fmid = float(fl.get("mid", self._config.flue_mid_C))
                fmax = float(fl.get("max", self._config.flue_max_C))
                if fmin < fmid < fmax:
                    self._config.flue_min_C = fmin
                    self._config.flue_mid_C = fmid
                    self._config.flue_max_C = fmax
            except Exception:
                pass

        # restore adapted params if present
        ap = data.get("adapted_params") or {}
        if isinstance(ap, dict):
            try:
                ds = float(ap.get("delta_scale", self._config.delta_scale))
                fwn = float(ap.get("flue_weight_near", self._config.flue_weight_near))
                band = float(ap.get("flue_weight_band_C", self._config.flue_weight_band_C))

                ds = max(float(self._config.adapt_delta_scale_min), min(ds, float(self._config.adapt_delta_scale_max)))
                fwn = max(float(self._config.adapt_flue_weight_near_min), min(fwn, float(self._config.adapt_flue_weight_near_max)))
                band = max(float(self._config.adapt_flue_weight_band_min_C), min(band, float(self._config.adapt_flue_weight_band_max_C)))

                self._config.delta_scale = ds
                self._config.flue_weight_near = fwn
                self._config.flue_weight_band_C = band
            except Exception:
                pass

        self._last_tick_ts = None
        self._last_power_ts = None
        self._last_rate_ts = None
        self._last_flue_rate_ts = None
        self._last_flue_base_for_rate = None

        self._restored_state_meta = {
            "saved_wall_ts": float(saved_wall_ts),
            "saved_boiler_temp": data.get("boiler_temp"),
            "saved_flue_gas_temp": data.get("flue_gas_temp"),
        }

    def _validate_restored_state(
        self,
        current_boiler_temp: float,
        current_flue_temp: float,
        now_wall: float,
        events: List[Event],
    ) -> bool:
        meta = self._restored_state_meta or {}
        saved_boiler = meta.get("saved_boiler_temp")
        saved_flue = meta.get("saved_flue_gas_temp")

        ok = True

        if isinstance(saved_boiler, (int, float)):
            delta_b = abs(float(current_boiler_temp) - float(saved_boiler))
            if delta_b > float(self._config.state_max_boiler_temp_delta_C):
                ok = False

        if isinstance(saved_flue, (int, float)):
            delta_f = abs(float(current_flue_temp) - float(saved_flue))
            if delta_f > float(self._config.state_max_flue_temp_delta_C):
                ok = False

        if ok:
            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_NEURO_FUZZY_STATE_RESTORED",
                    message=f"{self.id}: przywrócono stan z dysku",
                    data={
                        "saved_boiler_temp": saved_boiler,
                        "current_boiler_temp": current_boiler_temp,
                        "saved_flue_gas_temp": saved_flue,
                        "current_flue_gas_temp": current_flue_temp,
                    },
                )
            )
            return True

        events.append(
            Event(
                ts=now_wall,
                source=self.id,
                level=EventLevel.INFO,
                type="WORK_NEURO_FUZZY_STATE_RESTORE_SKIPPED",
                message=f"{self.id}: pominięto restore stanu (zbyt duża różnica temp.)",
                data={
                    "saved_boiler_temp": saved_boiler,
                    "current_boiler_temp": current_boiler_temp,
                    "saved_flue_gas_temp": saved_flue,
                    "current_flue_gas_temp": current_flue_temp,
                },
            )
        )
        return False

    def _maybe_persist_state(
        self,
        now_wall: float,
        boiler_temp: Optional[float],
        flue_temp: Optional[float],
        events: List[Event],
    ) -> None:
        interval = float(self._config.state_save_interval_s)
        if interval <= 0:
            return

        if self._last_state_save_wall_ts is not None and (now_wall - self._last_state_save_wall_ts) < interval:
            return

        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)

            data = {
                "saved_wall_ts": float(now_wall),
                "boiler_temp": float(boiler_temp) if boiler_temp is not None else None,
                "flue_gas_temp": float(flue_temp) if flue_temp is not None else None,
                "power": float(self._power),
                "boiler_f": float(self._boiler_f) if self._boiler_f is not None else None,

                # ZMIANA: zapisujemy oba filtry
                "flue_fast": float(self._flue_fast) if self._flue_fast is not None else None,
                "flue_base": float(self._flue_base) if self._flue_base is not None else None,

                "rule_weights": [float(w) for w in self._rule_weights],
                "flue_thresholds": {
                    "min": float(self._config.flue_min_C),
                    "mid": float(self._config.flue_mid_C),
                    "max": float(self._config.flue_max_C),
                },
                "adapted_params": {
                    "delta_scale": float(self._config.delta_scale),
                    "flue_weight_near": float(self._config.flue_weight_near),
                    "flue_weight_band_C": float(self._config.flue_weight_band_C),
                },
            }

            tmp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

            tmp_path.replace(self._state_path)
            self._last_state_save_wall_ts = now_wall

        except Exception as exc:
            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.WARNING,
                    type="WORK_NEURO_FUZZY_STATE_SAVE_ERROR",
                    message=f"{self.id}: błąd zapisu stanu: {exc}",
                    data={"error": str(exc)},
                )
            )

    # ----------------------------
    # Config IO
    # ----------------------------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        # bools
        for k in (
            "enabled",
            "neuro_enabled",
            "learning_freeze_on_saturation",
            "auto_flue_enabled",
            "adapt_enabled",
        ):
            if k in values:
                setattr(self._config, k, bool(values[k]))

        # numeric + optional numeric
        numeric_fields = (
            "boiler_set_temp",
            "min_power",
            "max_power",
            "max_slew_rate_percent_per_min",
            "boiler_tau_s",

            # flue filters
            "flue_tau_s",         # BASE
            "flue_fast_tau_s",    # FAST (optional)

            "flue_weight_band_C",
            "flue_weight_near",
            "flue_weight_far",
            "delta_universe_min",
            "delta_universe_max",
            "delta_universe_step",
            "delta_scale",
            "flue_min_C",
            "flue_mid_C",
            "flue_max_C",
            "flue_overlap_ratio",
            "flue_vhigh_margin_C",
            "state_save_interval_s",
            "state_max_age_s",
            "state_max_boiler_temp_delta_C",
            "state_max_flue_temp_delta_C",

            "e_nb_a", "e_nb_b", "e_nb_c", "e_nb_d",
            "e_ns_a", "e_ns_b", "e_ns_c",
            "e_ze_a", "e_ze_b", "e_ze_c",
            "e_ps_a", "e_ps_b", "e_ps_c",
            "e_pb_a", "e_pb_b", "e_pb_c", "e_pb_d",
            "r_fall_a", "r_fall_b", "r_fall_c", "r_fall_d",
            "r_stable_a", "r_stable_b", "r_stable_c",
            "r_rise_a", "r_rise_b", "r_rise_c", "r_rise_d",

            "learning_delay_s",
            "learning_min_update_interval_s",
            "learning_deadzone_C",
            "learning_rate",
            "learning_reg",
            "rule_weight_min",
            "rule_weight_max",
            "learning_event_delta_threshold",
            "learning_event_min_interval_s",
            "learning_buffer_max",

            "reward_temp_gain",
            "reward_k_dp",
            "reward_k_ddp",
            "reward_k_tf",
            "reward_k_dtf",
            "reward_clip",

            "auto_flue_window_s",
            "auto_flue_update_interval_s",
            "auto_flue_stable_abs_err_C",
            "auto_flue_stable_rate_C_per_min",
            "auto_flue_min_bound_C",
            "auto_flue_max_bound_C",
            "auto_flue_ema_alpha",
            "auto_flue_q_low",
            "auto_flue_q_mid",
            "auto_flue_q_high",
            "auto_flue_min_span_C",
            "auto_flue_event_min_interval_s",

            "adapt_window_s",
            "adapt_update_interval_s",
            "adapt_event_min_interval_s",
            "adapt_err_ok_C",
            "adapt_err_bad_C",
            "adapt_rate_ok_C_per_min",
            "adapt_power_std_high",
            "adapt_power_absdp_mean_high",
            "adapt_flue_std_high",
            "adapt_flue_rate_max_high",
            "adapt_delta_scale_step",
            "adapt_flue_weight_near_step",
            "adapt_flue_weight_band_step_C",
            "adapt_delta_scale_min",
            "adapt_delta_scale_max",
            "adapt_flue_weight_near_min",
            "adapt_flue_weight_near_max",
            "adapt_flue_weight_band_min_C",
            "adapt_flue_weight_band_max_C",
        )

        for field_name in numeric_fields:
            if field_name in values:
                try:
                    if field_name == "learning_buffer_max":
                        setattr(self._config, field_name, int(values[field_name]))
                    elif field_name == "flue_fast_tau_s":
                        # allow null/None to mean "auto"
                        v = values[field_name]
                        if v is None:
                            setattr(self._config, field_name, None)
                        else:
                            setattr(self._config, field_name, float(v))
                    else:
                        setattr(self._config, field_name, float(values[field_name]))
                except Exception:
                    pass

        if "state_dir" in values:
            self._config.state_dir = str(values["state_dir"])
            self._state_dir = (self._persist_root / self._config.state_dir).resolve()
            self._state_path = self._state_dir / self._config.state_file

        if "state_file" in values:
            self._config.state_file = str(values["state_file"])
            self._state_path = self._state_dir / self._config.state_file

        self._delta_universe = self._build_universe(
            self._config.delta_universe_min,
            self._config.delta_universe_max,
            self._config.delta_universe_step,
        )

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()
        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file
        self._delta_universe = self._build_universe(
            self._config.delta_universe_min,
            self._config.delta_universe_max,
            self._config.delta_universe_step,
        )

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for k in (
            "enabled",
            "neuro_enabled",
            "learning_freeze_on_saturation",
            "auto_flue_enabled",
            "adapt_enabled",
        ):
            if k in data:
                setattr(self._config, k, bool(data[k]))

        for k, v in data.items():
            if k in (
                "state_dir",
                "state_file",
                "enabled",
                "neuro_enabled",
                "learning_freeze_on_saturation",
                "auto_flue_enabled",
                "adapt_enabled",
            ):
                continue
            try:
                if hasattr(self._config, k):
                    if k == "learning_buffer_max":
                        setattr(self._config, k, int(v))
                    elif k == "flue_fast_tau_s":
                        # allow null
                        if v is None:
                            setattr(self._config, k, None)
                        else:
                            setattr(self._config, k, float(v))
                    else:
                        setattr(self._config, k, float(v))
            except Exception:
                pass

        if "state_dir" in data:
            self._config.state_dir = str(data["state_dir"])
        if "state_file" in data:
            self._config.state_file = str(data["state_file"])

        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

