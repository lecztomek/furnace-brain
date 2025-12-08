from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # pip install pyyaml

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
class FeederConfig:
    """
    Konfiguracja modułu podajnika ślimakowego.

    feed_on_base_s       – czas pracy ślimaka w jednym cyklu [s]
                           (tak jak "czas podawania" w sterownikach)
    feed_off_base_s      – przerwa przy 100% mocy [s]
                           (tak jak "czas przerwy" w sterownikach)
                           Dla mocy P [%] przeliczamy:
                               off_time = feed_off_base_s * (100 / P)

    min_power_to_feed    – minimalna moc [%], przy której w ogóle karmimy.
                           Poniżej tej mocy ślimak jest wyłączony (OFF).

    min_pause_s          – minimalna przerwa [s] (bez względu na power)
    max_pause_s          – maksymalna przerwa [s] (bez względu na power)
    """

    feed_on_base_s: float = 10.0
    feed_off_base_s: float = 64.0

    min_power_to_feed: float = 5.0

    min_pause_s: float = 10.0
    max_pause_s: float = 600.0  # np. 10 minut


class FeederModule(ModuleInterface):
    """
    Moduł sterujący ślimakiem podajnika na podstawie mocy kotła (power_percent).

    Logika:

    - Jeśli SystemState.mode w {OFF, MANUAL}:
        feeder_on = False, reset licznika.

    - W trybach IGNITION / WORK:
        - pobieramy power = system_state.outputs.power_percent,
        - jeśli power <= min_power_to_feed:
              feeder_on = False, reset licznika,
          inaczej:
              wyznaczamy czasy cyklu:
                  on_time = feed_on_base_s
                  off_time = feed_off_base_s * (100 / power)
                  off_time przycinamy do [min_pause_s, max_pause_s]

              następnie generujemy cykl:
                  - zaczynamy od ON (po wejściu w tryb karmienia),
                  - po on_time przełączamy na OFF,
                  - po off_time przełączamy z powrotem na ON,
                  - i tak w kółko.

    Zmiany stanu ślimaka raportujemy eventami FEEDER_ON / FEEDER_OFF.
    """

    def __init__(
        self,
        base_path: Optional[Path] = None,
        config: Optional[FeederConfig] = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or FeederConfig()
        self._load_config_from_file()

        # Stan wewnętrzny cyklu
        self._feeder_on: bool = False
        self._last_switch_ts: Optional[float] = None  # kiedy zmieniliśmy stan ON/OFF
        self._last_effective_active: bool = False  # czy w poprzednim ticku byliśmy w "aktywnej" pracy (power > min i mode auto/ignition)

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "feeder"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = Outputs()  # domyślnie nie zmieniamy nic poza feeder_on

        mode = system_state.mode
        power = system_state.outputs.power_percent  # 0–100%

        prev_feeder_on = self._feeder_on

        # Czy w ogóle powinniśmy automatycznie karmić w tym ticku?
        active_mode = mode in (BoilerMode.IGNITION, BoilerMode.WORK)
        active_power = power is not None and power > self._config.min_power_to_feed
        effective_active = bool(active_mode and active_power)

        if not effective_active:
            # W OFF / MANUAL lub przy zbyt małej mocy – ślimak wyłączony,
            # reset cyklu, ewentualnie event przy przejściu z ON na OFF.
            if self._feeder_on:
                self._feeder_on = False
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="FEEDER_OFF",
                        message="Podajnik ślimakowy: OFF (tryb ręczny/OFF lub zbyt mała moc).",
                        data={"power_percent": power, "mode": mode.name},
                    )
                )

            self._last_switch_ts = None
            self._last_effective_active = False

        else:
            # Aktywna praca – liczymy cykl ON/OFF
            # Czasy cyklu na podstawie aktualnego power
            on_time = max(self._config.feed_on_base_s, 0.0)

            # zabezpieczenie przed dzieleniem przez 0
            eff_power = max(float(power), 0.1)
            raw_off = self._config.feed_off_base_s * (100.0 / eff_power)

            off_time = max(self._config.min_pause_s, min(raw_off, self._config.max_pause_s))

            if not self._last_effective_active:
                # Wchodzimy świeżo w aktywne karmienie – zacznij od ON
                self._feeder_on = True
                self._last_switch_ts = now
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.INFO,
                        type="FEEDER_ON",
                        message=(
                            f"Podajnik ślimakowy: ON (start cyklu, power={power:.1f}%, "
                            f"on_time={on_time:.1f}s, off_time={off_time:.1f}s)"
                        ),
                        data={
                            "power_percent": power,
                            "on_time_s": on_time,
                            "off_time_s": off_time,
                            "mode": mode.name,
                        },
                    )
                )
            else:
                # Normalny cykl – sprawdzamy, czy trzeba przełączyć
                if self._last_switch_ts is None:
                    # na wszelki wypadek zainicjalizuj
                    self._feeder_on = True
                    self._last_switch_ts = now
                else:
                    elapsed = now - self._last_switch_ts
                    if self._feeder_on:
                        # jesteśmy w fazie ON
                        if elapsed >= on_time:
                            self._feeder_on = False
                            self._last_switch_ts = now
                            events.append(
                                Event(
                                    ts=now,
                                    source=self.id,
                                    level=EventLevel.INFO,
                                    type="FEEDER_OFF",
                                    message=(
                                        f"Podajnik ślimakowy: OFF (koniec podawania, "
                                        f"on_time={on_time:.1f}s)"
                                    ),
                                    data={
                                        "power_percent": power,
                                        "on_time_s": on_time,
                                        "off_time_s": off_time,
                                        "mode": mode.name,
                                    },
                                )
                            )
                    else:
                        # jesteśmy w fazie OFF
                        if elapsed >= off_time:
                            self._feeder_on = True
                            self._last_switch_ts = now
                            events.append(
                                Event(
                                    ts=now,
                                    source=self.id,
                                    level=EventLevel.INFO,
                                    type="FEEDER_ON",
                                    message=(
                                        f"Podajnik ślimakowy: ON (start podawania, "
                                        f"off_time={off_time:.1f}s)"
                                    ),
                                    data={
                                        "power_percent": power,
                                        "on_time_s": on_time,
                                        "off_time_s": off_time,
                                        "mode": mode.name,
                                    },
                                )
                            )

            self._last_effective_active = True

        # Wykrywanie zmiany stanu (dla pewności, że mamy event przy każdej zmianie)
        if self._feeder_on != prev_feeder_on:
            if self._feeder_on:
                # jeśli FEEDER_ON nie był już dodany powyżej, można dopisać;
                # zostawiamy jak jest, żeby nie dublować logów.
                pass
            else:
                # analogicznie dla OFF
                pass

        # Ustaw wyjście
        outputs.feeder_on = self._feeder_on

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return asdict(self._config)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "feed_on_base_s" in values:
            self._config.feed_on_base_s = float(values["feed_on_base_s"])
        if "feed_off_base_s" in values:
            self._config.feed_off_base_s = float(values["feed_off_base_s"])
        if "min_power_to_feed" in values:
            self._config.min_power_to_feed = float(values["min_power_to_feed"])
        if "min_pause_s" in values:
            self._config.min_pause_s = float(values["min_pause_s"])
        if "max_pause_s" in values:
            self._config.max_pause_s = float(values["max_pause_s"])

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
            "feed_on_base_s",
            "feed_off_base_s",
            "min_power_to_feed",
            "min_pause_s",
            "max_pause_s",
        ):
            if field in data:
                setattr(self._config, field, float(data[field]))

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
