from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Deque, Tuple
from collections import deque
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
    Prosty regulator samouczący (WORK) – wersja uproszczona:

    1) Uczenie baseline mocy jako EMA (z realnej mocy systemu).
       Próbki zbieramy tylko gdy:
         - |błąd| <= learn_gate_err_degC
         - temperatura jest "stabilna" w oknie: span(T) <= learn_max_span_degC
           gdzie span(T) = max(T) - min(T) z ostatnich temp_span_window_s sekund.

    2) Przejęcie sterowania:
         - gdy nauczył się min. czasu i min. próbek (i ma już EMA) -> TAKEOVER OD RAZU
           (bez dodatkowej fazy "stability required").

    3) Sterowanie po przejęciu (krokowe, proste):
         - start od baseline EMA
         - co min_adjust_interval_s:
             jeśli T < set - deadband -> +takeover_step_percent
             jeśli T > set + deadband -> -takeover_step_percent
             jeśli w deadband -> brak zmiany
         - clamp do [min_power, max_power]
         - wyjątek bezpieczeństwa: przy przegrzaniu można natychmiast obniżyć moc

    4) Poza WORK: nie nadpisuje outputs.power_percent.
    """

    enabled: bool = True
    boiler_set_temp: float = 55.0

    # wyjście
    min_power: float = 10.0
    max_power: float = 100.0

    # filtr temp
    boiler_temp_filter_tau_s: float = 15.0

    # okno stabilności (span)
    temp_span_window_s: float = 5 * 60.0  # okno do liczenia span(T)=max-min

    # EMA baseline (uczenie)
    ema_tau_s: float = 20 * 60.0
    learn_gate_err_degC: float = 1.0
    learn_max_span_degC: float = 0.5
    learn_min_time_s: float = 10 * 60.0
    learn_min_samples: int = 30

    # takeover: krok regulacji
    takeover_step_percent: float = 1.0

    # oddanie sterowania gdy błąd duży
    dropout_err_off_degC: float = 5.0

    # deadband (wykorzystujemy istniejące pole trim_deadband_degC jako deadband dla kroku)
    trim_deadband_degC: float = 0.5

    # korekta przegrzania
    overtemp_start_degC: float = 2.0
    overtemp_kp: float = 10.0

    # minimalny odstęp między zmianami mocy
    min_adjust_interval_s: float = 60.0  # 0 = zmieniaj zawsze

    # logi
    status_log_period_s: float = 30.0

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

        # persist paths (fallback)
        self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        self._load_config_from_file()

        # docelowa ścieżka persist
        if data_root is not None:
            self._state_dir = (Path(data_root).resolve() / "modules" / self.id).resolve() / "data"
        else:
            self._state_dir = (self._base_path / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        self._last_state_save_wall_ts: Optional[float] = None
        self._last_enabled: bool = bool(self._config.enabled)

        # filtry
        self._filter_last_ts: Optional[float] = None
        self._boiler_tf: Optional[float] = None

        # okno temperatury do span(T)
        self._temp_hist: Deque[Tuple[float, float]] = deque()  # (now_ctrl, boiler_tf)

        # uczenie EMA
        self._power_ema: Optional[float] = None
        self._learn_start_ctrl_ts: Optional[float] = None
        self._learn_samples: int = 0

        # przejęcie
        self._takeover: bool = False

        # wyjście (gdy przejęte)
        self._power_cmd: float = 0.0

        # ograniczenie częstotliwości zmian
        self._last_adjust_ctrl_ts: Optional[float] = None

        # logi
        self._last_status_log_ctrl_ts: Optional[float] = None
        self._last_in_work: bool = False

        # restore meta
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
            # reset logów / sesji po zmianie trybu
            self._last_status_log_ctrl_ts = None

            if not in_work:
                # poza WORK oddaj sterowanie
                self._takeover = False
                self._last_adjust_ctrl_ts = None
            else:
                # wejście do WORK: start uczenia jeśli nowa sesja
                if self._learn_start_ctrl_ts is None:
                    self._learn_start_ctrl_ts = now_ctrl
                self._last_adjust_ctrl_ts = None

        boiler_temp = sensors.boiler_temp

        # walidacja restore po temp.
        if boiler_temp is not None and self._restored_state_meta is not None:
            if not self._validate_restored_state(float(boiler_temp), now, events):
                self._hard_reset_runtime_state()
            self._restored_state_meta = None

        # filtr temp
        dt_filt = self._dt(self._filter_last_ts, now_ctrl)
        self._filter_last_ts = now_ctrl

        boiler_tf: Optional[float] = None
        if boiler_temp is not None:
            boiler_tf = self._lowpass_update(
                prev=self._boiler_tf,
                x=float(boiler_temp),
                dt=dt_filt,
                tau=max(float(self._config.boiler_temp_filter_tau_s), 0.1),
            )
            self._boiler_tf = boiler_tf

            # aktualizuj historię do span(T)
            self._push_temp_history(now_ctrl, float(boiler_tf))

        # realna moc systemu (źródło do uczenia gdy nie sterujemy)
        try:
            actual_power = float(system_state.outputs.power_percent)
        except Exception:
            actual_power = 0.0

        # disabled: nic nie nadpisuj
        if not enabled_now:
            self._takeover = False
            self._last_adjust_ctrl_ts = None
            self._last_in_work = in_work
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # poza WORK: nic nie nadpisuj
        if not in_work:
            self._last_in_work = False
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # WORK
        if self._learn_start_ctrl_ts is None:
            self._learn_start_ctrl_ts = now_ctrl

        # policz span(T) w oknie
        span = self._temp_span(now_ctrl, float(self._config.temp_span_window_s))

        # --- Uczenie EMA baseline: tylko przed takeover i tylko gdy warunki OK ---
        if (not self._takeover) and boiler_tf is not None and span is not None:
            err = float(self._config.boiler_set_temp) - float(boiler_tf)

            err_ok = abs(err) <= float(self._config.learn_gate_err_degC)
            span_ok = span <= float(self._config.learn_max_span_degC)

            if err_ok and span_ok:
                self._power_ema = self._ema_update(
                    prev=self._power_ema,
                    x=float(actual_power),
                    dt=dt_filt,
                    tau=max(float(self._config.ema_tau_s), 1.0),
                )
                self._learn_samples += 1

        # czy nauczył się min.
        learn_elapsed = 0.0
        if self._learn_start_ctrl_ts is not None:
            learn_elapsed = max(0.0, now_ctrl - self._learn_start_ctrl_ts)
        learned_enough = (
            learn_elapsed >= float(self._config.learn_min_time_s)
            and self._learn_samples >= int(self._config.learn_min_samples)
            and self._power_ema is not None
        )

        # --- TAKEOVER OD RAZU PO NAUCE (bez stabilności) ---
        if (not self._takeover) and learned_enough:
            self._takeover = True

            baseline0 = float(self._power_ema if self._power_ema is not None else actual_power)
            baseline0 = self._clamp(baseline0, float(self._config.min_power), float(self._config.max_power))

            # startujemy dokładnie od nauczonej mocy
            self._power_cmd = float(baseline0)

            # liczymy interwał od teraz: pierwsza korekta dopiero po min_adjust_interval_s (chyba że safety)
            self._last_adjust_ctrl_ts = now_ctrl

            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="POWER_WORK_PREDICTIVE_TAKEOVER_ON",
                    message=f"{self.id}: TAKEOVER ON (learn_elapsed={learn_elapsed:.0f}s samples={self._learn_samples} baseline0={baseline0:.1f}%)",
                    data={"learn_elapsed_s": float(learn_elapsed), "learn_samples": int(self._learn_samples), "baseline0": float(baseline0)},
                )
            )

        # oddanie sterowania gdy błąd duży
        err_abs: Optional[float] = None
        if self._takeover and boiler_tf is not None:
            err_abs = abs(float(self._config.boiler_set_temp) - float(boiler_tf))
            if float(err_abs) >= float(self._config.dropout_err_off_degC):
                self._takeover = False

                # RESET SESJI (learning + timery) — tak jak chcesz
                self._power_ema = None
                self._learn_samples = 0
                self._learn_start_ctrl_ts = now_ctrl  # start nowej nauki od teraz

                self._last_adjust_ctrl_ts = None
                self._temp_hist.clear()               # żeby span(T) zaczynał od nowa

                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="POWER_WORK_PREDICTIVE_TAKEOVER_OFF",
                        message=f"{self.id}: TAKEOVER OFF + RESET (|err|={float(err_abs):.2f}°C)",
                        data={"err_abs_degC": float(err_abs), "reset": True},
                    )
                )
                
        # jeśli nie przejęte: nie steruj
        if not self._takeover:
            self._maybe_log_status(
                now_wall=now,
                now_ctrl=now_ctrl,
                boiler_tf=boiler_tf,
                span=span,
                actual_power=actual_power,
                learned_enough=learned_enough,
                events=events,
            )
            self._last_in_work = True
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # ---------- TAKEOVER: proste sterowanie krokowe ----------
        step = max(float(self._config.takeover_step_percent), 0.0)
        deadband = max(float(self._config.trim_deadband_degC), 0.0)

        power_target = float(self._power_cmd)  # domyślnie trzymaj

        # decyzja: +step / -step / 0
        if boiler_tf is not None and step > 0.0:
            err = float(self._config.boiler_set_temp) - float(boiler_tf)  # + gdy za zimno
            if err > deadband:
                power_target = float(self._power_cmd) + step
            elif err < -deadband:
                power_target = float(self._power_cmd) - step

        # przegrzanie (safety) – może natychmiast obniżyć moc
        overtemp_active = False
        if boiler_temp is not None:
            t_set = float(self._config.boiler_set_temp)
            start = max(float(self._config.overtemp_start_degC), 0.0)
            if float(boiler_temp) > t_set + start:
                overtemp_active = True
                over = float(boiler_temp) - (t_set + start)
                power_target -= over * max(float(self._config.overtemp_kp), 0.0)

        # clamp
        power_target = self._clamp(power_target, float(self._config.min_power), float(self._config.max_power))

        # ograniczenie częstotliwości zmian
        power_cmd = self._apply_min_adjust_interval(
            target=float(power_target),
            now_ctrl=now_ctrl,
            force_decrease=bool(overtemp_active),
        )

        self._power_cmd = float(power_cmd)
        outputs.power_percent = self._power_cmd  # type: ignore[attr-defined]

        self._maybe_log_status(
            now_wall=now,
            now_ctrl=now_ctrl,
            boiler_tf=boiler_tf,
            span=span,
            actual_power=actual_power,
            learned_enough=learned_enough,
            events=events,
        )

        self._last_in_work = True
        self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, events=events)
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # ---------------- MIN ADJUST INTERVAL ----------------

    def _apply_min_adjust_interval(self, target: float, now_ctrl: float, force_decrease: bool) -> float:
        """
        Ogranicza częstotliwość zmian mocy.
        - Jeśli min_adjust_interval_s=0 -> zawsze przyjmuj target.
        - Jeśli nie minął interwał -> trzymamy poprzednią moc (self._power_cmd),
          chyba że force_decrease=True i target < self._power_cmd (bezpieczeństwo).
        """
        interval = max(float(self._config.min_adjust_interval_s), 0.0)

        # brak ograniczenia
        if interval <= 0.0:
            if abs(target - float(self._power_cmd)) > 1e-9:
                self._last_adjust_ctrl_ts = now_ctrl
            return float(target)

        # pierwsza zmiana w sesji -> od razu
        if self._last_adjust_ctrl_ts is None:
            if abs(target - float(self._power_cmd)) > 1e-9:
                self._last_adjust_ctrl_ts = now_ctrl
            return float(target)

        dt = now_ctrl - float(self._last_adjust_ctrl_ts)
        if dt < interval:
            # wyjątek: natychmiastowe obniżenie przy safety
            if force_decrease and float(target) < float(self._power_cmd):
                self._last_adjust_ctrl_ts = now_ctrl
                return float(target)
            return float(self._power_cmd)

        # minął interwał -> przyjmij target
        if abs(target - float(self._power_cmd)) > 1e-9:
            self._last_adjust_ctrl_ts = now_ctrl
        return float(target)

    # ---------------- STABILITY WINDOW (SPAN) ----------------

    def _push_temp_history(self, now_ctrl: float, boiler_tf: float) -> None:
        self._temp_hist.append((now_ctrl, float(boiler_tf)))
        keep_s = max(float(self._config.temp_span_window_s), 1.0) + 30.0
        cutoff = now_ctrl - keep_s
        while self._temp_hist and self._temp_hist[0][0] < cutoff:
            self._temp_hist.popleft()

    def _temp_span(self, now_ctrl: float, window_s: float) -> Optional[float]:
        window_s = max(float(window_s), 1.0)
        if not self._temp_hist:
            return None

        cutoff = now_ctrl - window_s
        vals: List[float] = [v for (ts, v) in self._temp_hist if ts >= cutoff]

        # wymagamy sensownego wypełnienia okna
        if len(vals) < 3:
            return None

        oldest_ts_in_vals: Optional[float] = None
        for ts, _v in self._temp_hist:
            if ts >= cutoff:
                oldest_ts_in_vals = ts
                break
        if oldest_ts_in_vals is None:
            return None

        covered = now_ctrl - oldest_ts_in_vals
        if covered < 0.7 * window_s:
            return None

        span = float(max(vals) - min(vals))
        return span if math.isfinite(span) else None

    # ---------------- STATUS LOG ----------------

    def _maybe_log_status(
        self,
        now_wall: float,
        now_ctrl: float,
        boiler_tf: Optional[float],
        span: Optional[float],
        actual_power: float,
        learned_enough: bool,
        events: List[Event],
    ) -> None:
        period = max(float(self._config.status_log_period_s), 1.0)
        if self._last_status_log_ctrl_ts is not None and (now_ctrl - self._last_status_log_ctrl_ts) < period:
            return

        err = None
        if boiler_tf is not None:
            err = float(self._config.boiler_set_temp) - float(boiler_tf)

        events.append(
            Event(
                ts=now_wall,
                source=self.id,
                level=EventLevel.INFO,
                type="POWER_WORK_PREDICTIVE_STATUS",
                message=(
                    f"{self.id}: takeover={self._takeover} "
                    f"boiler_tf={boiler_tf if boiler_tf is not None else 'None'} "
                    f"err={err if err is not None else 'None'} "
                    f"span={span if span is not None else 'None'} "
                    f"ema={self._power_ema if self._power_ema is not None else 'None'} "
                    f"cmd={self._power_cmd:.1f}% actual={actual_power:.1f}% "
                    f"learned={learned_enough} samples={self._learn_samples}"
                ),
                data={
                    "takeover": bool(self._takeover),
                    "boiler_tf": float(boiler_tf) if boiler_tf is not None else None,
                    "err": float(err) if err is not None else None,
                    "temp_span_degC": float(span) if span is not None else None,
                    "temp_span_window_s": float(self._config.temp_span_window_s),
                    "power_ema": float(self._power_ema) if self._power_ema is not None else None,
                    "power_cmd": float(self._power_cmd),
                    "actual_power": float(actual_power),
                    "learn_samples": int(self._learn_samples),
                    "learned_enough": bool(learned_enough),
                    "min_adjust_interval_s": float(self._config.min_adjust_interval_s),
                    "takeover_step_percent": float(self._config.takeover_step_percent),
                    "deadband_degC": float(self._config.trim_deadband_degC),
                },
            )
        )
        self._last_status_log_ctrl_ts = now_ctrl

    # ---------------- HELPERS ----------------

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

    @staticmethod
    def _ema_update(prev: Optional[float], x: float, dt: Optional[float], tau: float) -> float:
        if prev is None:
            return float(x)
        if dt is None or dt <= 0:
            return float(prev)
        alpha = 1.0 - math.exp(-float(dt) / float(tau))
        return float(prev) + alpha * (float(x) - float(prev))

    def _hard_reset_runtime_state(self) -> None:
        self._filter_last_ts = None
        self._boiler_tf = None
        self._temp_hist.clear()

        self._power_ema = None
        self._learn_start_ctrl_ts = None
        self._learn_samples = 0

        self._takeover = False

        self._power_cmd = 0.0
        self._last_adjust_ctrl_ts = None

        self._last_status_log_ctrl_ts = None
        self._last_in_work = False

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

        power_ema = data.get("power_ema")
        learn_samples = data.get("learn_samples")
        power_cmd = data.get("power_cmd")

        if isinstance(power_ema, (int, float)):
            v = float(power_ema)
            self._power_ema = v if math.isfinite(v) else None

        if isinstance(learn_samples, int):
            self._learn_samples = max(0, int(learn_samples))

        if isinstance(power_cmd, (int, float)):
            v = float(power_cmd)
            self._power_cmd = v if math.isfinite(v) else 0.0

        self._restored_state_meta = {
            "saved_wall_ts": float(saved_wall_ts),
            "saved_boiler_temp": data.get("boiler_temp"),
        }

        # po restarcie zawsze startujemy bez takeover
        self._takeover = False
        self._learn_start_ctrl_ts = None
        self._last_adjust_ctrl_ts = None
        self._last_status_log_ctrl_ts = None

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
                "power_ema": float(self._power_ema) if self._power_ema is not None else None,
                "learn_samples": int(self._learn_samples),
                "power_cmd": float(self._power_cmd),
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
                elif isinstance(cur, int):
                    setattr(self._config, k, int(v))
                elif isinstance(cur, float):
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
                elif isinstance(cur, int):
                    setattr(self._config, k, int(v))
                elif isinstance(cur, float):
                    setattr(self._config, k, float(v))
                else:
                    setattr(self._config, k, str(v))

        self._state_path = self._state_dir / self._config.state_file

    def _save_config_to_file(self) -> None:
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(asdict(self._config), f, sort_keys=True, allow_unicode=True)

