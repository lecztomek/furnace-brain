from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
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
class WorkFuzzyPowerConfig:
    enabled: bool = True
    boiler_set_temp: float = 56.0

    min_power: float = 15.0
    max_power: float = 100.0

    max_slew_rate_percent_per_min: float = 10.0  # 0.0 = wyłączony

    boiler_tau_s: float = 180.0
    flue_tau_s: float = 60.0

    # Waga wpływu spalin zależna od |błędu|:
    # - blisko zadanej (|e|~0): w ≈ flue_weight_near
    # - daleko (|e|>=flue_weight_band_C): w ≈ flue_weight_far
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

    # Flue (parametry opisowe)
    flue_min_C: float = 58.0
    flue_mid_C: float = 66.0
    flue_max_C: float = 76.0
    flue_overlap_ratio: float = 0.20
    flue_vhigh_margin_C: float = 8.0

    # ΔP universe
    delta_universe_min: float = -6.0
    delta_universe_max: float = 6.0
    delta_universe_step: float = 0.05
    delta_scale: float = 0.8

    # Persist
    state_dir: str = "data"
    state_file: str = "power_work_fuzzy_state.yaml"
    state_save_interval_s: float = 30.0
    state_max_age_s: float = 15 * 60.0
    state_max_boiler_temp_delta_C: float = 5.0
    state_max_flue_temp_delta_C: float = 30.0


class WorkFuzzyPowerModule(ModuleInterface):
    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[WorkFuzzyPowerConfig] = None,
        data_root: Optional[Path] = None,  # <--- loader wstrzyknie
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or WorkFuzzyPowerConfig()

        # persist root: data_root/modules/power_work_fuzzy (albo fallback do katalogu modułu)
        if data_root is not None:
            self._persist_root = (Path(data_root).resolve() / "modules" / self.id).resolve()
        else:
            self._persist_root = self._base_path.resolve()

        # ✅ ustaw ścieżki zanim _load_config_from_file() zacznie je dotykać
        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        # wczytaj values.yaml (może zmienić state_dir/state_file)
        self._load_config_from_file()

        # ✅ docelowe ścieżki po load (na wypadek zmian w values.yaml)
        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

        self._power: float = 0.0
        self._last_in_work: bool = False
        self._last_enabled: bool = bool(self._config.enabled)

        self._last_tick_ts: Optional[float] = None
        self._last_power_ts: Optional[float] = None

        self._boiler_f: Optional[float] = None
        self._flue_f: Optional[float] = None
        self._last_boiler_f: Optional[float] = None
        self._last_rate_ts: Optional[float] = None

        self._last_state_save_wall_ts: Optional[float] = None
        self._restored_state_meta: Optional[Dict[str, Any]] = None
        self._try_restore_state_from_disk()

        self._delta_universe = self._build_universe(
            self._config.delta_universe_min,
            self._config.delta_universe_max,
            self._config.delta_universe_step,
        )

    @property
    def id(self) -> str:
        return "power_work_fuzzy"

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
                    type="WORK_FUZZY_MODE_CHANGED",
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
                    type="WORK_FUZZY_ENABLED_CHANGED",
                    message=f"{self.id}: {'ENABLED' if enabled else 'DISABLED'}",
                    data={"enabled": enabled},
                )
            )

        if (not prev_in_work and in_work) or (not prev_enabled and enabled):
            self._last_power_ts = None

        if boiler_temp is not None and flue_temp is not None and self._restored_state_meta is not None:
            if not self._validate_restored_state(
                current_boiler_temp=float(boiler_temp),
                current_flue_temp=float(flue_temp),
                now_wall=now,
                events=events,
            ):
                self._reset_state()
            self._restored_state_meta = None

        self._update_filters(now_ctrl=now_ctrl, boiler_temp=boiler_temp, flue_temp=flue_temp)

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

        if self._boiler_f is None or self._flue_f is None:
            outputs.power_percent = self._power  # type: ignore[attr-defined]
            self._last_in_work = in_work
            self._last_enabled = enabled
            self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, flue_temp=flue_temp, events=events)
            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        err = float(self._config.boiler_set_temp) - float(self._boiler_f)
        rate = self._boiler_rate_degC_per_min(now_ctrl=now_ctrl)
        flue_f = float(self._flue_f)

        base_delta = self._mamdani_delta(err=err, rate=rate, flue=flue_f)
        delta = base_delta * float(self._config.delta_scale)

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

        if abs(self._power - prev_power) >= 5.0:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="WORK_FUZZY_POWER_CHANGED",
                    message=(
                        f"{self.id}: {prev_power:.1f}% → {self._power:.1f}% "
                        f"(T={float(self._boiler_f):.2f}°C, e={err:+.2f}°C, "
                        f"Tf={flue_f:.1f}°C, rate={rate:+.3f}°C/min)"
                    ),
                    data={
                        "prev_power": prev_power,
                        "power": self._power,
                        "boiler_f": float(self._boiler_f),
                        "err": err,
                        "flue_f": flue_f,
                        "rate_degC_per_min": rate,
                        "delta": float(delta),
                        "flue_weight": self._flue_weight(abs(err)),
                    },
                )
            )

        outputs.power_percent = self._power  # type: ignore[attr-defined]
        self._last_in_work = in_work
        self._last_enabled = enabled

        self._maybe_persist_state(now_wall=now, boiler_temp=boiler_temp, flue_temp=flue_temp, events=events)

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # -------- Mamdani core --------

    def _mamdani_delta(self, err: float, rate: float, flue: float) -> float:
        mu_e = self._fuzzify_error(err)
        mu_r = self._fuzzify_rate(rate)
        mu_f = self._fuzzify_flue(flue)

        w_flue = self._flue_weight(abs(err))

        rules: List[tuple[float, str]] = []

        # bazowe sterowanie błędem
        rules.append((mu_e["PB"], "UB"))
        rules.append((mu_e["PS"], "UM"))
        rules.append((mu_e["ZE"], "Z"))
        rules.append((mu_e["NS"], "DS"))
        rules.append((mu_e["NB"], "DB"))

        # rate-damping / antyprzeregulowanie
        rules.append((min(mu_e["PS"], mu_r["RISE"]), "DS"))
        rules.append((min(mu_e["ZE"], mu_r["RISE"]), "DM"))
        rules.append((min(mu_e["NS"], mu_r["RISE"]), "DB"))

        rules.append((min(mu_e["ZE"], mu_r["FALL"]), "US"))
        rules.append((min(mu_e["PS"], mu_r["FALL"]), "UM"))
        rules.append((min(mu_e["NS"], mu_r["FALL"]), "Z"))

        # reguły spalinowe – skalowane wagą zależną od |błędu|
        rules.append((w_flue * mu_f["VHIGH"], "DB"))
        rules.append((w_flue * min(mu_f["HIGH"], max(mu_e["ZE"], mu_e["NS"])), "DB"))
        rules.append((w_flue * min(mu_f["HIGH"], mu_e["PS"]), "DS"))

        rules.append((w_flue * min(mu_f["LOW"], mu_e["PB"]), "UB"))
        rules.append((w_flue * min(mu_f["LOW"], mu_e["PS"]), "UM"))
        rules.append((w_flue * min(mu_f["MID"], mu_e["PB"]), "UM"))
        rules.append((w_flue * min(mu_f["MID"], mu_e["PS"]), "US"))

        agg: List[float] = [0.0] * len(self._delta_universe)
        for strength, out_label in rules:
            if strength <= 0.0:
                continue
            for i, x in enumerate(self._delta_universe):
                mu_out = self._out_membership(out_label, x)
                v = strength if strength < mu_out else mu_out  # min()
                if v > agg[i]:
                    agg[i] = v  # max()

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
        if x < 0.0:
            x = 0.0
        elif x > 1.0:
            x = 1.0

        # smoothstep: 0..1 (gładkie przejście)
        s = x * x * (3.0 - 2.0 * x)

        near = float(c.flue_weight_near)
        far = float(c.flue_weight_far)

        w = near * (1.0 - s) + far * s

        if w < 0.0:
            w = 0.0
        if w > 5.0:
            w = 5.0
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

    # -------- filters / state --------

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
                self._flue_f = self._ema_update(self._flue_f, ft, dt, float(self._config.flue_tau_s))
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

    def _reset_state(self) -> None:
        self._power = 0.0
        self._last_tick_ts = None
        self._last_power_ts = None
        self._boiler_f = None
        self._flue_f = None
        self._last_boiler_f = None
        self._last_rate_ts = None

    # -------- membership primitives --------

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

    # -------- persist --------

    def _try_restore_state_from_disk(self) -> None:
        # ✅ zawsze licz ścieżki od persist_root (data_root/modules/<id>)
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
        flue_f = data.get("flue_f")

        if isinstance(power, (int, float)):
            self._power = float(power)

        if isinstance(boiler_f, (int, float)):
            self._boiler_f = float(boiler_f)
            self._last_boiler_f = float(boiler_f)

        if isinstance(flue_f, (int, float)):
            self._flue_f = float(flue_f)

        self._last_tick_ts = None
        self._last_power_ts = None
        self._last_rate_ts = None

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
                    type="WORK_FUZZY_STATE_RESTORED",
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
                type="WORK_FUZZY_STATE_RESTORE_SKIPPED",
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
                "flue_f": float(self._flue_f) if self._flue_f is not None else None,
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
                    type="WORK_FUZZY_STATE_SAVE_ERROR",
                    message=f"{self.id}: błąd zapisu stanu: {exc}",
                    data={"error": str(exc)},
                )
            )

    # -------- config io --------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "enabled" in values:
            self._config.enabled = bool(values["enabled"])

        for field_name in (
            "boiler_set_temp",
            "min_power",
            "max_power",
            "max_slew_rate_percent_per_min",
            "boiler_tau_s",
            "flue_tau_s",
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
        ):
            if field_name in values:
                setattr(self._config, field_name, float(values[field_name]))

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

        if "enabled" in data:
            self._config.enabled = bool(data["enabled"])

        for k, v in data.items():
            if k in ("state_dir", "state_file", "enabled"):
                continue
            try:
                if hasattr(self._config, k):
                    setattr(self._config, k, float(v))
            except Exception:
                pass

        if "state_dir" in data:
            self._config.state_dir = str(data["state_dir"])
        if "state_file" in data:
            self._config.state_file = str(data["state_file"])

        # ✅ ścieżki od persist_root (a nie od katalogu modułu)
        self._state_dir = (self._persist_root / self._config.state_dir).resolve()
        self._state_path = self._state_dir / self._config.state_file

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

