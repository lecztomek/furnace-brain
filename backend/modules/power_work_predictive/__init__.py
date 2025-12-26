from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import time
import math

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
class WorkPowerPredictiveConfig:
    """
    Regulator mocy w trybie WORK:
      - baza: stabilny PI (fallback)
      - w tle: model uczony online (ARX + RLS)
      - przejęcie: warunkowe i płynne (blend α)

    Wyjście:
      outputs.power_percent ∈ [min_power..max_power] (TYLKO w WORK)

    Poza WORK:
      - nie nadpisuje outputs.power_percent,
      - utrzymuje stan (tracking w IGNITION) aby nie było skoku po wejściu w WORK.

    Model:
      y[k] = a*y[k-1] + b*u[k-delay] + c
      parametry uczone RLS z zapominaniem λ.

    Przełączanie:
      - jeśli |błąd| mały + RMSE predykcji mały przez stable_required_s -> α rośnie
      - jeśli |błąd| duży lub RMSE duży -> α spada szybko do 0
      - finalna moc: (1-α)*P_PI + α*P_MODEL
    """

    enabled: bool = True
    boiler_set_temp: float = 55.0

    # wyjście
    min_power: float = 10.0
    max_power: float = 100.0

    # korekta przegrzania
    overtemp_start_degC: float = 2.0
    overtemp_kp: float = 10.0

    # ograniczenie szybkości zmian mocy w WORK
    max_slew_rate_percent_per_min: float = 10.0  # 0 = off

    # filtry
    boiler_temp_filter_tau_s: float = 15.0
    flue_temp_filter_tau_s: float = 30.0

    # PI (fallback)
    pi_kp: float = 6.0
    pi_ki: float = 0.02
    pi_integral_window_s: float = 900.0

    # model (uczenie)
    model_enabled: bool = True
    model_update_period_s: float = 15.0
    model_delay_s: float = 90.0
    model_lambda: float = 0.995
    model_init_a: float = 0.995
    model_init_b: float = 0.02
    model_init_c: float = 0.0
    model_init_P_diag: float = 1e6

    # jakość predykcji
    pred_err_ewma_tau_s: float = 600.0

    # sterowanie modelowe (MPC-lite)
    model_horizon_s: float = 20 * 60.0
    model_gain: float = 1.0  # pkt%/°C

    # przełączanie / blending
    switch_use_model: bool = True
    err_on_degC: float = 3.0
    rmse_on_degC: float = 1.0
    err_off_degC: float = 6.0
    rmse_off_degC: float = 2.0
    stable_required_s: float = 10 * 60.0
    alpha_ramp_up_per_s: float = 1.0 / (10 * 60.0)
    alpha_ramp_down_per_s: float = 1.0 / (2 * 60.0)

    # persist
    state_dir: str = "data"
    state_file: str = "power_work_predictive_state.yaml"
    state_save_interval_s: float = 30.0
    state_max_age_s: float = 15 * 60.0
    state_max_temp_delta_C: float = 5.0


class WorkPowerPredictiveModule(ModuleInterface):
    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[WorkPowerPredictiveConfig] = None,
        data_root: Optional[Path] = None,
    ) -> None:
        self._base_path = (base_path or Path(__file__).resolve().parent)
        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or WorkPowerPredictiveConfig()

        # ścieżki persist (tymczasowo)
        self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        self._load_config_from_file()

        # docelowa ścieżka persist (jak w power_work)
        if data_root is not None:
            self._state_dir = (Path(data_root).resolve() / "modules" / self.id).resolve() / "data"
        else:
            self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        self._last_state_save_wall_ts: Optional[float] = None
        self._last_enabled: bool = bool(self._config.enabled)

        # filtry
        self._boiler_tf: Optional[float] = None
        self._boiler_tf_prev: Optional[float] = None  # (1) poprzednia wartość do modelu (y[k-1])
        self._flue_tf: Optional[float] = None

        # PI
        self._pi_integral: float = 0.0
        self._pi_last_error: Optional[float] = None
        self._pi_last_tick_ts: Optional[float] = None
        self._pi_power: float = 0.0

        # wyjście + tryb
        self._power: float = 0.0
        self._last_in_work: bool = False
        self._last_power_ts: Optional[float] = None

        # model RLS: theta=[a,b,c], P 3x3
        self._theta: List[float] = [self._config.model_init_a, self._config.model_init_b, self._config.model_init_c]
        self._P: List[List[float]] = [
            [self._config.model_init_P_diag, 0.0, 0.0],
            [0.0, self._config.model_init_P_diag, 0.0],
            [0.0, 0.0, self._config.model_init_P_diag],
        ]
        self._model_last_update_ts: Optional[float] = None
        self._pred_rmse_ewma: float = 99.0

        # opóźnienie: historia u(t)
        self._u_hist: List[Tuple[float, float]] = []  # (ts_ctrl, power)

        # blending
        self._alpha: float = 0.0
        self._stable_since_ts: Optional[float] = None

        # restore
        self._restored_state_meta: Optional[Dict[str, Any]] = None
        self._try_restore_state_from_disk()

    @property
    def id(self) -> str:
        return "power_work_predictive"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()

        mode_enum = system_state.mode
        in_work = (mode_enum == BoilerMode.WORK)
        prev_in_work = self._last_in_work
        prev_power = self._power

        now_ctrl = float(getattr(system_state, "ts_mono", now))

        enabled_now = bool(self._config.enabled)
        if enabled_now != self._last_enabled:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="POWER_WORK_PREDICTIVE_ENABLED_CHANGED",
                    message=f"{self.id}: {'ENABLED' if enabled_now else 'DISABLED'}",
                    data={"enabled": enabled_now},
                )
            )
            self._last_enabled = enabled_now

        if prev_in_work != in_work:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="POWER_WORK_PREDICTIVE_MODE_CHANGED",
                    message=f"{self.id}: {'ENTER' if in_work else 'LEAVE'} WORK",
                    data={"in_work": in_work},
                )
            )

        if not prev_in_work and in_work:
            self._last_power_ts = None  # pierwszy krok bez slew limit

        boiler_temp = sensors.boiler_temp

        # walidacja restore po temp.
        if boiler_temp is not None and self._restored_state_meta is not None:
            if not self._validate_restored_state(float(boiler_temp), now, events):
                self._hard_reset_runtime_state()
            self._restored_state_meta = None

        # filtry
        boiler_tf: Optional[float] = None
        if boiler_temp is not None:
            # (1) zapamiętaj poprzednią wartość filtrowaną do modelu (y[k-1])
            self._boiler_tf_prev = self._boiler_tf

            boiler_tf = self._lowpass_update(
                prev=self._boiler_tf,
                x=float(boiler_temp),
                dt=self._dt(self._pi_last_tick_ts, now_ctrl),
                tau=max(float(self._config.boiler_temp_filter_tau_s), 0.1),
            )
            self._boiler_tf = boiler_tf

        flue_temp = getattr(sensors, "flue_temp", None)
        if flue_temp is not None:
            self._flue_tf = self._lowpass_update(
                prev=self._flue_tf,
                x=float(flue_temp),
                dt=self._dt(self._pi_last_tick_ts, now_ctrl),
                tau=max(float(self._config.flue_temp_filter_tau_s), 0.1),
            )

        # gdy wyłączony: nie nadpisuj output, tylko tracking i reset α
        if not enabled_now:
            if boiler_tf is not None:
                actual_power = float(system_state.outputs.power_percent)
                self._track_pi_to_power(now_ctrl, boiler_tf, actual_power)
                self._power = actual_power
                self._alpha = 0.0
                self._stable_since_ts = None

            self._last_in_work = in_work
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # historia u(t) do modelu
        current_power_for_history = float(system_state.outputs.power_percent if not in_work else self._power)
        self._push_u_history(now_ctrl, current_power_for_history)

        # PI / tracking
        if boiler_tf is not None:
            if in_work:
                p_pi = self._pi_step(now_ctrl, boiler_tf)
            else:
                if system_state.mode == BoilerMode.IGNITION:
                    actual_power = float(system_state.outputs.power_percent)
                    self._track_pi_to_power(now_ctrl, boiler_tf, actual_power)
                    p_pi = actual_power
                else:
                    self._pi_step(now_ctrl, boiler_tf)
                    p_pi = self._pi_power
        else:
            p_pi = self._pi_power

        # uczenie modelu
        if self._config.model_enabled and boiler_tf is not None:
            self._maybe_update_model(now_ctrl, boiler_tf)

        # poza WORK: nie nadpisuj
        if not in_work:
            self._last_in_work = in_work
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # WORK: moc PI i moc modelowa
        power_model = float(p_pi)
        if self._config.switch_use_model and self._config.model_enabled and boiler_tf is not None:
            power_model = self._model_based_power(boiler_tf, p_pi)

        # α (warunkowe przejęcie)
        self._update_alpha(now_ctrl, boiler_tf)

        power = (1.0 - self._alpha) * float(p_pi) + self._alpha * float(power_model)

        # przegrzanie
        if boiler_temp is not None:
            t_set = float(self._config.boiler_set_temp)
            start = max(float(self._config.overtemp_start_degC), 0.0)
            if float(boiler_temp) > t_set + start:
                over = float(boiler_temp) - (t_set + start)
                power -= over * max(float(self._config.overtemp_kp), 0.0)

        # clamp + slew
        power = self._clamp(power, float(self._config.min_power), float(self._config.max_power))
        power = self._apply_slew_limit(power, prev_power, now_ctrl, prev_in_work)

        self._power = power
        self._pi_power = float(p_pi)
        self._last_in_work = True

        if abs(self._power - prev_power) >= 5.0:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="POWER_WORK_PREDICTIVE_LEVEL_CHANGED",
                    message=(
                        f"{self.id}: {prev_power:.1f}% → {self._power:.1f}% "
                        f"(α={self._alpha:.2f}, RMSE≈{self._pred_rmse_ewma:.2f}°C)"
                    ),
                    data={
                        "prev_power": prev_power,
                        "power": self._power,
                        "alpha": float(self._alpha),
                        "pi_power": float(p_pi),
                        "model_power": float(power_model),
                        "pred_rmse_ewma": float(self._pred_rmse_ewma),
                        "boiler_temp": float(boiler_temp) if boiler_temp is not None else None,
                        "boiler_set_temp": float(self._config.boiler_set_temp),
                    },
                )
            )

        outputs.power_percent = self._power  # type: ignore[attr-defined]

        self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # ---------------- PI ----------------

    def _pi_step(self, now_ctrl: float, boiler_tf: float) -> float:
        error = float(self._config.boiler_set_temp) - float(boiler_tf)

        dt = self._dt(self._pi_last_tick_ts, now_ctrl)
        if dt is not None:
            window = max(float(self._config.pi_integral_window_s), 1.0)
            decay = 1.0 - dt / window
            decay = self._clamp(decay, 0.0, 1.0)
            self._pi_integral *= decay
            self._pi_integral += error * dt

        p_term = float(self._config.pi_kp) * error
        i_term = float(self._config.pi_ki) * self._pi_integral
        power = p_term + i_term

        self._pi_last_error = error
        self._pi_last_tick_ts = now_ctrl
        self._pi_power = power
        return power

    def _track_pi_to_power(self, now_ctrl: float, boiler_tf: float, actual_power: float) -> None:
        error = float(self._config.boiler_set_temp) - float(boiler_tf)
        self._pi_last_tick_ts = now_ctrl
        self._pi_last_error = error

        ki = float(self._config.pi_ki)
        if ki <= 0.0:
            self._pi_power = float(actual_power)
            return

        p_term = float(self._config.pi_kp) * error
        integral = (float(actual_power) - p_term) / ki
        self._pi_integral = self._clamp(integral, -10000.0, 10000.0)
        self._pi_power = float(actual_power)

    # ---------------- MODEL (RLS) ----------------

    def _maybe_update_model(self, now_ctrl: float, boiler_tf: float) -> None:
        period = max(float(self._config.model_update_period_s), 1.0)
        if self._model_last_update_ts is not None and (now_ctrl - self._model_last_update_ts) < period:
            return

        # u(t-delay)
        u_del = self._get_delayed_u(now_ctrl)
        if u_del is None:
            return

        # (1) y_prev MUSI być z poprzedniej próbki (y[k-1]) – nie bieżąca
        y_prev = float(self._boiler_tf_prev if self._boiler_tf_prev is not None else boiler_tf)

        # predykcja 1-krok
        a, b, c = self._theta
        y_hat = a * y_prev + b * float(u_del) + c
        resid = float(boiler_tf) - y_hat

        # (2)(3)(4) zabezpieczenie przed niefinity (chroni RMSE i RLS przed "wystrzałem")
        if not (math.isfinite(y_hat) and math.isfinite(resid)):
            self._reset_model_only()
            return

        self._update_pred_rmse(now_ctrl, resid)

        phi = [y_prev, float(u_del), 1.0]
        self._rls_update(phi, float(boiler_tf))

        self._model_last_update_ts = now_ctrl

    def _rls_update(self, phi: List[float], y: float) -> None:
        lam = self._clamp(float(self._config.model_lambda), 0.90, 0.99999)

        Pphi = [
            self._P[0][0] * phi[0] + self._P[0][1] * phi[1] + self._P[0][2] * phi[2],
            self._P[1][0] * phi[0] + self._P[1][1] * phi[1] + self._P[1][2] * phi[2],
            self._P[2][0] * phi[0] + self._P[2][1] * phi[1] + self._P[2][2] * phi[2],
        ]

        denom = lam + (phi[0] * Pphi[0] + phi[1] * Pphi[1] + phi[2] * Pphi[2])
        if denom <= 1e-12 or not math.isfinite(denom):
            return

        K = [Pphi[0] / denom, Pphi[1] / denom, Pphi[2] / denom]

        y_hat = phi[0] * self._theta[0] + phi[1] * self._theta[1] + phi[2] * self._theta[2]
        err = y - y_hat
        if not (math.isfinite(y_hat) and math.isfinite(err)):
            self._reset_model_only()
            return

        self._theta[0] += K[0] * err
        self._theta[1] += K[1] * err
        self._theta[2] += K[2] * err

        M = [
            [1.0 - K[0] * phi[0], -K[0] * phi[1], -K[0] * phi[2]],
            [-K[1] * phi[0], 1.0 - K[1] * phi[1], -K[1] * phi[2]],
            [-K[2] * phi[0], -K[2] * phi[1], 1.0 - K[2] * phi[2]],
        ]

        MP = [[0.0] * 3 for _ in range(3)]
        for i in range(3):
            for j in range(3):
                MP[i][j] = M[i][0] * self._P[0][j] + M[i][1] * self._P[1][j] + M[i][2] * self._P[2][j]

        inv_lam = 1.0 / lam
        for i in range(3):
            for j in range(3):
                self._P[i][j] = MP[i][j] * inv_lam

        # sanity: a w sensownym zakresie
        self._theta[0] = self._clamp(self._theta[0], 0.90, 0.9999)

        # (2) sanity: b i c w sensownym zakresie, żeby nie rozjechać predykcji/RMSE
        self._theta[1] = self._clamp(self._theta[1], -1.0, 1.0)      # b
        self._theta[2] = self._clamp(self._theta[2], -100.0, 100.0)  # c

        # (3) sanity: macierz P nie może się rozjechać liczbowo
        for i in range(3):
            for j in range(3):
                v = float(self._P[i][j])
                if not math.isfinite(v) or abs(v) > 1e12:
                    self._reset_model_only()
                    return

    def _update_pred_rmse(self, now_ctrl: float, resid: float) -> None:
        tau = max(float(self._config.pred_err_ewma_tau_s), 1.0)
        dt = 0.0
        if self._model_last_update_ts is not None:
            dt = max(0.0, now_ctrl - self._model_last_update_ts)

        alpha = 1.0 - math.exp(-dt / tau) if dt > 0 else 0.0
        s2 = resid * resid

        prev_rmse = float(self._pred_rmse_ewma)
        prev_s2 = prev_rmse * prev_rmse
        new_s2 = (1.0 - alpha) * prev_s2 + alpha * s2
        new_rmse = float(math.sqrt(max(new_s2, 0.0)))

        # (4) sanity: RMSE też musi być skończone
        self._pred_rmse_ewma = new_rmse if math.isfinite(new_rmse) else 99.0

    def _model_based_power(self, boiler_tf: float, p_pi: float) -> float:
        a, b, c = self._theta
        if abs(b) < 1e-6:
            return float(p_pi)

        horizon = max(float(self._config.model_horizon_s), 60.0)
        step = max(float(self._config.model_update_period_s), 10.0)
        n = int(max(1, horizon / step))

        y = float(boiler_tf)
        u = float(p_pi)
        for _ in range(n):
            y = a * y + b * u + c
            if not math.isfinite(y):
                return float(p_pi)

        e_end = float(self._config.boiler_set_temp) - y
        correction = float(self._config.model_gain) * e_end
        out = float(p_pi) + correction
        return out if math.isfinite(out) else float(p_pi)

    def _reset_model_only(self) -> None:
        # bez zmiany "zasad działania" modułu: tylko bezpieczny reset części modelowej
        self._theta = [self._config.model_init_a, self._config.model_init_b, self._config.model_init_c]
        self._P = [
            [self._config.model_init_P_diag, 0.0, 0.0],
            [0.0, self._config.model_init_P_diag, 0.0],
            [0.0, 0.0, self._config.model_init_P_diag],
        ]
        self._model_last_update_ts = None
        self._pred_rmse_ewma = 99.0

    # ---------------- SWITCH / α ----------------

    def _update_alpha(self, now_ctrl: float, boiler_tf: float) -> None:
        if not self._config.switch_use_model or not self._config.model_enabled:
            self._alpha = 0.0
            self._stable_since_ts = None
            return

        e = abs(float(self._config.boiler_set_temp) - float(boiler_tf))
        rmse = float(self._pred_rmse_ewma)

        good = (e <= float(self._config.err_on_degC)) and (rmse <= float(self._config.rmse_on_degC))
        bad = (e >= float(self._config.err_off_degC)) or (rmse >= float(self._config.rmse_off_degC))

        if good:
            if self._stable_since_ts is None:
                self._stable_since_ts = now_ctrl
        else:
            self._stable_since_ts = None

        can_ramp_up = False
        if self._stable_since_ts is not None:
            can_ramp_up = (now_ctrl - self._stable_since_ts) >= float(self._config.stable_required_s)

        dt = 0.0
        if self._last_power_ts is not None:
            dt = max(0.0, now_ctrl - self._last_power_ts)

        if bad:
            self._alpha = max(0.0, float(self._alpha) - float(self._config.alpha_ramp_down_per_s) * dt)
        else:
            if can_ramp_up:
                self._alpha = min(1.0, float(self._alpha) + float(self._config.alpha_ramp_up_per_s) * dt)
            else:
                self._alpha = max(0.0, float(self._alpha) - 0.5 * float(self._config.alpha_ramp_down_per_s) * dt)

    # ---------------- HELPERS ----------------

    def _apply_slew_limit(self, target: float, prev_power: float, now_ctrl: float, prev_in_work: bool) -> float:
        max_slew = max(float(self._config.max_slew_rate_percent_per_min), 0.0)
        if max_slew <= 0.0:
            self._last_power_ts = now_ctrl
            return target

        if self._last_power_ts is None or not prev_in_work:
            self._last_power_ts = now_ctrl
            return target

        dt = now_ctrl - self._last_power_ts
        if dt <= 0:
            return prev_power

        max_delta = max_slew * dt / 60.0
        delta = target - prev_power
        if delta > max_delta:
            out = prev_power + max_delta
        elif delta < -max_delta:
            out = prev_power - max_delta
        else:
            out = target

        self._last_power_ts = now_ctrl
        return out

    def _push_u_history(self, now_ctrl: float, power: float) -> None:
        self._u_hist.append((now_ctrl, float(power)))

        keep_s = (
            max(float(self._config.model_delay_s), 0.0)
            + max(float(self._config.model_horizon_s), 0.0)
            + 5 * 60.0
        )
        cutoff = now_ctrl - keep_s
        while self._u_hist and self._u_hist[0][0] < cutoff:
            self._u_hist.pop(0)

    def _get_delayed_u(self, now_ctrl: float) -> Optional[float]:
        delay = max(float(self._config.model_delay_s), 0.0)
        target_ts = now_ctrl - delay
        if not self._u_hist:
            return None

        for ts, val in reversed(self._u_hist):
            if ts <= target_ts:
                return float(val)
        return None

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        if x < lo:
            return lo
        if x > hi:
            return hi
        return x

    @staticmethod
    def _dt(prev_ts: Optional[float], now_ts: float) -> Optional[float]:
        if prev_ts is None:
            return None
        dt = now_ts - prev_ts
        return dt if dt > 0 else None

    @staticmethod
    def _lowpass_update(prev: Optional[float], x: float, dt: Optional[float], tau: float) -> float:
        if prev is None:
            return x
        if dt is None or dt <= 0:
            return prev
        alpha = dt / (tau + dt)
        return prev + alpha * (x - prev)

    def _hard_reset_runtime_state(self) -> None:
        self._boiler_tf = None
        self._boiler_tf_prev = None
        self._flue_tf = None

        self._pi_integral = 0.0
        self._pi_last_error = None
        self._pi_last_tick_ts = None
        self._pi_power = 0.0

        self._power = 0.0
        self._last_power_ts = None
        self._alpha = 0.0
        self._stable_since_ts = None

        self._theta = [self._config.model_init_a, self._config.model_init_b, self._config.model_init_c]
        self._P = [
            [self._config.model_init_P_diag, 0.0, 0.0],
            [0.0, self._config.model_init_P_diag, 0.0],
            [0.0, 0.0, self._config.model_init_P_diag],
        ]
        self._model_last_update_ts = None
        self._pred_rmse_ewma = 99.0
        self._u_hist = []

    # ---------------- PERSIST ----------------

    def _try_restore_state_from_disk(self) -> None:
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
            if age < 0 or age > max_age:
                return

        # PI
        pi_integral = data.get("pi_integral")
        pi_last_error = data.get("pi_last_error")
        pi_power = data.get("pi_power")

        if isinstance(pi_integral, (int, float)):
            self._pi_integral = float(pi_integral)
        if pi_last_error is None or isinstance(pi_last_error, (int, float)):
            self._pi_last_error = float(pi_last_error) if pi_last_error is not None else None
        if isinstance(pi_power, (int, float)):
            self._pi_power = float(pi_power)

        # model
        theta = data.get("theta")
        Pm = data.get("P")
        pred_rmse = data.get("pred_rmse_ewma")
        alpha = data.get("alpha")
        power = data.get("power")

        if isinstance(theta, list) and len(theta) == 3 and all(isinstance(x, (int, float)) for x in theta):
            self._theta = [float(theta[0]), float(theta[1]), float(theta[2])]
            # sanity po restore
            self._theta[0] = self._clamp(self._theta[0], 0.90, 0.9999)
            self._theta[1] = self._clamp(self._theta[1], -1.0, 1.0)
            self._theta[2] = self._clamp(self._theta[2], -100.0, 100.0)

        if (
            isinstance(Pm, list) and len(Pm) == 3
            and all(isinstance(row, list) and len(row) == 3 for row in Pm)
        ):
            ok = True
            Pnew: List[List[float]] = []
            for row in Pm:
                if not all(isinstance(x, (int, float)) for x in row):
                    ok = False
                    break
                Pnew.append([float(row[0]), float(row[1]), float(row[2])])
            if ok:
                self._P = Pnew

        if isinstance(pred_rmse, (int, float)):
            v = float(pred_rmse)
            self._pred_rmse_ewma = v if math.isfinite(v) else 99.0

        if isinstance(alpha, (int, float)):
            self._alpha = self._clamp(float(alpha), 0.0, 1.0)

        if isinstance(power, (int, float)):
            self._power = float(power)

        self._restored_state_meta = {
            "saved_wall_ts": float(saved_wall_ts),
            "saved_boiler_temp": data.get("boiler_temp"),
        }

        # po restarcie
        self._pi_last_tick_ts = None
        self._last_power_ts = None
        self._model_last_update_ts = None
        self._boiler_tf_prev = None

    def _validate_restored_state(self, current_boiler_temp: float, now_wall: float, events: List[Event]) -> bool:
        meta = self._restored_state_meta or {}
        saved_temp = meta.get("saved_boiler_temp")

        if saved_temp is None or not isinstance(saved_temp, (int, float)):
            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="POWER_WORK_PREDICTIVE_STATE_RESTORED",
                    message=f"{self.id}: przywrócono stan z dysku (bez walidacji temp)",
                    data={},
                )
            )
            return True

        delta = abs(float(current_boiler_temp) - float(saved_temp))
        if delta > float(self._config.state_max_temp_delta_C):
            events.append(
                Event(
                    ts=now_wall,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="POWER_WORK_PREDICTIVE_STATE_RESTORE_SKIPPED",
                    message=f"{self.id}: pominięto restore (ΔT={delta:.1f}°C > {self._config.state_max_temp_delta_C:.1f}°C)",
                    data={"delta_temp": delta, "saved_temp": float(saved_temp), "current_temp": float(current_boiler_temp)},
                )
            )
            return False

        events.append(
            Event(
                ts=now_wall,
                source=self.id,
                level=EventLevel.INFO,
                type="POWER_WORK_PREDICTIVE_STATE_RESTORED",
                message=f"{self.id}: przywrócono stan z dysku",
                data={"saved_temp": float(saved_temp), "current_temp": float(current_boiler_temp)},
            )
        )
        return True

    def _maybe_persist_state(self, now_wall: float, boiler_temp: Optional[float], events: List[Event]) -> None:
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

                "power": float(self._power),
                "alpha": float(self._alpha),
                "pred_rmse_ewma": float(self._pred_rmse_ewma),

                "pi_integral": float(self._pi_integral),
                "pi_last_error": float(self._pi_last_error) if self._pi_last_error is not None else None,
                "pi_power": float(self._pi_power),

                "theta": [float(self._theta[0]), float(self._theta[1]), float(self._theta[2])],
                "P": [
                    [float(self._P[0][0]), float(self._P[0][1]), float(self._P[0][2])],
                    [float(self._P[1][0]), float(self._P[1][1]), float(self._P[1][2])],
                    [float(self._P[2][0]), float(self._P[2][1]), float(self._P[2][2])],
                ],
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
                    type="POWER_WORK_PREDICTIVE_STATE_SAVE_ERROR",
                    message=f"{self.id}: błąd zapisu stanu: {exc}",
                    data={"error": str(exc)},
                )
            )

    # ---------------- CONFIG ----------------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        for k, v in values.items():
            if hasattr(self._config, k):
                cur = getattr(self._config, k)
                if isinstance(cur, bool):
                    setattr(self._config, k, bool(v))
                elif isinstance(cur, (int, float)):
                    setattr(self._config, k, float(v))
                else:
                    setattr(self._config, k, str(v))

        self._state_path = self._state_dir / self._config.state_file

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()
        self._state_path = self._state_dir / self._config.state_file

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for k, v in data.items():
            if hasattr(self._config, k):
                cur = getattr(self._config, k)
                if isinstance(cur, bool):
                    setattr(self._config, k, bool(v))
                elif isinstance(cur, (int, float)):
                    setattr(self._config, k, float(v))
                else:
                    setattr(self._config, k, str(v))

        self._state_path = self._state_dir / self._config.state_file

    def _save_config_to_file(self) -> None:
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(self._config), f, sort_keys=True, allow_unicode=True)

