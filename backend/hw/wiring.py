"""
Ustawienia -> Sprzęt: the physical outputs and sensors, each with the function it has.
Outputs: the relay channels of the harness (GPIO -> function). Sensors: up to SLOTS
DS18B20 numbers, each with a role. The defaults are the wiring that was hard-coded in
main.py, so an update never changes what the outputs drive. The blower is not a relay -
its speed is the PWM duty on GPIO19 to the YYAC-3S module (fixed, not a setting).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

log = logging.getLogger(__name__)

# function -> its name in the GUI (each relay function on exactly one output)
FUNCTIONS = {
    "feeder": "ślimak",
    "pump_cwu": "pompa CWU",
    "pump_co": "pompa CO",
    "mixer_open": "zawór - otwórz",
    "mixer_close": "zawór - zamknij",
}
UNUSED = "nieużywane"

# the relay outputs of the harness: (BCM GPIO, header pin, function it had when hard-coded)
OUTPUTS = [(16, 36, "feeder"), (20, 38, "pump_cwu"), (21, 40, "pump_co"), (6, 31, "mixer_open"), (12, 32, "mixer_close")]
FAN_PWM_PIN = 19   # pin 35, PWM to the YYAC-3S

# sensor role -> (Sensors field, name in the GUI)
ROLES = {
    "kocioł": "boiler_temp",
    "podajnik": "hopper_temp",
    "grzejniki": "radiators_temp",
    "zasobnik CWU": "cwu_temp",
    "powrót": "return_temp",
    "za zaworem": "mixer_temp",
}
NO_ROLE = "nieużywany"
SLOTS = 6
DEFAULT_SENSORS = [("28-000000506777", "kocioł"), ("28-00000052b809", "podajnik"), ("28-0000006f0738", "grzejniki")]


def out_key(gpio: int) -> str:
    return f"gpio{gpio}"


def defaults() -> Dict[str, Any]:
    out: Dict[str, Any] = {out_key(g): FUNCTIONS[f] for g, _, f in OUTPUTS}
    for i in range(SLOTS):
        rom, role = DEFAULT_SENSORS[i] if i < len(DEFAULT_SENSORS) else ("", NO_ROLE)
        out[f"sensor{i + 1}"] = rom
        out[f"sensor{i + 1}_role"] = role
    out["fan_inverted"] = False
    return out


def validate(values: Dict[str, Any]) -> List[str]:
    """Problems in Polish (shown in the GUI); an empty list = fine."""
    errors: List[str] = []
    by_name = {v: k for k, v in FUNCTIONS.items()}
    where: Dict[str, List[str]] = {f: [] for f in FUNCTIONS}
    for gpio, _, _ in OUTPUTS:
        name = str(values.get(out_key(gpio), UNUSED))
        if name == UNUSED:
            continue
        if name not in by_name:
            errors.append(f"GPIO{gpio}: nieznana funkcja '{name}'")
            continue
        where[by_name[name]].append(f"GPIO{gpio}")
    for f, gpios in where.items():
        if len(gpios) > 1:
            errors.append(f"{FUNCTIONS[f]} na kilku wyjściach naraz: {', '.join(gpios)}")
        elif not gpios:
            errors.append(f"{FUNCTIONS[f]} nie ma wyjścia")
    roms: Dict[str, int] = {}
    roles: Dict[str, int] = {}
    for i in range(1, SLOTS + 1):
        rom = str(values.get(f"sensor{i}") or "").strip()
        role = str(values.get(f"sensor{i}_role") or NO_ROLE)
        if role != NO_ROLE and role not in ROLES:
            errors.append(f"Czujnik {i}: nieznana funkcja '{role}'")
            continue
        if role == NO_ROLE:
            continue
        if not rom:
            errors.append(f"Czujnik {i} ({role}): wybierz numer czujnika")
            continue
        if not rom.startswith("28-") or len(rom) < 6:
            errors.append(f"Czujnik {i}: '{rom}' to nie numer czujnika DS18B20")
        if rom in roms:
            errors.append(f"Czujnik {rom} wybrany dwa razy: miejsca {roms[rom]} i {i}")
        roms[rom] = i
        if role in roles:
            errors.append(f"Funkcja '{role}' przypisana dwa razy: czujniki {roles[role]} i {i}")
        roles[role] = i
    if "kocioł" not in roles:
        errors.append("Czujnik kotła musi być przypisany")
    return errors


def pins(values: Dict[str, Any]) -> Dict[str, int]:
    """Function -> BCM GPIO (with the fixed fan PWM pin). Call on validated values."""
    by_name = {v: k for k, v in FUNCTIONS.items()}
    out = {by_name[str(values.get(out_key(g)))]: g for g, _, _ in OUTPUTS if str(values.get(out_key(g))) in by_name}
    out["fan_pwm"] = FAN_PWM_PIN
    return out


def rom_to_field(values: Dict[str, Any]) -> Dict[str, str]:
    """DS18B20 ROM id -> Sensors field, only the assigned ones."""
    out: Dict[str, str] = {}
    for i in range(1, SLOTS + 1):
        rom = str(values.get(f"sensor{i}") or "").strip()
        role = str(values.get(f"sensor{i}_role") or NO_ROLE)
        if rom and role in ROLES:
            out[rom] = ROLES[role]
    return out


def load(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """The settings to use: the saved ones when valid, else the defaults (and why)."""
    merged = {**defaults(), **(values or {})}
    errors = validate(merged)
    return (merged, []) if not errors else (defaults(), errors)


def load_file(path: Path) -> Tuple[Dict[str, Any], List[str]]:
    """The wiring at the start: values.yaml of the hardware module, or the original wiring
    when it is missing (first start after an update) or broken - an update never changes
    what the outputs drive."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("Hardware settings unreadable (%s) - using the default wiring", exc)
        raw = {}
    values, problems = load(raw if isinstance(raw, dict) else {})
    for p in problems:
        log.error("Hardware settings: %s - using the default wiring", p)
    return values, problems


def rpi_config(values: Dict[str, Any]):
    """HardwareConfig of the Raspberry Pi from the wiring settings."""
    from backend.hw.rpi_hw import Ds18b20Config, HardwareConfig, Max6675Config, PinConfig
    p = pins(values)
    return HardwareConfig(
        ds18b20=Ds18b20Config(rom_to_field=rom_to_field(values), base_path="/sys/bus/w1/devices"),
        # flue gas thermocouple (MAX6675) on CE0 -> spidev0.0
        max6675=Max6675Config(spi_bus=0, spi_dev=0, max_hz=4_000_000, mode=0),
        pins=PinConfig(feeder_pin=p["feeder"], pump_cwu_pin=p["pump_cwu"], pump_co_pin=p["pump_co"],
                       mixer_open_pin=p["mixer_open"], mixer_close_pin=p["mixer_close"],
                       fan_pwm_pin=p["fan_pwm"], fan_pwm_freq_hz=200),   # blower: YYAC-3S
        fan_inverted=bool(values.get("fan_inverted", False)),
        sensors_poll_interval_s=5.0,
        sensors_stale_after_s=20.0,
    )
