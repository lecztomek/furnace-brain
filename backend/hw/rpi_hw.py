from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from backend.core.state import Sensors, Outputs

log = logging.getLogger(__name__)


# =========================
# Konfiguracja sprzętu
# =========================

@dataclass(frozen=True)
class Ds18b20Config:
    """
    Mapowanie ROM ID DS18B20 -> pole w Sensors.
    Przykład rom_id: "28-00000abcdef0"
    field_name: np. "boiler_temp", "cwu_temp", "radiators_temp", ...
    """
    rom_to_field: Dict[str, str]
    base_path: str = "/sys/bus/w1/devices"


@dataclass(frozen=True)
class Max6675Config:
    """
    MAX6675 na SPI.
    spi_bus=0, spi_dev=0 -> /dev/spidev0.0 (CE0)
    """
    spi_bus: int = 0
    spi_dev: int = 0
    max_hz: int = 4_000_000
    mode: int = 0  # SPI mode 0


@dataclass(frozen=True)
class PinConfig:
    """
    BCM pin numbering.
    """
    # SSR/ULN2003 – wyjścia cyfrowe
    feeder_pin: int = 17
    pump_co_pin: int = 27
    pump_cwu_pin: int = 22
    pump_circ_pin: int = 23
    mixer_open_pin: int = 24
    mixer_close_pin: int = 25
    alarm_buzzer_pin: int = 5
    alarm_relay_pin: int = 6

    # YYAC-3S (PWM)
    fan_pwm_pin: int = 18
    fan_pwm_freq_hz: int = 200


@dataclass(frozen=True)
class HardwareConfig:
    ds18b20: Ds18b20Config
    max6675: Optional[Max6675Config]
    pins: PinConfig

    # Jeśli przez jakiś driver logika PWM się odwróciła:
    fan_inverted: bool = False

    # Polling czujników (cache odświeżany w tle co N sekund)
    sensors_poll_interval_s: float = 5.0

    # Jeśli nie było świeżego poprawnego odczytu dłużej niż N sekund,
    # to read_sensors() wyzeruje (ustawi None) dane czujników.
    sensors_stale_after_s: float = 20.0


# =========================
# Implementacja HardwareInterface
# =========================

class RpiHardware:
    """
    Implementacja HardwareInterface dla Raspberry Pi 2 Model B.
    - read_sensors(): szybki snapshot z cache (z mechanizmem "stale -> None")
    - polling w tle co sensors_poll_interval_s
    """

    def __init__(self, cfg: HardwareConfig) -> None:
        self.cfg = cfg

        self._gpio = None
        self._pwm = None

        self._pigpio = None
        self._pi = None

        self._spi = None

        # idempotencja wyjść
        self._last_outputs = Outputs()

        # cache sensorów + znaczniki świeżości per źródło
        self._sensors_lock = threading.Lock()
        self._last_sensors = Sensors()
        self._last_ds18b20_ok_ts: float = 0.0
        self._last_max6675_ok_ts: float = 0.0

        # wątek pollujący
        self._stop_evt = threading.Event()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)

        self._init_gpio()
        self._init_pwm()
        self._init_spi()

        self._poll_thread.start()

    # ---------- Public API ----------

    def read_sensors(self) -> Sensors:
        """
        Zwraca natychmiast ostatni snapshot (cache).
        Jeśli dane są "stare" (brak poprawnego odczytu > sensors_stale_after_s),
        to odpowiednie pola będą ustawione na None.
        """
        now = time.time()
        stale_after = max(1.0, float(self.cfg.sensors_stale_after_s))

        with self._sensors_lock:
            snap = Sensors(**vars(self._last_sensors))
            ds_ok_ts = self._last_ds18b20_ok_ts
            max_ok_ts = self._last_max6675_ok_ts

        # Jeśli DS18B20 nie odświeżały się za długo, zeruj pola z DS18B20 (z mapowania)
        if ds_ok_ts <= 0.0 or (now - ds_ok_ts) > stale_after:
            for _rom, field in self.cfg.ds18b20.rom_to_field.items():
                if hasattr(snap, field):
                    setattr(snap, field, None)

        # Jeśli MAX6675 nie odświeżał się za długo, zeruj flue_gas_temp
        if self.cfg.max6675 is not None:
            if max_ok_ts <= 0.0 or (now - max_ok_ts) > stale_after:
                snap.flue_gas_temp = None

        return snap

    def apply_outputs(self, outputs: Outputs) -> None:
        """
        Ustawia wyjścia. Idempotentne: zmieniamy tylko to, co się zmieniło.
        Nie wywalamy ticka na drobnych błędach.
        """
        try:
            self._apply_digital(outputs)
        except Exception as e:
            log.error("apply_outputs(digital) failed: %s", e)

        try:
            self._apply_fan_pwm(outputs.fan_power)
        except Exception as e:
            log.error("apply_outputs(fan_pwm) failed: %s", e)

        self._last_outputs = outputs

    def close(self) -> None:
        """
        Wywołaj przy shutdown.
        """
        self._stop_evt.set()
        try:
            if self._poll_thread.is_alive():
                self._poll_thread.join(timeout=2.0)
        except Exception:
            pass

        # fan off
        try:
            self._apply_fan_pwm(0)
        except Exception:
            pass

        if self._pi is not None:
            try:
                self._pi.stop()
            except Exception:
                pass

        if self._gpio is not None:
            try:
                self._gpio.cleanup()
            except Exception:
                pass

        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:
                pass

    # ---------- Polling loop ----------

    def _poll_loop(self) -> None:
        interval = max(0.5, float(self.cfg.sensors_poll_interval_s))
        while not self._stop_evt.is_set():
            t0 = time.time()

            # czytamy i aktualizujemy cache
            s, ds_ok, max_ok = self._read_sensors_uncached_with_freshness()

            with self._sensors_lock:
                self._last_sensors = s
                if ds_ok:
                    self._last_ds18b20_ok_ts = t0
                if max_ok:
                    self._last_max6675_ok_ts = t0

            dt = time.time() - t0
            sleep_left = max(0.0, interval - dt)
            self._stop_evt.wait(sleep_left)

    def _read_sensors_uncached_with_freshness(self) -> tuple[Sensors, bool, bool]:
        """
        Realny odczyt z czujników (może trwać).
        Zwraca:
        - Sensors
        - ds18b20_ok: czy był choć 1 poprawny odczyt DS18B20 w tej iteracji
        - max6675_ok: czy MAX6675 zwrócił poprawną wartość w tej iteracji
        """
        s = Sensors()
        ds18b20_ok = False
        max6675_ok = False

        # DS18B20
        try:
            ds18b20_ok = self._read_all_ds18b20_into(s)
        except Exception as e:
            log.warning("DS18B20 read failed: %s", e)

        # MAX6675
        if self._spi is not None:
            try:
                val = self._read_max6675_c()
                s.flue_gas_temp = val
                max6675_ok = val is not None
            except Exception as e:
                log.warning("MAX6675 read failed: %s", e)

        return s, ds18b20_ok, max6675_ok

    # ---------- Init ----------

    def _init_gpio(self) -> None:
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except Exception as e:
            raise RuntimeError("RPi.GPIO not available (are you on Raspberry Pi OS?)") from e

        self._gpio = GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        pc = self.cfg.pins
        out_pins = [
            pc.feeder_pin,
            pc.pump_co_pin,
            pc.pump_cwu_pin,
            pc.pump_circ_pin,
            pc.mixer_open_pin,
            pc.mixer_close_pin,
            pc.alarm_buzzer_pin,
            pc.alarm_relay_pin,
        ]
        for p in out_pins:
            GPIO.setup(p, GPIO.OUT, initial=GPIO.LOW)

    def _init_pwm(self) -> None:
        """
        Prefer pigpio (stabilniejsze PWM). Fallback na RPi.GPIO.PWM.
        """
        pc = self.cfg.pins

        # 1) pigpio
        try:
            import pigpio  # type: ignore
            self._pigpio = pigpio
            self._pi = pigpio.pi()
            if self._pi.connected:
                self._pi.set_mode(pc.fan_pwm_pin, pigpio.OUTPUT)
                self._pi.set_PWM_frequency(pc.fan_pwm_pin, pc.fan_pwm_freq_hz)
                self._pi.set_PWM_dutycycle(pc.fan_pwm_pin, 0)
                log.info("PWM: using pigpio on GPIO%d @ %d Hz", pc.fan_pwm_pin, pc.fan_pwm_freq_hz)
                return
            else:
                self._pi = None
        except Exception:
            self._pi = None

        # 2) RPi.GPIO PWM
        try:
            GPIO = self._gpio
            GPIO.setup(pc.fan_pwm_pin, GPIO.OUT, initial=GPIO.LOW)
            self._pwm = GPIO.PWM(pc.fan_pwm_pin, pc.fan_pwm_freq_hz)
            self._pwm.start(0.0)
            log.info("PWM: using RPi.GPIO.PWM on GPIO%d @ %d Hz", pc.fan_pwm_pin, pc.fan_pwm_freq_hz)
        except Exception as e:
            log.warning("PWM init failed (no pigpio, no GPIO.PWM): %s", e)
            self._pwm = None

    def _init_spi(self) -> None:
        if self.cfg.max6675 is None:
            return
        try:
            import spidev  # type: ignore
        except Exception as e:
            log.warning("spidev not available; MAX6675 disabled: %s", e)
            return

        mc = self.cfg.max6675
        spi = spidev.SpiDev()
        spi.open(mc.spi_bus, mc.spi_dev)
        spi.max_speed_hz = mc.max_hz
        spi.mode = mc.mode
        self._spi = spi
        log.info("SPI: MAX6675 enabled on spidev%d.%d", mc.spi_bus, mc.spi_dev)

    # ---------- DS18B20 ----------

    def _read_all_ds18b20_into(self, sensors: Sensors) -> bool:
        """
        Wypełnia sensors polami z mapowania.
        Zwraca True, jeśli chociaż jeden czujnik dał poprawny odczyt (nie-None).
        """
        cfg = self.cfg.ds18b20
        any_ok = False

        for rom_id, field in cfg.rom_to_field.items():
            temp = self._read_ds18b20_c(cfg.base_path, rom_id)
            if hasattr(sensors, field):
                setattr(sensors, field, temp)
                if temp is not None:
                    any_ok = True
            else:
                log.warning("Unknown Sensors field for DS18B20 mapping: %s", field)

        return any_ok

    def _read_ds18b20_c(self, base_path: str, rom_id: str) -> Optional[float]:
        """
        Zwraca temperaturę w °C albo None.
        """
        path = os.path.join(base_path, rom_id, "w1_slave")
        try:
            with open(path, "r", encoding="ascii") as f:
                lines = f.read().strip().splitlines()
        except FileNotFoundError:
            return None

        if len(lines) < 2:
            return None
        if not lines[0].strip().endswith("YES"):
            return None

        idx = lines[1].find("t=")
        if idx < 0:
            return None

        raw = lines[1][idx + 2 :]
        try:
            milli_c = int(raw)
        except ValueError:
            return None

        return milli_c / 1000.0

    # ---------- MAX6675 ----------

    def _read_max6675_c(self) -> Optional[float]:
        """
        MAX6675: 16-bit:
        - bit 2 = 1 -> termopara odłączona
        - bity 15..3 -> temp * 0.25°C
        """
        if self._spi is None:
            return None

        data = self._spi.readbytes(2)
        if len(data) != 2:
            return None

        value = (data[0] << 8) | data[1]
        if value & 0x0004:
            return None

        temp_quarter = (value >> 3) & 0x1FFF
        return temp_quarter * 0.25

    # ---------- Outputs ----------

    def _apply_digital(self, o: Outputs) -> None:
        GPIO = self._gpio
        pc = self.cfg.pins

        # zabezpieczenie: nie otwieraj i nie zamykaj jednocześnie
        mixer_open = bool(o.mixer_open_on)
        mixer_close = bool(o.mixer_close_on)
        if mixer_open and mixer_close:
            mixer_open = False
            mixer_close = False

        desired = {
            pc.feeder_pin: bool(o.feeder_on),
            pc.pump_co_pin: bool(o.pump_co_on),
            pc.pump_cwu_pin: bool(o.pump_cwu_on),
            pc.pump_circ_pin: bool(o.pump_circ_on),
            pc.mixer_open_pin: mixer_open,
            pc.mixer_close_pin: mixer_close,
            pc.alarm_buzzer_pin: bool(o.alarm_buzzer_on),
            pc.alarm_relay_pin: bool(o.alarm_relay_on),
        }

        last = self._last_outputs
        last_map = {
            pc.feeder_pin: bool(last.feeder_on),
            pc.pump_co_pin: bool(last.pump_co_on),
            pc.pump_cwu_pin: bool(last.pump_cwu_on),
            pc.pump_circ_pin: bool(last.pump_circ_on),
            pc.mixer_open_pin: bool(last.mixer_open_on),
            pc.mixer_close_pin: bool(last.mixer_close_on),
            pc.alarm_buzzer_pin: bool(last.alarm_buzzer_on),
            pc.alarm_relay_pin: bool(last.alarm_relay_on),
        }

        for pin, state in desired.items():
            if last_map.get(pin) == state:
                continue
            GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW)

    def _apply_fan_pwm(self, fan_power: int) -> None:
        fan_power = int(max(0, min(100, fan_power)))

        # idempotencja
        if int(self._last_outputs.fan_power) == fan_power:
            return

        duty = fan_power
        if self.cfg.fan_inverted:
            duty = 100 - duty

        pc = self.cfg.pins

        # pigpio: duty 0..255
        if self._pi is not None:
            dc = int(round(duty * 255 / 100))
            self._pi.set_PWM_frequency(pc.fan_pwm_pin, pc.fan_pwm_freq_hz)
            self._pi.set_PWM_dutycycle(pc.fan_pwm_pin, dc)
            return

        # RPi.GPIO.PWM: duty 0..100
        if self._pwm is not None:
            self._pwm.ChangeDutyCycle(float(duty))
            return

        # fallback: ON/OFF
        GPIO = self._gpio
        GPIO.output(pc.fan_pwm_pin, GPIO.HIGH if duty >= 50 else GPIO.LOW)
