from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.kernel import ModuleInterface, ModuleTickResult
from backend.core.state import (
    BoilerMode,
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
)


# ---------- KONFIGURACJA RUNTIME ----------


@dataclass
class MixerConfig:
    """
    Konfiguracja modułu zaworu mieszającego.

    target_temp           – docelowa temperatura za zaworem [°C]
    ok_band_degC          – odchylenie od zadanej, które uznajemy za OK (martwa strefa) [°C]

    min_pulse_s           – minimalny czas pojedynczego ruchu (OTWÓRZ/ZAMKNIJ) [s]
    max_pulse_s           – maksymalny czas pojedynczego ruchu [s]
    adjust_interval_s     – jak często NAJSZYBCIEJ wprowadzamy kolejną korektę [s]

    ignition_open_boiler_temp      – od jakiej temperatury kotła [°C] w IGNITION
                                     w ogóle pozwalamy otwierać zawór

    ignition_max_boiler_drop_degC  – maksymalny dopuszczalny spadek temperatury kotła
                                     po ostatnim OTWÓRZ [°C]. Jeśli spadek większy,
                                     wstrzymujemy kolejne otwarcia do czasu „odbicia”.
    ignition_recover_factor        – frakcja spadku, jaką kocioł musi odzyskać,
                                     żeby znów pozwolić na OTWÓRZ (0..1).
                                     Np. 0.5 → przy spadku 6°C czekamy, aż
                                     odzyska 3°C.
    """

    target_temp: float = 40.0
    ok_band_degC: float = 2.0

    min_pulse_s: float = 0.5
    max_pulse_s: float = 3.0
    adjust_interval_s: float = 10.0

    ignition_open_boiler_temp: float = 55.0

    ignition_max_boiler_drop_degC: float = 5.0
    ignition_recover_factor: float = 0.5


class MixerModule(ModuleInterface):
    """
    Moduł sterujący zaworem mieszającym.

    - Start: zawór zakładamy jako ZAMKNIĘTY (0%) – znamy tylko ruch względny.
    - IGNITION:
        * dopóki T_kotła < ignition_open_boiler_temp → nie otwieramy zaworu,
        * gdy T_kotła jest już „ciepły”:
            - patrzymy na T_mix vs target_temp,
            - jeśli za zimno → próbujemy OTWORZYĆ, ALE:
                + po każdym OTWÓRZ patrzymy, o ile spadło na kotle,
                + jeśli > ignition_max_boiler_drop_degC → blokujemy dalsze
                  otwarcia, dopóki kocioł się częściowo nie odbije.
    - WORK:
        * klasyczny regulator na T_mix z martwą strefą.
    - OFF / MANUAL:
        * nie sterujemy zaworem.
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[MixerConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or MixerConfig()
        self._load_config_from_file()

        # Stan ruchu zaworu:
        self._movement_until_ts: Optional[float] = None
        self._movement_direction: Optional[str] = None  # "open" / "close" / None
        self._last_action_ts: Optional[float] = None

        # IGNITION – śledzenie wpływu OTWÓRZ na kocioł
        self._ign_last_open_start_boiler_temp: Optional[float] = None
        self._ign_last_open_drop_too_big: bool = False

        self._last_mode: Optional[str] = None

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "mixer"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = Outputs()

        mode_enum = system_state.mode
        boiler_temp = sensors.boiler_temp
        mix_temp = sensors.mixer_temp

        prev_direction = self._movement_direction
        prev_mode = self._last_mode

        # Mapowanie trybu:
        if mode_enum == BoilerMode.IGNITION:
            effective_mode = "ignition"
        elif mode_enum == BoilerMode.WORK:
            effective_mode = "work"
        elif mode_enum in (BoilerMode.OFF, BoilerMode.MANUAL):
            effective_mode = "off"
        else:
            effective_mode = "off"

        # 1) OFF / MANUAL – zawór nie ruszany
        if effective_mode == "off":
            self._stop_movement()
        else:
            # 2) jeśli trwa ruch – kontynuujemy impuls
            if self._movement_until_ts is not None and now < self._movement_until_ts:
                if self._movement_direction == "open":
                    outputs.mixer_open_on = True
                    outputs.mixer_close_on = False
                elif self._movement_direction == "close":
                    outputs.mixer_open_on = False
                    outputs.mixer_close_on = True
            else:
                # Ruch się skończył – zatrzymujemy zawór
                if self._movement_direction == "open" and effective_mode == "ignition":
                    # tu możemy policzyć spadek na kotle po poprzednim OTWÓRZ
                    self._update_ignition_boiler_drop(boiler_temp)

                self._stop_movement()

                # Czy możemy wykonać nową korektę?
                if self._can_adjust(now) and mix_temp is not None:
                    if effective_mode == "ignition":
                        direction = self._decide_direction_ignition(
                            mix_temp=mix_temp,
                            boiler_temp=boiler_temp,
                        )
                    else:
                        direction = self._decide_direction_work(mix_temp=mix_temp)

                    if direction is not None:
                        pulse_s = self._compute_pulse_duration(mix_temp=mix_temp)

                        # w IGNITION przy OTWÓRZ zapamiętujemy T_kotła na starcie impulsu
                        if effective_mode == "ignition" and direction == "open":
                            self._ign_last_open_start_boiler_temp = boiler_temp

                        self._start_movement(now, direction, pulse_s)

                        if direction == "open":
                            outputs.mixer_open_on = True
                            outputs.mixer_close_on = False
                        else:
                            outputs.mixer_open_on = False
                            outputs.mixer_close_on = True

                        events.append(
                            Event(
                                ts=now,
                                source=self.id,
                                level=EventLevel.INFO,
                                type="MIXER_MOVE",
                                message=(
                                    f"Zawór mieszający: {direction.upper()} "
                                    f"{pulse_s:.1f}s "
                                    f"(T_mix={mix_temp:.1f}°C, "
                                    f"zadana={self._config.target_temp:.1f}°C, "
                                    f"tryb={effective_mode})"
                                ),
                                data={
                                    "direction": direction,
                                    "pulse_s": pulse_s,
                                    "mixer_temp": mix_temp,
                                    "target_temp": self._config.target_temp,
                                    "mode": effective_mode,
                                    "boiler_temp": boiler_temp,
                                },
                            )
                        )

        # Event zmiany trybu:
        if prev_mode != effective_mode:
            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="MIXER_MODE_CHANGED",
                    message=f"Zawór mieszający: tryb '{prev_mode}' → '{effective_mode}'",
                    data={"prev_mode": prev_mode, "mode": effective_mode},
                )
            )

        self._last_mode = effective_mode

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- LOGIKA POMOCNICZA ----------

    def _stop_movement(self) -> None:
        self._movement_until_ts = None
        self._movement_direction = None

    def _can_adjust(self, now: float) -> bool:
        if self._last_action_ts is None:
            return True
        return (now - self._last_action_ts) >= self._config.adjust_interval_s

    def _decide_direction_work(self, mix_temp: float) -> Optional[str]:
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        if mix_temp < t_set - band:
            return "open"
        if mix_temp > t_set + band:
            return "close"
        return None

    def _decide_direction_ignition(
        self,
        mix_temp: float,
        boiler_temp: Optional[float],
    ) -> Optional[str]:
        """
        IGNITION:
          - chronimy kocioł przed zbyt dużym spadkiem temp. po OTWÓRZ,
          - dopóki kocioł nie „odbił”, blokujemy kolejne otwarcia.
        """
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        # Za gorąco za zaworem → ZAMKNIJ zawsze (tego nie blokujemy)
        if mix_temp > t_set + band:
            return "close"

        # Za zimno za zaworem → rozważamy OTWÓRZ
        if mix_temp < t_set - band:
            if boiler_temp is None:
                return None

            # 1) kocioł musi być powyżej progu dla ignition
            if boiler_temp < self._config.ignition_open_boiler_temp:
                return None

            # 2) jeśli ostatnie OTWÓRZ spowodowało za duży spadek,
            #    to czekamy, aż kocioł się częściowo odbije
            if self._ign_last_open_drop_too_big and \
               self._ign_last_open_start_boiler_temp is not None:
                max_drop = self._config.ignition_max_boiler_drop_degC
                recover_factor = self._config.ignition_recover_factor
                allowed_drop = max_drop * (1.0 - recover_factor)

                drop_now = self._ign_last_open_start_boiler_temp - boiler_temp
                if drop_now > allowed_drop:
                    # jeszcze za bardzo „przyduszony” – nie otwieraj
                    return None
                else:
                    # kocioł się odbił wystarczająco – możemy znów otwierać
                    self._ign_last_open_drop_too_big = False

            # wszystko OK → możemy OTWORZYĆ
            return "open"

        # w martwej strefie – nic nie rób
        return None

    def _update_ignition_boiler_drop(self, boiler_temp: Optional[float]) -> None:
        """
        Po zakończonym ruchu OTWÓRZ w IGNITION sprawdzamy,
        o ile spadła temperatura kotła – jeśli za dużo, włączamy
        blokadę kolejnych otwarć.
        """
        if boiler_temp is None:
            return
        if self._ign_last_open_start_boiler_temp is None:
            return

        drop = self._ign_last_open_start_boiler_temp - boiler_temp
        if drop > self._config.ignition_max_boiler_drop_degC:
            self._ign_last_open_drop_too_big = True

    def _compute_pulse_duration(self, mix_temp: float) -> float:
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        error = abs(t_set - mix_temp)
        max_err = 10.0
        eff_err = max(0.0, min(error - band, max_err))
        k = eff_err / max_err  # 0..1

        pulse = self._config.min_pulse_s + k * (self._config.max_pulse_s - self._config.min_pulse_s)
        if pulse < self._config.min_pulse_s:
            pulse = self._config.min_pulse_s
        if pulse > self._config.max_pulse_s:
            pulse = self._config.max_pulse_s

        return pulse

    def _start_movement(self, now: float, direction: str, pulse_s: float) -> None:
        self._movement_direction = direction
        self._movement_until_ts = now + pulse_s
        self._last_action_ts = now

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "target_temp" in values:
            self._config.target_temp = float(values["target_temp"])
        if "ok_band_degC" in values:
            self._config.ok_band_degC = float(values["ok_band_degC"])

        if "min_pulse_s" in values:
            self._config.min_pulse_s = float(values["min_pulse_s"])
        if "max_pulse_s" in values:
            self._config.max_pulse_s = float(values["max_pulse_s"])
        if "adjust_interval_s" in values:
            self._config.adjust_interval_s = float(values["adjust_interval_s"])

        if "ignition_open_boiler_temp" in values:
            self._config.ignition_open_boiler_temp = float(values["ignition_open_boiler_temp"])

        if "ignition_max_boiler_drop_degC" in values:
            self._config.ignition_max_boiler_drop_degC = float(values["ignition_max_boiler_drop_degC"])
        if "ignition_recover_factor" in values:
            self._config.ignition_recover_factor = float(values["ignition_recover_factor"])

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        """
        Publiczne API wymagane przez Kernel.
        """
        self._load_config_from_file()
			
    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for field in (
            "target_temp",
            "ok_band_degC",
            "min_pulse_s",
            "max_pulse_s",
            "adjust_interval_s",
            "ignition_open_boiler_temp",
            "ignition_max_boiler_drop_degC",
            "ignition_recover_factor",
        ):
            if field in data:
                setattr(self._config, field, float(data[field]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
