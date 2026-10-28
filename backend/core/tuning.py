"""
Tuning advice for the service page: how the furnace ran lately (from the
history CSV, the event log and the current settings) and what could be
changed - each finding has a status, the numbers behind it and a concrete
suggestion in Polish.

Pure functions on rows / events / settings, so it is easy to test; reading
the files is done by `TuningReport`.
"""
from __future__ import annotations

import csv
import json
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.analytics import _float, files_by_day, history_rows
from backend.core.settings_log import read_entries

SETTLE_S = 2 * 3600         # after an ignition the boiler swings for ~2 h - judged later
SETPOINT_STEP_C = 1.0       # a setpoint change at least this big ...
SETPOINT_SETTLE_S = 2 * 3600  # ... also needs ~2 h before it's judged (a hot boiler overshoots a lower setpoint)
SWING_STD = 1.5             # swing worth a suggestion (±°C) - a coal bed reacts slowly, ±1-1.5 is fine
SWING_BAD_STD = 2.5
MILD_SWING_STD = 1.0        # below SWING_STD but a regular wave above this: a hint (flue lead, then a softer PID)
LEAD_GOOD = 8.0             # "Wyprzedzanie spalinami" that settled the boiler twin
IGNITION_MIN_S = 300        # a shorter IGNITION is a click, not an ignition
WINDOW_H = 12               # regulation: the last 12 h of settled WORK (~9 waves of the PID; within EVENT_DAYS)
EVENT_DAYS = 7              # flame-outs, overheats, sensors, setting changes
MIN_WORK_H = 3.0            # less settled WORK than this - no verdict
SMOOTH_ROWS = 5             # ~2.5 min moving average before counting swings
SWING_HYST = 0.3            # a swing must cross the mean by this much
CHANGE_SIDE_H = 12          # setting change: compare 12 h before / after
CHANGE_SKIP_S = 30 * 60     # ... skipping the first 30 min after it
CHANGE_MIN_H = 2.0
WINTER_C = -15.0            # the curve is checked for this outside temperature
FROST_C = (-10.0, -15.0)    # power reserve of the curve checked for these
ROOM_C = 20.0               # rooms (no sensor)
RAD_DROP_C = 2.0            # radiator mean below the supply
RAD_EXP = 1.3               # radiator output ~ (mean - room) ^ 1.3

# after a change of these the PID is judged only from the work since then (+ CHANGE_SKIP_S):
# yesterday's wave says nothing about the new settings
PID_KEYS = {("power_work", k) for k in ("kp", "ki", "flue_brake_gain", "min_power", "max_power",
                                       "max_slew_rate_percent_per_min")} | {("feeder", "feed_on_base_s"),
                                                                            ("feeder", "feed_off_base_s")}

EVENT_KEYS = ("FLAMEOUT", "OVERHEAT_ON", "MISSING_ON", "SETTINGS_CHANGED", "API_ERROR", ";ERROR;")

# settings worth showing in the "what changed" list (module, key) -> label
TRACKED = {
    ("power_work", "boiler_set_temp"): "zadana kotła",
    ("power_work", "kp"): "Kp",
    ("power_work", "ki"): "Ki",
    ("power_work", "flue_brake_gain"): "wyprzedzanie spalinami",
    ("power_work", "min_power"): "moc min",
    ("power_work", "max_power"): "moc max",
    ("power_work", "max_slew_rate_percent_per_min"): "szybkość zmian mocy",
    ("feeder", "feed_on_base_s"): "podanie ślimaka",
    ("feeder", "feed_off_base_s"): "przerwa ślimaka",
    ("mode", "switch_temp"): "przejście rozpalanie → praca",
    ("mixer", "curve_at_0"): "krzywa: zadana przy 0°C",
    ("mixer", "curve_slope"): "krzywa: nachylenie",
    ("mixer", "target_temp"): "zadana grzejników",
    ("mixer", "curve_enabled"): "krzywa grzewcza",
}


def fmt(v: Optional[float], nd: int = 1) -> str:
    if v is None:
        return "--"
    s = f"{v:.{nd}f}"
    return s.replace(".", ",")


def fmt_num(v: float) -> str:
    """A setting value the way it is typed in the settings: 10, 0,005, 8,5."""
    if v == int(v):
        return str(int(v))
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _parse(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r.get("data_czas") or "")
        except ValueError:
            continue
        out.append({
            "ts": ts,
            "mode": (r.get("tryb_pracy") or "").upper(),
            "boiler": _float(r.get("temp_pieca")),
            "power": _float(r.get("power")),
            "rad": _float(r.get("temp_grzejnikow")),
            "outside": _float(r.get("temp_zewn")),
            "setpoint": _float(r.get("zadana")),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def settled(rows: List[Dict[str, Any]], fallback_setpoint: Optional[float]) -> List[Dict[str, Any]]:
    """Rows of WORK at least SETTLE_S after entering it and SETPOINT_SETTLE_S after a setpoint change,
    with a boiler reading and a setpoint."""
    out = []
    since: Optional[datetime] = None
    prev_ts: Optional[datetime] = None
    last_sp: Optional[float] = None
    sp_since: Optional[datetime] = None
    for r in rows:
        gap = prev_ts is not None and (r["ts"] - prev_ts).total_seconds() > 300
        prev_ts = r["ts"]
        if r["mode"] != "WORK" or gap:
            since = r["ts"] if r["mode"] == "WORK" else None
            continue
        if since is None:
            since = r["ts"]
        sp = r["setpoint"] if r["setpoint"] is not None else fallback_setpoint
        if sp is not None:
            if last_sp is not None and abs(sp - last_sp) >= SETPOINT_STEP_C:
                sp_since = r["ts"]
            last_sp = sp
        if r["boiler"] is None or sp is None or (r["ts"] - since).total_seconds() < SETTLE_S:
            continue
        if sp_since is not None and (r["ts"] - sp_since).total_seconds() < SETPOINT_SETTLE_S:
            continue
        out.append({**r, "setpoint": sp, "err": r["boiler"] - sp})
    return out


def regulation(rows: List[Dict[str, Any]], min_power: float, max_power: float) -> Optional[Dict[str, Any]]:
    """Bias, swing, swing period and power saturation of settled WORK rows (30 s apart)."""
    if not rows:
        return None
    errs = [r["err"] for r in rows]
    n = len(errs)
    mean = sum(errs) / n
    std = math.sqrt(sum((e - mean) ** 2 for e in errs) / n)
    powers = [r["power"] for r in rows if r["power"] is not None]
    # swings: crossings of the mean of the smoothed error, with a hysteresis
    smooth = [sum(errs[max(0, i - SMOOTH_ROWS + 1):i + 1]) / len(errs[max(0, i - SMOOTH_ROWS + 1):i + 1])
              for i in range(n)]
    side, crossings = 0, 0
    for e in smooth:
        s = 1 if e > mean + SWING_HYST else -1 if e < mean - SWING_HYST else 0
        if s and side and s != side:
            crossings += 1
        if s:
            side = s
    hours = n * 30 / 3600
    period_min = (2 * hours * 60 / crossings) if crossings >= 4 else None
    jumps = [abs(b["power"] - a["power"]) for a, b in zip(rows, rows[1:])
             if a["power"] is not None and b["power"] is not None and (b["ts"] - a["ts"]).total_seconds() <= 60]
    return {
        "hours": round(hours, 1),
        "mean_err": round(mean, 2),
        "std": round(std, 2),
        "within_2": round(100 * sum(1 for e in errs if abs(e) <= 2) / n),
        "max_over": round(max(errs), 1),
        "max_under": round(-min(errs), 1),
        "period_min": None if period_min is None else round(period_min),
        "power_avg": round(sum(powers) / len(powers)) if powers else None,
        "at_max_pct": round(100 * sum(1 for p in powers if p >= max_power - 1) / len(powers)) if powers else 0,
        "at_min_pct": round(100 * sum(1 for p in powers if p <= min_power + 1) / len(powers)) if powers else 0,
        "power_jump": round(sum(jumps) / len(jumps), 2) if jumps else None,
    }


def curve_target(cfg: Dict[str, Any], outside: Optional[float]) -> Optional[float]:
    if not cfg.get("curve_enabled") or outside is None:
        return _float(cfg.get("target_temp"))
    t = float(cfg.get("curve_at_0", 30)) - float(cfg.get("curve_slope", 0.45)) * outside
    return max(float(cfg.get("curve_min", 22)), min(float(cfg.get("curve_max", 45)), t))


def radiators(rows: List[Dict[str, Any]], mixer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Radiators against the target the mixer had (curve from the outside average)."""
    if not mixer:
        return None
    avg_h = float(mixer.get("curve_avg_h", 6) or 6)
    window: List[tuple] = []
    diffs: List[float] = []
    rads: List[float] = []
    outs: List[float] = []
    for r in rows:
        if r["outside"] is not None:
            window.append((r["ts"], r["outside"]))
        while window and (r["ts"] - window[0][0]).total_seconds() > avg_h * 3600:
            window.pop(0)
        outside = sum(v for _, v in window) / len(window) if window else None
        target = curve_target(mixer, outside)
        if r["rad"] is None or target is None:
            continue
        diffs.append(r["rad"] - target)
        rads.append(r["rad"])
        if r["outside"] is not None:
            outs.append(r["outside"])
    if len(diffs) < 240:
        return None
    n = len(diffs)
    return {
        "rad_avg": round(sum(rads) / len(rads), 1),
        "outside_avg": round(sum(outs) / len(outs), 1) if outs else None,
        "mean": round(sum(diffs) / n, 2),
        "mean_abs": round(sum(abs(d) for d in diffs) / n, 2),
        "within_1": round(100 * sum(1 for d in diffs if abs(d) <= 1) / n),
    }


def ignitions(rows: List[Dict[str, Any]], fallback_setpoint: Optional[float]) -> List[Dict[str, Any]]:
    """Every IGNITION -> WORK switch: how high the boiler went and how low it fell in the hour after."""
    out = []
    ign_since: Optional[datetime] = None
    for i in range(1, len(rows)):
        if rows[i - 1]["mode"] == "IGNITION" and (i < 2 or rows[i - 2]["mode"] != "IGNITION"):
            ign_since = rows[i - 1]["ts"]
        if not (rows[i]["mode"] == "WORK" and rows[i - 1]["mode"] == "IGNITION"):
            continue
        if ign_since is None or (rows[i]["ts"] - ign_since).total_seconds() < IGNITION_MIN_S:
            continue
        start = rows[i]["ts"]
        sp = rows[i]["setpoint"] if rows[i]["setpoint"] is not None else fallback_setpoint
        after = [r for r in rows[i:i + 130] if (r["ts"] - start).total_seconds() <= 3600
                 and r["mode"] == "WORK" and r["boiler"] is not None]
        if sp is None or len(after) < 60 or (rows[i]["boiler"] or 0) > sp + 1:
            continue  # switched by hand on an already hot boiler - not an ignition
        late = [r["boiler"] for r in after if (r["ts"] - start).total_seconds() >= 15 * 60]
        out.append({
            "at": start.isoformat(timespec="minutes"),
            "boiler_at_switch": rows[i]["boiler"],
            "setpoint": sp,
            "peak_over": round(max(r["boiler"] for r in after) - sp, 1),
            "dip_under": round(sp - min(late), 1) if late else None,
        })
    return out


def event_summary(events: List[Dict[str, str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"flameouts": [], "boiler_overheats": 0, "hopper_overheats": 0,
                           "sensor_missing": {}, "api_errors": 0, "module_errors": 0}
    for e in events:
        t = e.get("type") or ""
        if t == "FLAMEOUT":
            out["flameouts"].append(e.get("data_czas", "")[:16])
        elif t == "BOILER_OVERHEAT_ON":
            out["boiler_overheats"] += 1
        elif t == "HOPPER_OVERHEAT_ON":
            out["hopper_overheats"] += 1
        elif t.startswith("SENSOR_") and t.endswith("_MISSING_ON"):
            key = t[len("SENSOR_"):-len("_MISSING_ON")].lower()
            out["sensor_missing"][key] = out["sensor_missing"].get(key, 0) + 1
        elif t == "API_ERROR":
            out["api_errors"] += 1
        elif (e.get("level") or "").upper() == "ERROR":
            out["module_errors"] += 1
    return out


def setting_changes(events: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """SETTINGS_CHANGED entries of tracked settings, newest first."""
    out = []
    for e in events:
        if e.get("type") != "SETTINGS_CHANGED":
            continue
        try:
            data = json.loads(e.get("data_json") or "{}")
            ts = datetime.fromisoformat(e.get("data_czas") or "")
        except ValueError:
            continue
        module = data.get("module")
        for key, (old, new) in (data.get("changes") or {}).items():
            label = TRACKED.get((module, key))
            if label:
                out.append({"ts": ts, "module": module, "key": key, "label": label, "old": old, "new": new})
    out.sort(key=lambda c: c["ts"], reverse=True)
    return out


def log_changes(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Changes of tracked settings and of the app version from the settings history, newest first.
    The history also catches what the events miss: changes from before the events had them, a file
    edited by hand, a version update."""
    out: List[Dict[str, Any]] = []
    prev_version: Optional[str] = None
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        version = e.get("version")
        if prev_version is not None and version and version != prev_version:
            out.append({"ts": ts, "module": "app", "key": "version", "label": "wersja programu",
                        "old": prev_version, "new": version})
        prev_version = version or prev_version
        for module, keys in (e.get("changed") or {}).items():
            for key, pair in keys.items():
                label = TRACKED.get((module, key))
                if label and isinstance(pair, list) and len(pair) == 2 and pair[0] is not None:
                    out.append({"ts": ts, "module": module, "key": key, "label": label, "old": pair[0], "new": pair[1]})
    out.sort(key=lambda c: c["ts"], reverse=True)
    return out


def merge_changes(from_events: List[Dict[str, Any]], from_log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Both sources, one entry per change (the same setting to the same value within 5 min = one), newest first."""
    out = list(from_events)
    for c in from_log:
        if not any(o["module"] == c["module"] and o["key"] == c["key"] and o["new"] == c["new"]
                   and abs((o["ts"] - c["ts"]).total_seconds()) <= 300 for o in out):
            out.append(c)
    out.sort(key=lambda c: c["ts"], reverse=True)
    return out


def compare_change(change_ts: datetime, rows: List[Dict[str, Any]], min_power: float,
                   max_power: float, next_change: Optional[datetime] = None) -> Dict[str, Any]:
    before = [r for r in rows if change_ts - timedelta(hours=CHANGE_SIDE_H) <= r["ts"] < change_ts]
    end = change_ts + timedelta(hours=CHANGE_SIDE_H)
    if next_change is not None:
        end = min(end, next_change)
    after = [r for r in rows if change_ts + timedelta(seconds=CHANGE_SKIP_S) <= r["ts"] < end]

    def side(part):
        reg = regulation(part, min_power, max_power)
        if reg is None or reg["hours"] < CHANGE_MIN_H:
            return None
        return {k: reg[k] for k in ("hours", "mean_err", "std", "within_2", "power_avg")}

    b, a = side(before), side(after)
    verdict = None
    if b and a:
        score_b = b["within_2"] - 10 * b["std"]
        score_a = a["within_2"] - 10 * a["std"]
        verdict = "better" if score_a > score_b + 5 else "worse" if score_a < score_b - 5 else "same"
    return {"before": b, "after": a, "verdict": verdict}


# ---------------------------------------------------------------- advice

def advise(reg: Optional[Dict[str, Any]], rad: Optional[Dict[str, Any]], igns: List[Dict[str, Any]],
           ev: Dict[str, Any], s: Dict[str, Dict[str, Any]],
           pid_change: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Findings, PID first: integral (Ki), Kp, the feeder dose and the blower, then the rest."""
    items: List[Dict[str, str]] = []

    def add(area: str, status: str, title: str, detail: str, tip: str = "") -> None:
        items.append({"area": area, "status": status, "title": title, "detail": detail, "tip": tip})

    pw = s.get("power_work") or {}
    feeder = s.get("feeder") or {}
    blower = s.get("blower") or {}
    mixer = s.get("mixer") or {}
    kp, ki = _float(pw.get("kp")), _float(pw.get("ki"))
    lead = _float(pw.get("flue_brake_gain")) or 0.0     # "Wyprzedzanie spalinami", 0 = off
    sp = _float(pw.get("boiler_set_temp"))
    pmin, pmax = _float(pw.get("min_power")) or 0.0, _float(pw.get("max_power")) or 100.0
    feed_on = _float(feeder.get("feed_on_base_s"))
    fan = _float(blower.get("base_fan_percent"))
    feed_tip = 0  # the feeder dose suggested below: +1 / -1 s - the blower follows it

    if reg is None or reg["hours"] < MIN_WORK_H:
        if pid_change is not None:
            add("PID", "info", "Za mało pracy od zmiany ustawień",
                f"{pid_change['label']} zmienione {pid_change['ts']:%d.%m %H:%M} "
                f"({pid_change['old']} → {pid_change['new']}). Ocena liczy tylko pracę po zmianie: "
                f"jest {fmt(reg['hours'] if reg else 0)} h, potrzeba {fmt(MIN_WORK_H, 0)} h. Do tego czasu bez nowych sugestii.")
        else:
            add("PID", "info", "Za mało pracy do oceny",
                f"W ostatnich {EVENT_DAYS} dniach {fmt(reg['hours'] if reg else 0)} h ustabilizowanej pracy "
                f"(liczonej od 2 h po rozpaleniu i po zmianie zadanej; potrzeba {fmt(MIN_WORK_H, 0)} h). "
                "Ocena całki, Kp, ślimaka i dmuchawy pojawi się później.")
    else:
        m, std, period = reg["mean_err"], reg["std"], reg["period_min"]
        starved = m < -1.0 and reg["at_max_pct"] >= 25
        flooded = m > 1.0 and reg["at_min_pct"] >= 25
        slow_swing = std > SWING_STD and period is not None and period >= 60
        fast_swing = std > SWING_STD and not slow_swing

        # --- integral (Ki): holds the setpoint on average ---
        lower = f"{fmt_num(ki)} → {fmt_num(round(ki * 0.7, 4))}" if ki else ""
        if starved or flooded:
            add("Całka (Ki)", "ok", "Całka bez zmian",
                "Odchyłka od zadanej wynika z dawki ślimaka (moc stoi na granicy), nie z całki.")
        elif slow_swing:
            add("Całka (Ki)", "bad" if std > SWING_BAD_STD else "warn", "Wolne kołysanie - całka przestrzeliwuje",
                f"Rozrzut ±{fmt(std)}°C, jedno wahnięcie co ok. {period} min.",
                f"Zmniejsz Ki ({lower})." if ki else "Zmniejsz Ki.")
        elif m < -1.0:
            add("Całka (Ki)", "warn", "Kocioł trzyma się poniżej zadanej",
                f"Średnio {fmt(-m)}°C poniżej, a moc ma jeszcze zapas (średnio {reg['power_avg']}%).",
                f"Zwiększ Ki ({fmt_num(ki)} → {fmt_num(round(ki * 1.5, 4))})." if ki else "Zwiększ Ki.")
        elif m > 1.0:
            add("Całka (Ki)", "warn", "Kocioł trzyma się powyżej zadanej", f"Średnio {fmt(m)}°C powyżej.",
                f"Zmniejsz Ki ({lower})." if ki else "Zmniejsz Ki.")
        else:
            add("Całka (Ki)", "ok", "Całka trzyma zadaną",
                f"Średnio {fmt(m)}°C od zadanej, {reg['within_2']}% czasu w ±2°C.")

        # --- proportional gain (Kp): reacts to the deviation now ---
        jump = reg["power_jump"]
        if fast_swing:
            per = f", jedno wahnięcie co ok. {period} min" if period else ""
            add("Wzmocnienie (Kp)", "bad" if std > SWING_BAD_STD else "warn", "Szybkie kołysanie",
                f"Rozrzut ±{fmt(std)}°C (od +{fmt(reg['max_over'])} do −{fmt(reg['max_under'])}°C){per}.",
                f"Zmniejsz Kp o ok. 20% ({fmt_num(kp)} → {fmt_num(round(kp * 0.8, 1))})." if kp else "Zmniejsz Kp.")
        elif jump is not None and jump > 1.5:
            add("Wzmocnienie (Kp)", "warn", "Moc szarpie",
                f"Moc zmienia się średnio o {fmt(jump)} pkt co 30 s - ślimak i dmuchawa ciągle zmieniają rytm.",
                f"Zmniejsz Kp ({fmt_num(kp)} → {fmt_num(round(kp * 0.8, 1))})." if kp else "Zmniejsz Kp.")
        elif std < 0.5 and reg["max_under"] > 3:
            add("Wzmocnienie (Kp)", "warn", "Powolna reakcja na spadki",
                f"Na co dzień ±{fmt(std)}°C, ale kocioł potrafił spaść {fmt(reg['max_under'])}°C poniżej zadanej.",
                f"Zwiększ Kp o ok. 20% ({fmt_num(kp)} → {fmt_num(round(kp * 1.2, 1))})." if kp else "Zwiększ Kp.")
        elif MILD_SWING_STD < std <= SWING_STD and period is not None and period >= 40 and kp and ki:
            # within the norm, but a regular wave: the coal answers ~30 min late and the PID keeps
            # adding meanwhile. The flue sees the fire 10-20 min earlier - leading with it fixes the
            # cause (on the boiler twin it beat a softer PID); a softer PID only once it is on.
            why = (f"Rozrzut ±{fmt(std)}°C, regularnie co ok. {period} min: węgiel oddaje ciepło z opóźnieniem, "
                   "a regulator w tym czasie dokłada za dużo. Mieści się w normie.")
            if lead <= 0:
                add("Wzmocnienie (Kp)", "hint", "Lekkie falowanie - włącz wyprzedzanie spalinami",
                    why + " Spaliny pokazują ogień 10-20 min wcześniej niż woda w kotle - regulator zdąży "
                    "ująć albo dodać mocy przed przestrzałem. Kp i Ki zostaw, jedna zmiana naraz.",
                    "Włącz wyprzedzanie spalinami (0 → 5).")
            elif lead < LEAD_GOOD:
                add("Wzmocnienie (Kp)", "hint", "Lekkie falowanie - mocniejsze wyprzedzanie",
                    why + f" Wyprzedzanie spalinami jest na {fmt_num(lead)} - można mocniej.",
                    f"Zwiększ wyprzedzanie spalinami ({fmt_num(lead)} → {fmt_num(min(LEAD_GOOD, lead + 3))}).")
            else:
                add("Wzmocnienie (Kp)", "hint", "Lekkie falowanie - można złagodzić",
                    why + " Łagodniej = mniejsze falowanie i spokojniejsza moc, ale po kąpieli powrót do zadanej "
                    "o ok. 10-15 min dłuższy.",
                    f"Złagodź regulator (Kp {fmt_num(kp)} → {fmt_num(round(kp * 0.8, 1))}, "
                    f"Ki {fmt_num(ki)} → {fmt_num(round(ki * 0.8, 4))}).")
        else:
            add("Wzmocnienie (Kp)", "ok", "Kp w porządku",
                f"Rozrzut ±{fmt(std)}°C" + (f", moc zmienia się spokojnie ({fmt(jump)} pkt / 30 s)." if jump is not None else "."))

        # --- feeder dose: the PID works best around the middle of its range ---
        avg = reg["power_avg"]
        if starved or (avg is not None and avg > 75) or reg["at_max_pct"] >= 40:
            feed_tip = 1
            add("Ślimak", "bad" if starved else "warn", "Dawka węgla za mała",
                f"Moc średnio {avg}%, {reg['at_max_pct']}% czasu na maksimum"
                + (f" i kocioł {fmt(-m)}°C poniżej zadanej." if starved else " - przy większym mrozie zabraknie."),
                f"Wydłuż podanie ślimaka ({fmt_num(feed_on)} → {fmt_num(feed_on + 1)} s)." if feed_on else "Wydłuż podanie ślimaka.")
        elif flooded or (avg is not None and avg < 35) or reg["at_min_pct"] >= 40:
            feed_tip = -1
            add("Ślimak", "warn", "Dawka węgla za duża",
                f"Moc średnio {avg}%, {reg['at_min_pct']}% czasu na minimum - PID nie ma czym regulować w dół"
                + (f", kocioł {fmt(m)}°C ponad zadaną." if flooded else "."),
                f"Skróć podanie ślimaka ({fmt_num(feed_on)} → {fmt_num(feed_on - 1)} s)." if feed_on and feed_on > 3
                else "Skróć podanie ślimaka.")
        else:
            add("Ślimak", "ok", "Dawka dobrana",
                f"Moc średnio {avg}% - PID ma zapas w obie strony "
                f"(na maksimum {reg['at_max_pct']}%, na minimum {reg['at_min_pct']}% czasu).")

    # --- blower: air to the coal fed; no flue / O2 measurement, so mostly from the symptoms ---
    to_blow = _float(blower.get("min_power_to_blow"))
    to_feed = _float(feeder.get("min_power_to_feed")) or 0.0
    if to_blow is not None and to_feed < pmin <= to_blow:
        add("Dmuchawa", "bad", "Dmuchawa staje na mocy minimalnej",
            f"Przy mocy min PID ({fmt_num(pmin)}%) ślimak podaje węgiel, a dmuchawa stoi (wyłącza się do "
            f"{fmt_num(to_blow)}%) - węgiel bez powietrza.", f"Ustaw próg dmuchawy poniżej {fmt_num(pmin)}%.")
    elif feed_tip and fan:
        share = (feed_on + feed_tip) / feed_on if feed_on else 1.0
        add("Dmuchawa", "warn", "Dmuchawa razem ze ślimakiem",
            "Po zmianie dawki węgla zmień obroty dmuchawy w tej samej proporcji, żeby nie zmienić spalania.",
            f"Obroty {fmt_num(fan)} → {fmt_num(round(fan * share))}%.")
    elif ev["flameouts"] and fan:
        add("Dmuchawa", "warn", "Wygaszanie - możliwe za mało powietrza",
            f"Piec wygasł {len(ev['flameouts'])}× przy pracy. Częsta przyczyna: węgiel się dusi.",
            f"Podnieś obroty dmuchawy o 2-3% ({fmt_num(fan)} → {fmt_num(fan + 3)}%) i obserwuj popiół.")
    else:
        add("Dmuchawa", "info", "Oceń po płomieniu i popiele",
            f"Obroty {fmt_num(fan) if fan else '--'}%. Sterownik nie mierzy tlenu w spalinach, "
            "więc nie oceni powietrza sam.",
            "Dobrze: płomień żółto-pomarańczowy, spokojny, nad całym żarem, z komina prawie bez dymu, "
            "popiół jasny i sypki. "
            "Za mało powietrza: płomień ciemnopomarańczowy, leniwy, kopci, ciemny dym z komina, sadza na drzwiczkach, "
            "czarne kawałki węgla w popiele - dodaj 2-3%. "
            "Za dużo: płomień krótki, prawie biały, huczy, żar wypalony zaraz przy ślimaku, spieczony żużel, "
            "wysokie spaliny - odejmij 2-3%. Po zmianie odczekaj kilka godzin.")

    # --- ignition -> work ---
    if igns:
        worst = max(igns, key=lambda x: x["peak_over"])
        dips = [x for x in igns if x["dip_under"] is not None and x["dip_under"] > 4]
        if worst["peak_over"] > 3:
            add("Rozpalanie", "warn", "Przestrzał po rozpaleniu",
                f"{worst['at'][5:10].replace('-', '.')} {worst['at'][11:]}: po przejściu w pracę kocioł doszedł do "
                f"{fmt(worst['setpoint'] + worst['peak_over'])}°C (zadana {fmt(worst['setpoint'], 0)}).",
                "Obniż temperaturę przejścia rozpalanie → praca o 2°C.")
        elif dips:
            d = dips[0]
            add("Rozpalanie", "warn", "Spadek po rozpaleniu",
                f"{d['at'][5:10].replace('-', '.')} {d['at'][11:]}: godzinę po przejściu w pracę kocioł spadł "
                f"{fmt(d['dip_under'])}°C poniżej zadanej - żar jeszcze się nie rozszedł.",
                "Ustaw przejście w pracę bliżej zadanej albo wydłuż minimalny czas rozpalania.")
        else:
            add("Rozpalanie", "ok", "Przejście w pracę płynne",
                f"{len(igns)} rozpaleń w {EVENT_DAYS} dni, najwyżej +{fmt(worst['peak_over'])}°C ponad zadaną.")

    # --- radiators ---
    if rad is not None:
        if rad["mean_abs"] <= 1.0:
            add("Grzejniki", "ok", "Zawór trzyma temperaturę",
                f"Średnio {fmt(rad['mean_abs'])}°C od zadanej, {rad['within_1']}% czasu w ±1°C.")
        elif rad["mean"] < -1.0:
            add("Grzejniki", "warn", "Grzejniki chłodniejsze niż zadana",
                f"Średnio {fmt(-rad['mean'])}°C poniżej zadanej.",
                "Sprawdź, czy kocioł nie jest za zimny dla krzywej i czy zawór się w pełni otwiera.")
        else:
            add("Grzejniki", "warn", "Zawór nie nadąża",
                f"Średnio {fmt(rad['mean_abs'])}°C od zadanej (od strony {'ciepłej' if rad['mean'] > 0 else 'zimnej'}).",
                "Zmniejsz strefę martwą albo skróć odstęp między ruchami zaworu.")

    # --- heating curve: the radiators in frost and the power that takes ---
    if mixer.get("curve_enabled"):
        frost = [(t, curve_target(mixer, t)) for t in FROST_C]
        values = " • ".join(f"{fmt(t, 0)}°C → {fmt(v)}°C" for t, v in [(5.0, curve_target(mixer, 5.0)), (0.0, curve_target(mixer, 0.0))] + frost)
        need = None
        if reg is not None and rad is not None and reg.get("power_avg") and rad["rad_avg"] - RAD_DROP_C - ROOM_C >= 5 \
                and rad.get("outside_avg") is not None and rad["outside_avg"] <= 12:
            # radiators give off heat ~ (mean - room)^1.3: from today's power at today's supply
            base = rad["rad_avg"] - RAD_DROP_C - ROOM_C
            need = [(t, reg["power_avg"] * ((v - RAD_DROP_C - ROOM_C) / base) ** RAD_EXP) for t, v in frost]
        slope = _float(mixer.get("curve_slope"))
        if need and need[0][1] > pmax:
            # a rough model (rooms ~20 °C, no return sensor, today's power - high while the house
            # still warms up after the summer): a hint with the numbers, never a problem
            add("Krzywa", "hint", "Krzywa może być za stroma na mróz",
                f"Krzywa: {values}. Z dzisiejszej pracy ({reg['power_avg']}% mocy przy {fmt(rad['rad_avg'])}°C "
                f"na grzejnikach) przy {fmt(need[0][0], 0)}°C wychodzi ok. {need[0][1]:.0f}% mocy - więcej niż kocioł ma "
                f"({pmax:.0f}%). Szacunek zgrubny: na początku sezonu zawyżony, bo dom się jeszcze dogrzewa.",
                f"Jeśli w mrozy kocioł stoi na maksimum poniżej zadanej, zmniejsz nachylenie "
                f"({fmt_num(slope)} → {fmt_num(round(slope - 0.1, 2))})." if slope else "")
        elif need:
            add("Krzywa", "ok", "Krzywa w zasięgu kotła",
                f"Krzywa: {values}. Przy {fmt(need[0][0], 0)}°C ok. {need[0][1]:.0f}% mocy, "
                f"przy {fmt(need[1][0], 0)}°C ok. {need[1][1]:.0f}% (dziś {reg['power_avg']}% przy {fmt(rad['rad_avg'])}°C).")
        else:
            add("Krzywa", "info", "Krzywa grzewcza", f"Krzywa: {values}. Zapas mocy na mróz policzę przy chłodniejszej pogodzie.")

    # --- settings ---
    if mixer.get("curve_enabled") and sp is not None:
        winter = curve_target(mixer, WINTER_C)
        if winter is not None and winter > sp - 5:
            add("Ustawienia", "warn", "Krzywa a zadana kotła",
                f"Przy {fmt(WINTER_C, 0)}°C krzywa chce {fmt(winter)}°C na grzejnikach, a kocioł ma {fmt(sp, 0)}°C.",
                f"Na duże mrozy podnieś zadaną kotła do co najmniej {fmt(winter + 5, 0)}°C.")
    pump_co = _float((s.get("pump_co") or {}).get("boiler_on_temp"))
    if pump_co is not None and sp is not None and pump_co > sp - 3:
        add("Ustawienia", "bad", "Pompa CO włącza się za późno",
            f"Próg pompy {fmt(pump_co, 0)}°C przy zadanej kotła {fmt(sp, 0)}°C.",
            f"Ustaw próg pompy CO poniżej {fmt(sp - 5, 0)}°C.")
    switch = _float((s.get("mode") or {}).get("switch_temp"))
    if switch is not None and sp is not None and switch > sp:
        add("Ustawienia", "warn", "Przejście w pracę powyżej zadanej",
            f"Rozpalanie kończy się przy {fmt(switch, 0)}°C, zadana {fmt(sp, 0)}°C - po przejściu PID długo zbija moc.",
            f"Ustaw przejście na {fmt(sp - 2, 0)}°C.")
    trip = _float((s.get("overheat") or {}).get("boiler_trip_temp"))
    if (s.get("overheat") or {}).get("enabled") is False:
        add("Ustawienia", "bad", "Ochrona przed przegrzaniem wyłączona", "Moduł przegrzania jest wyłączony.",
            "Włącz go w Ustawieniach.")
    elif trip is not None and sp is not None and trip - sp < 6:
        add("Ustawienia", "warn", "Mały zapas do przegrzania",
            f"Próg przegrzania {fmt(trip, 0)}°C, zadana {fmt(sp, 0)}°C.", "Zostaw co najmniej 8°C zapasu.")
    if (s.get("flameout") or {}).get("enabled") is False:
        add("Ustawienia", "warn", "Wykrywanie wygaśnięcia wyłączone",
            "Po wygaśnięciu ślimak będzie podawał węgiel do zimnej retorty.", "Włącz moduł wygasania.")

    # --- events of the last days ---
    if ev["flameouts"]:
        add("Zdarzenia", "bad", f"Wygasł {len(ev['flameouts'])}×",
            "Ostatnio: " + ", ".join(x[5:10].replace("-", ".") + " " + x[11:] for x in ev["flameouts"][-3:]) + ".",
            "Sprawdź węgiel (wilgotny, spieczony), czy ślimak nie jest zablokowany i czy nie trzeba wybrać popiołu.")
    if ev["boiler_overheats"]:
        add("Zdarzenia", "warn", f"Przegrzanie kotła {ev['boiler_overheats']}×", "Zadziałała ochrona przed przegrzaniem.",
            "Zwykle po nagłym zamknięciu grzejników: rozważ niższą moc min.")
    if ev["hopper_overheats"]:
        add("Zdarzenia", "bad", f"Przegrzanie podajnika {ev['hopper_overheats']}×", "Możliwe cofanie żaru do zasobnika.",
            "Sprawdź uszczelnienie zasobnika i czy przerwy ślimaka nie są za długie.")
    names = {"boiler_temp": "kotła", "radiators_temp": "grzejników", "hopper_temp": "podajnika", "flue_gas_temp": "spalin"}
    for key, n in sorted(ev["sensor_missing"].items()):
        add("Zdarzenia", "warn", f"Czujnik {names.get(key, key)} gubił odczyt", f"{n}× w {EVENT_DAYS} dni.",
            "Sprawdź przewód i złącze czujnika.")
    if not (ev["flameouts"] or ev["boiler_overheats"] or ev["hopper_overheats"] or ev["sensor_missing"]):
        add("Zdarzenia", "ok", "Bez awarii", f"Bez wygaśnięć, przegrzań i zgubionych czujników przez {EVENT_DAYS} dni.")
    return items


def verdict(items: List[Dict[str, str]]) -> Dict[str, Any]:
    bad = sum(1 for i in items if i["status"] == "bad")
    warn = sum(1 for i in items if i["status"] == "warn")
    if bad:
        return {"status": "bad", "text": f"Do poprawy: {bad + warn}", "bad": bad, "warn": warn}
    if warn:
        return {"status": "warn", "text": f"Sugestie: {warn}", "bad": 0, "warn": warn}
    return {"status": "ok", "text": "Wszystko w porządku", "bad": 0, "warn": 0}


# ---------------------------------------------------------------- files

class TuningReport:
    """
    Reads the files for `build`. Past days don't change, so each is read once
    and kept in memory (~1 MB a day); later builds read only today's files -
    a fraction of a second on the Pi instead of seconds.
    """

    def __init__(self, history_dir: Path, events_dir: Path, settings_dir: Optional[Path] = None) -> None:
        self.history_dir = history_dir
        self.events_dir = events_dir
        self.settings_dir = settings_dir
        self._days: Dict[str, tuple] = {}   # "YYYY-MM-DD" -> (history rows, events) of a past day
        self.progress: Dict[str, int] = {"done": 0, "total": 0}

    def _day_events(self, paths: List[Path]) -> List[Dict[str, str]]:
        # the log has thousands of feeder / blower lines a day - only the
        # lines with the wanted types are parsed
        out: List[Dict[str, str]] = []
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").replace("\0", "").splitlines()
            except OSError:
                continue
            if not lines:
                continue
            wanted = [lines[0]] + [ln for ln in lines[1:] if any(k in ln for k in EVENT_KEYS)]
            if len(wanted) > 1:
                out.extend(csv.DictReader(wanted, delimiter=";"))
        return out

    def _load(self, first: date, today: date, pause_s: float = 0.0) -> tuple:
        # the directories are listed once (thousands of files on the Pi), not globbed per day
        hist_ix = files_by_day(self.history_dir, "boiler")
        ev_ix = files_by_day(self.events_dir, "events")
        rows: List[Dict[str, Any]] = []
        events: List[Dict[str, str]] = []
        days = (today - first).days + 1
        self.progress = {"done": 0, "total": days}
        for i in range(days):
            d = first + timedelta(days=i)
            key = d.isoformat()
            if d < today and key in self._days:
                day_rows, day_events = self._days[key]
            else:
                day_rows = _parse(history_rows(self.history_dir, d, index=hist_ix))
                day_events = self._day_events(ev_ix.get(f"{d:%Y%m%d}", []))
                if d < today:
                    self._days[key] = (day_rows, day_events)
                if pause_s:
                    time.sleep(pause_s)   # leave the CPU to the control loop
            rows.extend(day_rows)
            events.extend(day_events)
            self.progress["done"] = i + 1
        for key in [k for k in self._days if k < first.isoformat()]:
            del self._days[key]
        rows.sort(key=lambda r: r["ts"])
        return rows, events

    def build(self, settings: Dict[str, Dict[str, Any]], now: Optional[datetime] = None,
              pause_s: float = 0.0) -> Dict[str, Any]:
        now = now or datetime.now()
        first = (now - timedelta(days=EVENT_DAYS)).date()
        all_rows, events = self._load(first, now.date(), pause_s)
        rows = [r for r in all_rows if r["ts"] >= now - timedelta(days=EVENT_DAYS)]
        pw = settings.get("power_work") or {}
        sp = _float(pw.get("boiler_set_temp"))
        pmin, pmax = _float(pw.get("min_power")) or 0.0, _float(pw.get("max_power")) or 100.0

        steady = settled(rows, sp)
        recent = steady[-int(WINDOW_H * 3600 / 30):]
        from_log = log_changes(read_entries(self.settings_dir, now - timedelta(days=EVENT_DAYS))) \
            if self.settings_dir is not None else []
        changes = merge_changes(setting_changes(events), from_log)[:10]
        # a PID setting changed within the window: judge only the work after it
        pid_change = next((c for c in changes if (c["module"], c["key"]) in PID_KEYS), None)
        if pid_change is not None and recent and pid_change["ts"] + timedelta(seconds=CHANGE_SKIP_S) > recent[0]["ts"]:
            recent = [r for r in recent if r["ts"] >= pid_change["ts"] + timedelta(seconds=CHANGE_SKIP_S)]
        else:
            pid_change = None
        reg = regulation(recent, pmin, pmax)
        rad = radiators(recent, settings.get("mixer") or {})
        igns = ignitions(rows, sp)
        ev = event_summary(events)
        items = advise(reg, rad, igns, ev, settings, pid_change)

        out_changes = []
        for i, c in enumerate(changes):
            newer = changes[i - 1]["ts"] if i > 0 else None
            out_changes.append({
                "at": c["ts"].isoformat(timespec="minutes"), "label": c["label"],
                "old": c["old"], "new": c["new"],
                **compare_change(c["ts"], steady, pmin, pmax, newer),
            })
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "verdict": verdict(items),
            "regulation": reg,
            "regulation_since": recent[0]["ts"].isoformat(timespec="minutes") if recent else None,
            # judged only after this PID setting change
            "pid_change": None if pid_change is None else {"label": pid_change["label"],
                                                           "at": pid_change["ts"].isoformat(timespec="minutes")},
            "radiators": rad,
            "ignitions": igns[-5:],
            "items": items,
            "changes": out_changes,
        }
