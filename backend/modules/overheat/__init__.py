from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

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

# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class OverheatConfig:
    # Kocioł
    boiler_trip_temp: float = 90.0
    boiler_hysteresis: float = 5.0

    # Podajnik/ślimak (u Ciebie: hopper_temp)
    hopper_trip_temp: float = 70.0
    hopper_hysteresis: float = 5.0

    # Ile minut ma chodzić ślimak po wejściu w przegrzanie podajnika (wypchnięcie żaru)
    hopper_purge_minutes: float = 2.0


# ---------- MODUŁ ----------

class OverheatModule(ModuleInterface):
    """
    overheat:
    - Przegrzanie kotła (Sensors.boiler_temp):
        gdy T >= boiler_trip_temp -> pompy CO+CWU ON, dmuchawa OFF, ślimak OFF, mixer OPEN
        wyłącza się dopiero gdy T <= (boiler_trip_temp - boiler_hysteresis)

    - Przegrzanie podajnika/ślimaka (Sensors.hopper_temp):
        gdy T >= hopper_trip_temp -> pompy CO+CWU ON, dmuchawa OFF, mixer NIE ruszamy,
        uruchamiamy ślimak na hopper_purge_minutes (jednorazowo na wejście w alarm)
        wyłącza się dopiero gdy T <= (hopper_trip_temp - hopper_hysteresis)
    """

    def __init__(self, base_path: Path | None = None, config: OverheatConfig | None = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or OverheatConfig()
        self._load_config_from_file()

        # stan wewnętrzny (histereza + purge)
        self._boiler_active: bool = False
        self._hopper_active: bool = False

        # UWAGA: ten timestamp jest w czasie MONOTONICZNYM (ctrl time)
        self._purge_until: Optional[float] = None

        # rate-limit na event braku czujnika (ctrl time)
        self._missing_sensor_last_event_ts: float = 0.0

    @property
    def id(self) -> str:
        return "overheat"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()  # domyślnie nic nie zmieniamy
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        # czas sterujący (odporny na DST/NTP); eventy/logi nadal na wall time (now)
        now_ctrl = system_state.ts_mono

        t_boiler = sensors.boiler_temp
        t_hopper = sensors.hopper_temp

        # Bez fallbacków: jeśli brak danych -> nie wymuszamy, tylko warning (max co 60s ctrl-time)
        if t_boiler is None or t_hopper is None:
            if now_ctrl - self._missing_sensor_last_event_ts >= 60.0:
                self._missing_sensor_last_event_ts = now_ctrl
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.WARNING,
                        type="OVERHEAT_MISSING_SENSOR",
                        message="Brak odczytu boiler_temp i/lub hopper_temp. Moduł overheat nie wymusza wyjść.",
                        data={"boiler_temp": t_boiler, "hopper_temp": t_hopper},
                    )
                )
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # ---------- BOILER overheat (z histerezą) ----------
        prev_boiler = self._boiler_active
        if not self._boiler_active:
            if t_boiler >= self._config.boiler_trip_temp:
                self._boiler_active = True
        else:
            if t_boiler <= (self._config.boiler_trip_temp - self._config.boiler_hysteresis):
                self._boiler_active = False

        if prev_boiler != self._boiler_active:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.ALARM if self._boiler_active else EventLevel.INFO,
                    type="BOILER_OVERHEAT_ON" if self._boiler_active else "BOILER_OVERHEAT_OFF",
                    message=(
                        f"Przegrzanie kotła: AKTYWNE (T={t_boiler:.1f}°C, próg={self._config.boiler_trip_temp:.1f}°C)"
                        if self._boiler_active
                        else f"Przegrzanie kotła: ZAKOŃCZONE (T={t_boiler:.1f}°C, reset<="
                             f"{self._config.boiler_trip_temp - self._config.boiler_hysteresis:.1f}°C)"
                    ),
                    data={
                        "boiler_temp": t_boiler,
                        "trip": self._config.boiler_trip_temp,
                        "hysteresis": self._config.boiler_hysteresis,
                    },
                )
            )

        # ---------- HOPPER overheat (z histerezą + purge) ----------
        prev_hopper = self._hopper_active
        if not self._hopper_active:
            if t_hopper >= self._config.hopper_trip_temp:
                self._hopper_active = True

                # purge jednorazowo na wejście w alarm (ctrl-time)
                purge_seconds = max(0.0, float(self._config.hopper_purge_minutes) * 60.0)
                if purge_seconds > 0:
                    self._purge_until = now_ctrl + purge_seconds
                    events.append(
                        Event(
                            ts=now,
                            source=self.id,
                            level=EventLevel.ALARM,
                            type="HOPPER_PURGE_START",
                            message=f"Przegrzanie podajnika: uruchomiono ślimak na {self._config.hopper_purge_minutes:.1f} min.",
                            data={
                                "purge_minutes": self._config.hopper_purge_minutes,
                                "purge_seconds": purge_seconds,
                                # dla czytelności w logach: wall-time szacunkowe (nie wpływa na sterowanie)
                                "purge_until_wall_ts": now + purge_seconds,
                            },
                        )
                    )
        else:
            if t_hopper <= (self._config.hopper_trip_temp - self._config.hopper_hysteresis):
                self._hopper_active = False
                self._purge_until = None

        if prev_hopper != self._hopper_active:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.ALARM if self._hopper_active else EventLevel.INFO,
                    type="HOPPER_OVERHEAT_ON" if self._hopper_active else "HOPPER_OVERHEAT_OFF",
                    message=(
                        f"Przegrzanie podajnika: AKTYWNE (T={t_hopper:.1f}°C, próg={self._config.hopper_trip_temp:.1f}°C)"
                        if self._hopper_active
                        else f"Przegrzanie podajnika: ZAKOŃCZONE (T={t_hopper:.1f}°C, reset<="
                             f"{self._config.hopper_trip_temp - self._config.hopper_hysteresis:.1f}°C)"
                    ),
                    data={
                        "hopper_temp": t_hopper,
                        "trip": self._config.hopper_trip_temp,
                        "hysteresis": self._config.hopper_hysteresis,
                    },
                )
            )

        safety_active = self._boiler_active or self._hopper_active
        if not safety_active:
            return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

        # info: nadpisanie MANUAL (bezpieczeństwo)
        if system_state.mode == BoilerMode.MANUAL:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.WARNING,
                    type="OVERHEAT_OVERRIDE_MANUAL",
                    message="Ochrona przegrzania aktywna – nadpisuje sterowanie MANUAL.",
                    data={},
                )
            )

        # purge: feeder_on tylko w czasie purge i tylko przy przegrzaniu podajnika
        purge_on = False
        if self._hopper_active and self._purge_until is not None:
            purge_on = now_ctrl < self._purge_until
            if not purge_on:
                self._purge_until = None
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="HOPPER_PURGE_END",
                        message="Zakończono wypychanie żaru (purge) ślimakiem.",
                        data={},
                    )
                )

        # wymuszenia bezpieczeństwa
        outputs.pump_co_on = True
        outputs.pump_cwu_on = True
        outputs.fan_power = 0

        # domyślnie OFF, ale w purge ON
        outputs.feeder_on = bool(purge_on)

        # mieszacz: otwieramy TYLKO przy przegrzaniu kotła
        if self._boiler_active:
            outputs.mixer_open_on = True
            outputs.mixer_close_on = False
        else:
            outputs.mixer_open_on = False
            outputs.mixer_close_on = False

        return ModuleTickResult(partial_outputs=outputs, events=events, status=status)

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return {
            "boiler_trip_temp": self._config.boiler_trip_temp,
            "boiler_hysteresis": self._config.boiler_hysteresis,
            "hopper_trip_temp": self._config.hopper_trip_temp,
            "hopper_hysteresis": self._config.hopper_hysteresis,
            "hopper_purge_minutes": self._config.hopper_purge_minutes,
        }

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "boiler_trip_temp" in values:
            self._config.boiler_trip_temp = float(values["boiler_trip_temp"])
        if "boiler_hysteresis" in values:
            self._config.boiler_hysteresis = float(values["boiler_hysteresis"])

        if "hopper_trip_temp" in values:
            self._config.hopper_trip_temp = float(values["hopper_trip_temp"])
        if "hopper_hysteresis" in values:
            self._config.hopper_hysteresis = float(values["hopper_hysteresis"])

        if "hopper_purge_minutes" in values:
            self._config.hopper_purge_minutes = float(values["hopper_purge_minutes"])

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "boiler_trip_temp" in data:
            self._config.boiler_trip_temp = float(data["boiler_trip_temp"])
        if "boiler_hysteresis" in data:
            self._config.boiler_hysteresis = float(data["boiler_hysteresis"])

        if "hopper_trip_temp" in data:
            self._config.hopper_trip_temp = float(data["hopper_trip_temp"])
        if "hopper_hysteresis" in data:
            self._config.hopper_hysteresis = float(data["hopper_hysteresis"])

        if "hopper_purge_minutes" in data:
            self._config.hopper_purge_minutes = float(data["hopper_purge_minutes"])

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

