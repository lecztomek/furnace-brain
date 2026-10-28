"""Ustawienia -> Sprzęt: which GPIO drives which output, which DS18B20 is which sensor.
An update must never change what the outputs drive; a swap must work at once and safely."""
import shutil
import sys
import threading
import types
from pathlib import Path

import pytest
import yaml

from backend.core import hardware_setup as H
from backend.core.config_store import ConfigStore
from backend.core.state import Outputs

MODULES = Path(__file__).resolve().parents[1] / "backend" / "modules"


def test_defaults_are_the_wiring_that_was_hard_coded():
    # main.py before this setting: feeder 16, CWU 20, CO 21, valve open 6 / close 12, fan PWM 19
    assert H.pins(H.defaults()) == {"feeder": 16, "pump_cwu": 20, "pump_co": 21,
                                    "mixer_open": 6, "mixer_close": 12, "fan_pwm": 19}
    assert H.rom_to_field(H.defaults()) == {"28-000000506777": "boiler_temp", "28-00000052b809": "hopper_temp",
                                            "28-0000006f0738": "radiators_temp"}
    assert H.validate(H.defaults()) == []
    # the schema shows the same defaults, the sample file too
    schema = yaml.safe_load((MODULES / "hardware" / "schema.yaml").read_text(encoding="utf-8"))
    assert {f["key"]: f["default"] for f in schema["fields"]} == H.defaults()
    assert yaml.safe_load((MODULES / "hardware" / "values.yaml.sample").read_text(encoding="utf-8")) == H.defaults()


def test_swapped_pumps_are_fine_wrong_setups_are_not():
    swapped = dict(H.defaults(), pin_pump_co=20, pin_pump_cwu=21)
    assert H.validate(swapped) == [] and H.pins(swapped)["pump_co"] == 20
    assert "dwa razy" in H.validate(dict(H.defaults(), pin_pump_co=16))[0]           # same GPIO as the feeder
    assert "nie nadaje się" in H.validate(dict(H.defaults(), pin_feeder=4))[0]       # the 1-Wire bus
    assert "nie nadaje się" in H.validate(dict(H.defaults(), pin_feeder=3))[0]       # I2C, HIGH at boot
    assert "PWM" in H.validate(dict(H.defaults(), pin_fan_pwm=20, pin_pump_cwu=5))[0]
    assert "dwa razy" in H.validate(dict(H.defaults(), sensor_cwu="28-0000006f0738"))[0]
    assert "kotła" in H.validate(dict(H.defaults(), sensor_boiler=""))[0]
    assert "nie numer" in H.validate(dict(H.defaults(), sensor_cwu="abc"))[0]


def test_load_falls_back_to_the_defaults():
    assert H.load({}) == (H.defaults(), [])                                          # first start after the update
    values, problems = H.load({"pin_pump_co": 21.0, "pin_pump_cwu": 20.0})          # numbers saved as floats
    assert problems == [] and H.pins(values)["pump_co"] == 21
    values, problems = H.load({"pin_pump_co": 16})                                   # broken: the old wiring
    assert values == H.defaults() and problems


def _store(tmp_path):
    shutil.copytree(MODULES / "hardware", tmp_path / "hardware", ignore=shutil.ignore_patterns("values.yaml", "__pycache__"))
    return ConfigStore(tmp_path, ["hardware"], validators={"hardware": H.validate})


def test_the_settings_page_refuses_a_wrong_setup(tmp_path):
    store = _store(tmp_path)
    store.fill_missing_defaults()
    before = (tmp_path / "hardware" / "values.yaml").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="dwa razy"):
        store.set_values("hardware", {"pin_pump_co": 16})
    with pytest.raises(ValueError, match="dwa razy"):
        store.set_value("hardware", "pin_pump_cwu", 21)                             # one field: CO is still on 21
    assert (tmp_path / "hardware" / "values.yaml").read_text(encoding="utf-8") == before   # untouched
    saved = store.set_values("hardware", {"pin_pump_co": 20, "pin_pump_cwu": 21})   # a real swap
    assert H.pins(saved)["pump_co"] == 20 and H.pins(saved)["pump_cwu"] == 21


# ---------- the Raspberry Pi side, on a fake RPi.GPIO that records every pin change ----------

class FakeGPIO(types.ModuleType):
    BCM, OUT, HIGH, LOW = "BCM", "OUT", 1, 0

    def __init__(self):
        super().__init__("RPi.GPIO")
        self.level = {}
        self.log = []

    def setmode(self, m): pass
    def setwarnings(self, w): pass

    def setup(self, pin, mode, initial=0):
        self.level[pin] = initial
        self.log.append(("setup", pin, initial))

    def output(self, pin, value):
        assert pin in self.level, f"GPIO {pin} written before setup"
        self.level[pin] = value
        self.log.append(("out", pin, value))

    def cleanup(self): pass

    class PWM:
        def __init__(self, pin, hz): self.pin = pin
        def start(self, d): pass
        def ChangeDutyCycle(self, d): pass
        def stop(self): pass


@pytest.fixture
def rpi(tmp_path, monkeypatch):
    gpio = FakeGPIO()
    pkg = types.ModuleType("RPi")
    pkg.GPIO = gpio
    monkeypatch.setitem(sys.modules, "RPi", pkg)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    monkeypatch.setitem(sys.modules, "pigpio", None)     # no pigpio: RPi.GPIO PWM
    monkeypatch.setitem(sys.modules, "spidev", None)     # no MAX6675
    from backend.hw.rpi_hw import Ds18b20Config, HardwareConfig, PinConfig, RpiHardware
    w1 = tmp_path / "w1"
    for rom, milli in (("28-000000506777", 56250), ("28-00000052b809", 29500), ("28-0000006f0738", 30125),
                       ("28-0000aaaa0001", 48000)):
        (w1 / rom).mkdir(parents=True)
        (w1 / rom / "w1_slave").write_text(f"aa bb : crc=00 YES\naa bb t={milli}\n")
    p = H.pins(H.defaults())
    cfg = HardwareConfig(
        ds18b20=Ds18b20Config(rom_to_field=H.rom_to_field(H.defaults()), base_path=str(w1)),
        max6675=None,
        pins=PinConfig(feeder_pin=p["feeder"], pump_cwu_pin=p["pump_cwu"], pump_co_pin=p["pump_co"],
                       mixer_open_pin=p["mixer_open"], mixer_close_pin=p["mixer_close"], fan_pwm_pin=p["fan_pwm"]),
        sensors_poll_interval_s=0.5)
    hw = RpiHardware(cfg)
    yield hw, gpio
    hw.close()


def test_outputs_go_to_the_assigned_pins(rpi):
    hw, gpio = rpi
    hw.apply_outputs(Outputs(pump_co_on=True, feeder_on=True))
    assert gpio.level[21] == 1 and gpio.level[16] == 1 and gpio.level[20] == 0      # CO on GPIO21, as before


def test_swapping_the_pumps_at_once(rpi):
    hw, gpio = rpi
    on = Outputs(pump_co_on=True, feeder_on=True)
    hw.apply_outputs(on)
    gpio.log.clear()
    swapped = dict(H.defaults(), pin_pump_co=20, pin_pump_cwu=21)
    hw.reconfigure(H.pins(swapped), H.rom_to_field(swapped))
    # first everything LOW - nothing runs on a wrong pin even for a moment
    assert all(v == 0 for (kind, _, v) in gpio.log if kind == "out") and gpio.level[16] == 0
    hw.apply_outputs(on)                                                              # the next control step
    assert gpio.level[20] == 1 and gpio.level[21] == 0 and gpio.level[16] == 1      # CO now on GPIO20
    hw.apply_outputs(Outputs(pump_cwu_on=True))
    assert gpio.level[21] == 1 and gpio.level[20] == 0 and gpio.level[16] == 0


def test_a_new_pin_is_set_up_before_use(rpi):
    hw, gpio = rpi
    moved = dict(H.defaults(), pin_pump_co=26)
    hw.reconfigure(H.pins(moved), H.rom_to_field(moved))
    assert ("setup", 26, 0) in gpio.log
    hw.apply_outputs(Outputs(pump_co_on=True))
    assert gpio.level[26] == 1 and gpio.level[21] == 0                              # the old pin stays LOW


def test_swap_during_a_running_control_loop(rpi):
    """The GUI saves while the control loop sets the outputs every few ms: the pump still ends up on."""
    hw, gpio = rpi
    on = Outputs(pump_co_on=True)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            hw.apply_outputs(on)

    t = threading.Thread(target=loop)
    t.start()
    swapped = dict(H.defaults(), pin_pump_co=20, pin_pump_cwu=21)
    for i in range(50):
        values = swapped if i % 2 == 0 else H.defaults()
        hw.reconfigure(H.pins(values), H.rom_to_field(values))
    stop.set()
    t.join()
    hw.apply_outputs(on)
    # after the last reconfigure (defaults): CO on GPIO21, nothing left on 20
    assert gpio.level[21] == 1 and gpio.level[20] == 0


def test_sensor_assignment_and_inventory(rpi):
    hw, _ = rpi
    import time as _t
    _t.sleep(1.2)                                                                      # one poll
    s = hw.read_sensors()
    assert s.boiler_temp == 56.25 and s.radiators_temp == 30.125 and s.cwu_temp is None
    found = {x["rom"]: x for x in hw.detected_sensors()}
    assert found["28-0000aaaa0001"]["field"] is None and found["28-0000aaaa0001"]["temp"] == 48.0   # unassigned
    assert found["28-000000506777"]["field"] == "boiler_temp"
    # the spare sensor becomes the CWU tank
    values = dict(H.defaults(), sensor_cwu="28-0000aaaa0001")
    hw.reconfigure(H.pins(values), H.rom_to_field(values))
    _t.sleep(1.2)
    assert hw.read_sensors().cwu_temp == 48.0
    # a number that is not on the bus shows as missing
    values = dict(H.defaults(), sensor_return="28-00000000dead")
    hw.reconfigure(H.pins(values), H.rom_to_field(values))
    assert any(x.get("missing") and x["rom"] == "28-00000000dead" for x in hw.detected_sensors())
