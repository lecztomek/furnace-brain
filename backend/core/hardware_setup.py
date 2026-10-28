"""
Which GPIO drives which output and which DS18B20 is which sensor - set in
Ustawienia -> Sprzęt (modules/hardware/values.yaml) instead of main.py.
The defaults are the wiring the furnace had when this was hard-coded.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# output -> setting key, default BCM GPIO (pin on the header)
OUTPUT_PINS = {
    "feeder": ("pin_feeder", 16),          # pin 36
    "pump_cwu": ("pin_pump_cwu", 20),      # pin 38
    "pump_co": ("pin_pump_co", 21),        # pin 40
    "mixer_open": ("pin_mixer_open", 6),   # pin 31
    "mixer_close": ("pin_mixer_close", 12),  # pin 32
}
# the blower is not a relay: its speed is the PWM duty on GPIO19 (pin 35) to the YYAC-3S
# module - fixed, not in the settings; the other outputs may not use it
FAN_PWM_PIN = 19

# Sensors field -> setting key, default DS18B20 ROM id ("" = none)
SENSOR_ROMS = {
    "boiler_temp": ("sensor_boiler", "28-000000506777"),
    "hopper_temp": ("sensor_hopper", "28-00000052b809"),
    "radiators_temp": ("sensor_radiators", "28-0000006f0738"),
    "cwu_temp": ("sensor_cwu", ""),
    "return_temp": ("sensor_return", ""),
    "mixer_temp": ("sensor_mixer", ""),
}

# GPIOs on the 40-pin header that are safe as outputs (BCM). Left out: 0/1 (ID EEPROM),
# 2/3 (I2C, pulled HIGH at boot - a relay there would click on while the Pi starts),
# 4 (1-Wire), 7-11 (SPI of the MAX6675), 14/15 (serial console)
USABLE_GPIO = [5, 6, 12, 13, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]

LABELS = {
    "pin_feeder": "ślimak", "pin_pump_cwu": "pompa CWU", "pin_pump_co": "pompa CO",
    "pin_mixer_open": "zawór - otwórz", "pin_mixer_close": "zawór - zamknij", "pin_fan_pwm": "dmuchawa (PWM)",
    "sensor_boiler": "kocioł", "sensor_hopper": "podajnik", "sensor_radiators": "grzejniki",
    "sensor_cwu": "zasobnik CWU", "sensor_return": "powrót", "sensor_mixer": "za zaworem",
}


def defaults() -> Dict[str, Any]:
    out: Dict[str, Any] = {key: pin for key, pin in OUTPUT_PINS.values()}
    out.update({key: rom for key, rom in SENSOR_ROMS.values()})
    out["fan_inverted"] = False
    return out


def _pin(values: Dict[str, Any], key: str, default: int) -> Any:
    v = values.get(key, default)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return v


def validate(values: Dict[str, Any]) -> List[str]:
    """Problems in Polish (shown in the GUI); an empty list = fine."""
    errors: List[str] = []
    used: Dict[int, str] = {FAN_PWM_PIN: "pin_fan_pwm"}
    for name, (key, default) in OUTPUT_PINS.items():
        pin = _pin(values, key, default)
        if not isinstance(pin, int) or pin not in USABLE_GPIO:
            errors.append(f"{LABELS[key]}: GPIO {pin} nie nadaje się na wyjście (dozwolone: {', '.join(map(str, USABLE_GPIO))})")
            continue
        if pin in used:
            errors.append(f"GPIO {pin} przypisany dwa razy: {LABELS[used[pin]]} i {LABELS[key]}")
        used[pin] = key
    seen: Dict[str, str] = {}
    for field, (key, _) in SENSOR_ROMS.items():
        rom = str(values.get(key) or "").strip()
        if not rom:
            continue
        if not rom.startswith("28-") or len(rom) < 6:
            errors.append(f"{LABELS[key]}: '{rom}' to nie numer czujnika DS18B20 (np. 28-000000506777)")
        elif rom in seen:
            errors.append(f"Czujnik {rom} przypisany dwa razy: {LABELS[seen[rom]]} i {LABELS[key]}")
        seen[rom] = key
    if not str(values.get(SENSOR_ROMS["boiler_temp"][0]) or "").strip():
        errors.append("Czujnik kotła musi być przypisany")
    return errors


def pins(values: Dict[str, Any]) -> Dict[str, int]:
    """Output name -> BCM GPIO (with the fixed fan PWM pin)."""
    out = {name: _pin(values, key, default) for name, (key, default) in OUTPUT_PINS.items()}
    out["fan_pwm"] = FAN_PWM_PIN
    return out


def rom_to_field(values: Dict[str, Any]) -> Dict[str, str]:
    """DS18B20 ROM id -> Sensors field, only the assigned ones."""
    out: Dict[str, str] = {}
    for field, (key, default) in SENSOR_ROMS.items():
        rom = str(values.get(key, default) or "").strip()
        if rom:
            out[rom] = field
    return out


def load(values: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """The settings to use: the saved ones when valid, else the defaults (and why)."""
    merged = {**defaults(), **(values or {})}
    errors = validate(merged)
    return (merged, []) if not errors else (defaults(), errors)
