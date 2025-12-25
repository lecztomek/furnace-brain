from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
import csv
import datetime as dt
import yaml  # pip install pyyaml

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # Python 3.7/3.8

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import (
    Event,
    EventLevel,
    ModuleStatus,
    Sensors,
    SystemState,
    PartialOutputs,
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
    log_dir: str = "data"
    interval_sec: float = 30.0
    file_prefix: str = "boiler"
    timezone: str = "Europe/Warsaw"

class HistoryModule(ModuleInterface):
    def __init__(
        self,
        *,
        data_root: Path,                 # <<< WYMAGANE
        base_path: Path | None = None,
        config: HistoryConfig | None = None,
    ) -> None:
        if data_root is None:
            raise ValueError("HistoryModule: data_root is required")

        self._data_root = data_root.resolve()
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or HistoryConfig()
        self._load_config_from_file()

        self._tz = ZoneInfo(self._config.timezone)

        self._persist_root = (self._data_root / "modules" / self.id).resolve()
        self._log_dir = (self._persist_root / self._config.log_dir).resolve()

        # Stan wewnętrzny: ostatni zapis wg czasu monotonicznego
        self._last_write_mono: Optional[float] = None

    @property
    def id(self) -> str:
        return "history"

    def tick(
        self,
        now: float,              # wall-clock z kernela (time.time())
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()


        now_mono: float = system_state.ts_mono

        interval = float(self._config.interval_sec)
        should_write = (
            self._last_write_mono is None
            or (now_mono - self._last_write_mono) >= interval
        )

        now_mono: float = system_state.ts_mono

        anchor_wall = float(now)
        anchor_mono = float(now_mono)

        log_now = float(self._mono_to_wall(now_mono, anchor_wall, anchor_mono))

        if should_write:
            try:
                self._write_row(log_now, sensors, system_state)  # zapis czasu wall-clock do CSV
                self._last_write_mono = now_mono
            except Exception as exc:
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

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- ZAPIS CSV ----------

    @staticmethod
    def _get_attr(obj: Any, *names: str) -> Any:
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _mono_to_wall(self, ts_mono: float, anchor_wall: float, anchor_mono: float) -> float:
        # wall(ts) ~= anchor_wall - (anchor_mono - ts_mono)
        return anchor_wall - (anchor_mono - ts_mono)


    def _write_row(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> None:
        ts = dt.datetime.fromtimestamp(now)
        ts_str = ts.isoformat(timespec="seconds")

        temp_pieca = sensors.boiler_temp
        power = system_state.outputs.power_percent
        temp_grzejnikow = sensors.radiators_temp
        temp_spalin = sensors.flue_gas_temp

        mode = system_state.mode
        tryb_pracy = mode.name if mode is not None else None

        self._log_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{self._config.file_prefix}_{ts.strftime('%Y%m%d_%H')}.csv"
        file_path = self._log_dir / filename
        new_file = not file_path.exists()

        with file_path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")

            if new_file:
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
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return {
            "log_dir": self._config.log_dir,
            "interval_sec": self._config.interval_sec,
            "file_prefix": self._config.file_prefix,
        }

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "log_dir" in values:
            self._config.log_dir = str(values["log_dir"])
            self._log_dir = (self._base_path / self._config.log_dir).resolve()

        if "interval_sec" in values:
            self._config.interval_sec = float(values["interval_sec"])

        if "file_prefix" in values:
            self._config.file_prefix = str(values["file_prefix"])

        if persist:
            self._save_config_to_file()

    def _load_config_from_file(self) -> None:
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
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)

