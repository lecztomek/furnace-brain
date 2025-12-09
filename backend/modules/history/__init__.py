from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import csv
import datetime as dt
import yaml  # pip install pyyaml

from backend.core.kernel import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    EventLevel,
    ModuleStatus,
    Outputs,
    Sensors,
    SystemState,
)


# ---------- KONFIGURACJA RUNTIME ----------

@dataclass
class HistoryConfig:
    """
    Konfiguracja modułu historii.

    log_dir        – katalog, w którym zapisywane są pliki CSV z historią.
    interval_sec   – co ile sekund zapisywać nowy punkt danych.
    file_prefix    – prefiks nazwy pliku CSV.
    """

    log_dir: str = "data"        # względnie do katalogu modułu
    interval_sec: float = 30.0      # logowanie co 30 s
    file_prefix: str = "boiler"     # np. boiler_20241209_13.csv


class HistoryModule(ModuleInterface):
    """
    Moduł historii – zapisuje wybrane parametry kotła do plików CSV.

    Zapisujemy:
    - data_czas       – timestamp (ISO 8601),
    - temp_pieca      – temperatura kotła,
    - power           – moc,
    - temp_grzejnikow – temperatura obiegu CO / grzejników,
    - temp_spalin     – temperatura spalin,
    - tryb_pracy      – np. "ignition", "work", itp.

    Dane zapisywane są co `interval_sec` sekund (domyślnie 30 s).
    Dla każdej godziny powstaje osobny plik CSV:
      <log_dir>/<file_prefix>_YYYYMMDD_HH.csv

    Uwaga: limit całkowitego rozmiaru (np. 1 GB) można zaimplementować
    w osobnym module "sprzątającym" historię.
    """

    def __init__(
        self,
        base_path: Path | None = None,
        config: HistoryConfig | None = None,
    ) -> None:
        # Katalog modułu (tu leżą schema.yaml i values.yaml)
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        # Konfiguracja runtime
        self._config = config or HistoryConfig()
        self._load_config_from_file()

        # Katalog do logowania (może być względny względem katalogu modułu)
        self._log_dir = (self._base_path / self._config.log_dir).resolve()

        # Stan wewnętrzny
        self._last_write_ts: Optional[float] = None

    # --- ModuleInterface ---

    @property
    def id(self) -> str:
        return "history"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        """
        Jeden krok modułu historii.

        Co `interval_sec` sekund dopisujemy wiersz do odpowiedniego pliku CSV.
        """
        events: List[Event] = []
        outputs = Outputs()  # niczego nie sterujemy, tylko logujemy

        should_write = (
            self._last_write_ts is None
            or (now - self._last_write_ts) >= self._config.interval_sec
        )

        if should_write:
            try:
                self._write_row(now, sensors, system_state)
                self._last_write_ts = now
            except Exception as exc:
                # Zgłaszamy event, ale nie przerywamy pracy systemu
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.ERROR,
                        type="HISTORY_WRITE_ERROR",
                        message=f"Błąd zapisu historii: {exc}",
                        data={"exception": repr(exc)},
                    )
                )

        # Status modułu – kernel i tak to nadpisze, ale musimy zwrócić instancję
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- ZAPIS CSV ----------

    @staticmethod
    def _get_attr(obj: Any, *names: str) -> Any:
        """
        Pomocniczo: pobierz pierwsze istniejące pole z podanych nazw.
        Jeśli żadne nie istnieje – zwróć None.
        """
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _write_row(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> None:
        # Czas w lokalnej strefie (zależnie od systemu)
        ts = dt.datetime.fromtimestamp(now)
        ts_str = ts.isoformat(timespec="seconds")

        # Odczyt wartości z sensors/system_state
        temp_pieca = self._get_attr(sensors, "boiler_temp", "temp_boiler", "kociol_temp")
        power = self._get_attr(sensors, "boiler_power", "power", "power_kw")
        temp_grzejnikow = self._get_attr(
            sensors,
            "radiators_temp",
            "temp_radiators",
            "temp_co",
            "co_temp",
        )
        temp_spalin = self._get_attr(
            sensors,
            "exhaust_temp",
            "temp_exhaust",
            "flue_temp",
            "temp_spalin",
        )
        tryb_pracy = self._get_attr(
            system_state,
            "boiler_mode",
            "mode",
            "work_mode",
        )

        # Upewniamy się, że katalog istnieje
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # 1 plik na godzinę
        filename = f"{self._config.file_prefix}_{ts.strftime('%Y%m%d_%H')}.csv"
        file_path = self._log_dir / filename

        new_file = not file_path.exists()

        with file_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")

            if new_file:
                # Nagłówek
                writer.writerow(
                    [
                        "data_czas",
                        "temp_pieca",
                        "power",
                        "temp_grzejnikow",
                        "temp_spalin",
                        "tryb_pracy",
                    ]
                )

            # Wiersz danych
            writer.writerow(
                [
                    ts_str,
                    temp_pieca if temp_pieca is not None else "",
                    power if power is not None else "",
                    temp_grzejnikow if temp_grzejnikow is not None else "",
                    temp_spalin if temp_spalin is not None else "",
                    tryb_pracy if tryb_pracy is not None else "",
                ]
            )

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        """
        Zwraca schemat konfiguracji z pliku schema.yaml jako dict.
        """
        if not self._schema_path.exists():
            return {}

        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        """
        Zwraca aktualne wartości konfiguracji jako dict.
        """
        return {
            "log_dir": self._config.log_dir,
            "interval_sec": self._config.interval_sec,
            "file_prefix": self._config.file_prefix,
        }

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        """
        Aktualizuje konfigurację modułu na podstawie dict (np. z GUI).
        Opcjonalnie zapisuje do values.yaml.
        """
        if "log_dir" in values:
            self._config.log_dir = str(values["log_dir"])
            self._log_dir = (self._base_path / self._config.log_dir).resolve()

        if "interval_sec" in values:
            self._config.interval_sec = float(values["interval_sec"])

        if "file_prefix" in values:
            self._config.file_prefix = str(values["file_prefix"])

        if persist:
            self._save_config_to_file()

    # ---------- PLIK values.yaml ----------

    def _load_config_from_file(self) -> None:
        """
        Ładuje values.yaml (jeśli istnieje) i nadpisuje domyślne wartości.
        """
        if not self._config_path.exists():
            return

        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "log_dir" in data:
            self._config.log_dir = str(data["log_dir"])
        if "interval_sec" in data:
            self._config.interval_sec = float(data["interval_sec"])
        if "file_prefix" in data:
            self._config.file_prefix = str(data["file_prefix"])

    def _save_config_to_file(self) -> None:
        """
        Zapisuje aktualną konfigurację do values.yaml.
        """
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
