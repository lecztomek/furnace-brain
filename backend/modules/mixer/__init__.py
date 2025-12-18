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
    PartialOutputs,
)

# ---------- KONFIGURACJA RUNTIME ----------


@dataclass
class MixerConfig:
    """
    Konfiguracja modułu zaworu mieszającego.

    target_temp           – docelowa temperatura w obiegu CO za zaworem [°C]
                            (w naszym modelu: Sensors.radiators_temp)
    ok_band_degC          – odchylenie od zadanej, które uznajemy za OK (martwa strefa) [°C]

    min_pulse_s           – minimalny czas pojedynczego ruchu (OTWÓRZ/ZAMKNIJ) [s]
    max_pulse_s           – maksymalny czas pojedynczego ruchu [s]
    adjust_interval_s     – jak często NAJSZYBCIEJ wprowadzamy kolejną korektę [s]

    ramp_error_factor     – mnożnik martwej strefy, przy którym uznajemy,
                            że temperatura na grzejnikach jest „daleko od zadanej”
                            i przechodzimy w tryb ramp (dogrzewanie z ochroną kotła).

    boiler_min_temp_for_open   – od jakiej temperatury kotła [°C] w ogóle
                                 pozwalamy OTWIERAĆ zawór w trybie „ramp”
                                 (chroni kocioł na starcie).
    boiler_max_drop_degC       – maksymalny dopuszczalny spadek temperatury kotła
                                 po ostatnim OTWÓRZ [°C]. Jeśli spadek większy,
                                 wstrzymujemy kolejne otwarcia do czasu „odbicia”.
    boiler_recover_factor      – frakcja spadku, jaką kocioł musi odzyskać,
                                 żeby znów pozwolić na OTWÓRZ (0..1).
                                 Np. 0.5 → przy spadku 6°C czekamy, aż
                                 odzyska 3°C.

    preclose_on_ignition_enabled – czy przy wejściu w IGNITION wykonywać pre-close
                                  (pełne domknięcie) przed rampowaniem.
    preclose_full_close_time_s   – czas pełnego domknięcia zaworu (od 100% do 0%) [s]
    """

    target_temp: float = 40.0
    ok_band_degC: float = 2.0

    min_pulse_s: float = 0.5
    max_pulse_s: float = 3.0
    adjust_interval_s: float = 10.0

    ramp_error_factor: float = 2.0

    boiler_min_temp_for_open: float = 55.0
    boiler_max_drop_degC: float = 5.0
    boiler_recover_factor: float = 0.5

    preclose_on_ignition_enabled: bool = True
    preclose_full_close_time_s: float = 120.0


class MixerModule(ModuleInterface):
    """
    Moduł sterujący zaworem mieszającym.

    Sterujemy na podstawie temperatury W OBIEGU CO (radiators_temp),
    która jest „za zaworem”.

    - Start: zawór zakładamy jako ZAMKNIĘTY (0%) – znamy tylko ruch względny.
    - OFF / MANUAL:
        * nie sterujemy zaworem.
    - AUTO (dowolny tryb kotła poza OFF/MANUAL):
        * logika mieszacza NIE patrzy na tryb kotła (IGNITION/WORK),
          używa go tylko do stwierdzenia OFF/MANUAL,
        * wewnętrznie ma dwa tryby pracy:
            - "ramp":
                + gdy temperatura na grzejnikach jest DALEKO od zadanej
                  (błąd > ramp_error_factor × ok_band_degC),
                + przy zbyt zimnych grzejnikach OTWIERA z ochroną kotła
                  (boiler_min_temp_for_open, boiler_max_drop_degC,
                  boiler_recover_factor),
            - "stabilize":
                + gdy jesteśmy BLISKO zadanej,
                + klasyczny regulator na T_CO z martwą strefą, bez patrzenia
                  na temperaturę kotła.

    DODATEK:
    - Opcjonalny "pre-close" przy przejściu do IGNITION:
        Jeśli wchodzimy w rozpalanie i T_CO jest daleko od zadanej (czyli i tak
        weszlibyśmy w "ramp"), to przed rampowaniem wykonujemy pełne domknięcie
        zaworu przez preclose_full_close_time_s, aby startować ramp z pewnego
        punktu (0%).
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

        # Ochrona kotła – śledzenie wpływu OTWÓRZ na kocioł (tryb "ramp")
        self._last_open_start_boiler_temp: Optional[float] = None
        self._last_open_drop_too_big: bool = False

        # Ostatni tryb logiki mieszacza ("off" / "ramp" / "stabilize" / "ignition_preclose")
        self._last_mode: Optional[str] = None

        # Do wykrywania przejść trybów kotła
        self._prev_boiler_mode: Optional[BoilerMode] = None

        # Jednorazowy pre-close na wejściu w IGNITION
        self._ignition_preclose_done: bool = False
        self._force_full_close: bool = False

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
        outputs = PartialOutputs()

        mode_enum = system_state.mode
        boiler_temp = sensors.boiler_temp
        # Używamy temperatury w obiegu CO (radiators_temp) jako T za zaworem
        rad_temp = sensors.radiators_temp

        prev_mode = self._last_mode

        # --- Wykrycie wejścia/wyjścia z IGNITION ---
        entering_ignition = (
            mode_enum == BoilerMode.IGNITION
            and self._prev_boiler_mode != BoilerMode.IGNITION
        )
        leaving_ignition = (
            mode_enum != BoilerMode.IGNITION
            and self._prev_boiler_mode == BoilerMode.IGNITION
        )

        if leaving_ignition:
            # nowa sesja rozpalania w przyszłości -> znów wolno zrobić preclose
            self._ignition_preclose_done = False
            self._force_full_close = False

        # --- Pre-close przed rampowaniem na wejściu w IGNITION (opcjonalny bajer) ---
        if (
            entering_ignition
            and self._config.preclose_on_ignition_enabled
            and not self._ignition_preclose_done
            and self._is_far_from_setpoint(rad_temp)
        ):
            self._ignition_preclose_done = True
            self._force_full_close = True

            # utnij ewentualny bieżący impuls i rozpocznij pełne domknięcie
            self._stop_movement()
            close_s = float(self._config.preclose_full_close_time_s)
            self._start_movement(now, "close", close_s)

            outputs.mixer_open_on = False
            outputs.mixer_close_on = True

            events.append(
                Event(
                    ts=now,
                    source=self.id,
                    level=EventLevel.INFO,
                    type="MIXER_PRECLOSE_ON_IGNITION",
                    message=(
                        f"Zawór mieszający: pełne ZAMKNIĘCIE {close_s:.1f}s "
                        f"przed rampowaniem (wejście w IGNITION, "
                        f"T_CO={rad_temp:.1f}°C, zadana={self._config.target_temp:.1f}°C)"
                    ),
                    data={
                        "pulse_s": close_s,
                        "radiators_temp": rad_temp,
                        "target_temp": self._config.target_temp,
                        "mode": "ignition_preclose",
                        "boiler_temp": boiler_temp,
                    },
                )
            )

            # Event zmiany trybu logiki mieszacza:
            if prev_mode != "ignition_preclose":
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="MIXER_MODE_CHANGED",
                        message=f"Zawór mieszający: tryb '{prev_mode}' → 'ignition_preclose'",
                        data={"prev_mode": prev_mode, "mode": "ignition_preclose"},
                    )
                )

            self._last_mode = "ignition_preclose"
            self._prev_boiler_mode = mode_enum

            status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
            return ModuleTickResult(
                partial_outputs=outputs,
                events=events,
                status=status,
            )

        # ---------- WYZNACZENIE TRYBU LOGIKI MIESZACZA ----------

        if self._force_full_close:
            effective_mode = "ignition_preclose"
        else:
            if mode_enum in (BoilerMode.OFF, BoilerMode.MANUAL):
                effective_mode = "off"
            else:
                # AUTO – decydujemy tylko po błędzie na grzejnikach
                if rad_temp is None:
                    # brak danych – zachowuj się zachowawczo jak "stabilize"
                    effective_mode = "stabilize"
                else:
                    t_set = self._config.target_temp
                    band = self._config.ok_band_degC
                    error = abs(t_set - rad_temp)

                    # próg "daleko od zadanej" – ramp_error_factor × martwa strefa
                    far_err = self._config.ramp_error_factor * band

                    if error > far_err:
                        effective_mode = "ramp"
                    else:
                        effective_mode = "stabilize"

        # ---------- GŁÓWNA LOGIKA RUCHU ZAWORU ----------

        if effective_mode == "off":
            # OFF / MANUAL – zawór nie ruszany
            self._stop_movement()
            self._force_full_close = False
        else:
            # 1) jeśli trwa ruch – kontynuujemy impuls
            if self._movement_until_ts is not None and now < self._movement_until_ts:
                if self._movement_direction == "open":
                    outputs.mixer_open_on = True
                    outputs.mixer_close_on = False
                elif self._movement_direction == "close":
                    outputs.mixer_open_on = False
                    outputs.mixer_close_on = True
            else:
                # Ruch się skończył – zatrzymujemy zawór
                finished_dir = self._movement_direction

                if finished_dir == "open":
                    # po OTWÓRZ możemy policzyć spadek na kotle
                    self._update_boiler_drop(boiler_temp)

                self._stop_movement()

                # Jeśli skończyliśmy pre-close, zdejmij flagę i pozwól wejść w ramp/stabilize
                if self._force_full_close and finished_dir == "close":
                    self._force_full_close = False

                # Czy możemy wykonać nową korektę?
                if self._can_adjust(now) and rad_temp is not None and not self._force_full_close:
                    if effective_mode == "ramp":
                        direction = self._decide_direction_ramp(
                            mix_temp=rad_temp,
                            boiler_temp=boiler_temp,
                        )
                    elif effective_mode == "stabilize":
                        direction = self._decide_direction_work(mix_temp=rad_temp)
                    else:
                        direction = None

                    if direction is not None:
                        # Długość impulsu zależna od błędu na grzejnikach
                        pulse_s = self._compute_pulse_duration(mix_temp=rad_temp)

                        # W trybie "ramp" przy OTWÓRZ zapamiętujemy T_kotła
                        if effective_mode == "ramp" and direction == "open":
                            self._last_open_start_boiler_temp = boiler_temp

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
                                    f"(T_CO={rad_temp:.1f}°C, "
                                    f"zadana={self._config.target_temp:.1f}°C, "
                                    f"tryb={effective_mode})"
                                ),
                                data={
                                    "direction": direction,
                                    "pulse_s": pulse_s,
                                    "radiators_temp": rad_temp,
                                    "target_temp": self._config.target_temp,
                                    "mode": effective_mode,
                                    "boiler_temp": boiler_temp,
                                },
                            )
                        )

        # Event zmiany trybu logiki mieszacza:
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
        self._prev_boiler_mode = mode_enum

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- LOGIKA POMOCNICZA ----------

    def _is_far_from_setpoint(self, rad_temp: Optional[float]) -> bool:
        if rad_temp is None:
            return False
        t_set = self._config.target_temp
        band = self._config.ok_band_degC
        far_err = self._config.ramp_error_factor * band
        return abs(t_set - rad_temp) > far_err

    def _stop_movement(self) -> None:
        self._movement_until_ts = None
        self._movement_direction = None

    def _can_adjust(self, now: float) -> bool:
        if self._last_action_ts is None:
            return True
        return (now - self._last_action_ts) >= self._config.adjust_interval_s

    def _decide_direction_work(self, mix_temp: float) -> Optional[str]:
        """
        Tryb "stabilize" – klasyczny regulator na T_CO (radiators_temp) z martwą strefą.
        """
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        if mix_temp < t_set - band:
            return "open"
        if mix_temp > t_set + band:
            return "close"
        return None

    def _decide_direction_ramp(
        self,
        mix_temp: float,
        boiler_temp: Optional[float],
    ) -> Optional[str]:
        """
        Tryb "ramp" – daleko od zadanej na grzejnikach.

        - za gorąco w obiegu CO → ZAMKNIJ zawsze (bez patrzenia na kocioł),
        - za zimno w CO → OTWÓRZ, ale:
            * kocioł musi być powyżej boiler_min_temp_for_open,
            * jeśli ostatnie OTWÓRZ spowodowało za duży spadek na kotle,
              czekamy, aż kocioł się częściowo odbije (boiler_recover_factor).
        """
        t_set = self._config.target_temp
        band = self._config.ok_band_degC

        # Za gorąco w obiegu CO → ZAMKNIJ zawsze (tego nie blokujemy)
        if mix_temp > t_set + band:
            return "close"

        # Za zimno w CO → rozważamy OTWÓRZ
        if mix_temp < t_set - band:
            if boiler_temp is None:
                return None

            # 1) kocioł musi być powyżej progu
            if boiler_temp < self._config.boiler_min_temp_for_open:
                return None

            # 2) jeśli ostatnie OTWÓRZ spowodowało za duży spadek,
            #    to czekamy, aż kocioł się częściowo odbije
            if self._last_open_drop_too_big and self._last_open_start_boiler_temp is not None:
                max_drop = self._config.boiler_max_drop_degC
                recover_factor = self._config.boiler_recover_factor
                allowed_drop = max_drop * (1.0 - recover_factor)

                drop_now = self._last_open_start_boiler_temp - boiler_temp
                if drop_now > allowed_drop:
                    # jeszcze za bardzo „przyduszony” – nie otwieraj
                    return None
                else:
                    # kocioł się odbił wystarczająco – możemy znów otwierać
                    self._last_open_drop_too_big = False

            # wszystko OK → możemy OTWORZYĆ
            return "open"

        # w martwej strefie – nic nie rób
        return None

    def _update_boiler_drop(self, boiler_temp: Optional[float]) -> None:
        """
        Po zakończonym ruchu OTWÓRZ w trybie "ramp" sprawdzamy,
        o ile spadła temperatura kotła – jeśli za dużo, włączamy
        blokadę kolejnych otwarć (dopóki kocioł się nie odbije).
        """
        if boiler_temp is None:
            return
        if self._last_open_start_boiler_temp is None:
            return

        drop = self._last_open_start_boiler_temp - boiler_temp
        if drop > self._config.boiler_max_drop_degC:
            self._last_open_drop_too_big = True

    def _compute_pulse_duration(self, mix_temp: float) -> float:
        """
        Wyznaczanie długości impulsu OTWÓRZ/ZAMKNIJ na podstawie błędu
        temperatury CO względem zadanej (z martwą strefą).
        """
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

        if "ramp_error_factor" in values:
            self._config.ramp_error_factor = float(values["ramp_error_factor"])

        if "boiler_min_temp_for_open" in values:
            self._config.boiler_min_temp_for_open = float(values["boiler_min_temp_for_open"])
        if "boiler_max_drop_degC" in values:
            self._config.boiler_max_drop_degC = float(values["boiler_max_drop_degC"])
        if "boiler_recover_factor" in values:
            self._config.boiler_recover_factor = float(values["boiler_recover_factor"])

        if "preclose_on_ignition_enabled" in values:
            self._config.preclose_on_ignition_enabled = bool(values["preclose_on_ignition_enabled"])
        if "preclose_full_close_time_s" in values:
            self._config.preclose_full_close_time_s = float(values["preclose_full_close_time_s"])

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
            "ramp_error_factor",
            "boiler_min_temp_for_open",
            "boiler_max_drop_degC",
            "boiler_recover_factor",
            "preclose_on_ignition_enabled",
            "preclose_full_close_time_s",
        ):
            if field in data:
                if field == "preclose_on_ignition_enabled":
                    setattr(self._config, field, bool(data[field]))
                else:
                    setattr(self._config, field, float(data[field]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
