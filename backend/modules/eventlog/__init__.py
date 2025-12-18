from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import csv
import datetime as dt
import json
import yaml  # pip install pyyaml

from backend.core.kernel import ModuleInterface, ModuleTickResult
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
class EventLogConfig:
    """
    Konfiguracja modułu logów eventów.

    log_dir        – katalog na pliki CSV.
    file_prefix    – prefiks nazwy pliku.
    rotate         – rotacja plików: "hour" albo "day".
    """
    log_dir: str = "data"
    file_prefix: str = "events"
    rotate: str = "hour"  # "hour" | "day"


class EventLogModule(ModuleInterface):
    """
    Moduł logów – zapisuje eventy (SystemState.recent_events) do CSV.

    UWAGA: kernel ustawia state.recent_events = all_events na końcu kroku.
    Ten moduł w praktyce zapisuje eventy z POPRZEDNIEGO ticka (bo je widzi
    na wejściu do tick()) – bez modyfikacji kernela to jest OK.
    """

    def __init__(
        self,
        base_path: Path | None = None,
        config: EventLogConfig | None = None,
    ) -> None:
        if base_path is None:
            self._base_path = Path(__file__).resolve().parent
        else:
            self._base_path = base_path

        self._schema_path = self._base_path / "schema.yaml"
        self._config_path = self._base_path / "values.yaml"

        self._config = config or EventLogConfig()
        self._load_config_from_file()

        self._log_dir = (self._base_path / self._config.log_dir).resolve()

        # deduplikacja / “cursor”
        self._last_flushed_ts: Optional[float] = None
        self._last_ts_fingerprints: Set[str] = set()

    @property
    def id(self) -> str:
        return "eventlog"

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        events: List[Event] = []
        outputs = PartialOutputs()  # niczego nie sterujemy

        # bierzemy snapshot eventów dostępnych w state (to jest poprzedni tick)
        pending = list(system_state.recent_events or [])

        if pending:
            try:
                self._write_events(pending)
            except Exception as exc:
                events.append(
                    Event(
                        ts=now,
                        source=self.id,
                        level=EventLevel.ERROR,
                        type="EVENTLOG_WRITE_ERROR",
                        message=f"Błąd zapisu logów: {exc}",
                        data={"exception": repr(exc)},
                    )
                )

        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)

        return ModuleTickResult(
            partial_outputs=outputs,
            events=events,
            status=status,
        )

    # ---------- ZAPIS ----------

    def _fingerprint(self, e: Event) -> str:
        # stabilny fingerprint (żeby nie dublić przy tym samym ts)
        payload = {
            "ts": e.ts,
            "source": e.source,
            "level": getattr(e.level, "name", str(e.level)),
            "type": e.type,
            "message": e.message,
            "data": e.data or {},
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    def _file_path_for_ts(self, ts: float) -> Path:
        t = dt.datetime.fromtimestamp(ts)
        if self._config.rotate == "day":
            suffix = t.strftime("%Y%m%d")
        else:
            suffix = t.strftime("%Y%m%d_%H")
        filename = f"{self._config.file_prefix}_{suffix}.csv"
        return self._log_dir / filename

    def _write_events(self, pending: List[Event]) -> None:
        # sort dla powtarzalności
        pending.sort(key=lambda e: (e.ts, e.source, e.type, e.message))

        # filtr: tylko “nowe” względem kursora
        to_write: List[Event] = []
        for e in pending:
            fp = self._fingerprint(e)

            if self._last_flushed_ts is None:
                to_write.append(e)
                continue

            if e.ts > self._last_flushed_ts:
                to_write.append(e)
                continue

            if e.ts == self._last_flushed_ts and fp not in self._last_ts_fingerprints:
                to_write.append(e)
                continue

        if not to_write:
            return

        # katalog
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # grupujemy po pliku (rotacja hour/day) wg TS eventu
        buckets: Dict[Path, List[Event]] = {}
        for e in to_write:
            p = self._file_path_for_ts(e.ts)
            buckets.setdefault(p, []).append(e)

        for path, items in buckets.items():
            new_file = not path.exists()

            with path.open("a", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter=";")

                if new_file:
                    w.writerow(
                        [
                            "data_czas",     # ISO lokalny
                            "ts_epoch",      # float
                            "level",         # INFO/WARNING/ERROR/ALARM
                            "source",
                            "type",
                            "message",
                            "data_json",     # JSON
                        ]
                    )

                for e in items:
                    t = dt.datetime.fromtimestamp(e.ts)
                    ts_str = t.isoformat(timespec="seconds")

                    level = e.level.name if hasattr(e.level, "name") else str(e.level)
                    data_json = json.dumps(e.data or {}, ensure_ascii=False, separators=(",", ":"))

                    w.writerow(
                        [
                            ts_str,
                            f"{e.ts:.3f}",
                            level,
                            e.source,
                            e.type,
                            e.message,
                            data_json,
                        ]
                    )

        # update kursora (ostatni zapisany ts + fingerprints)
        max_ts = max(e.ts for e in to_write)
        if self._last_flushed_ts is None or max_ts > self._last_flushed_ts:
            self._last_flushed_ts = max_ts
            self._last_ts_fingerprints = set()

        # dodaj fingerprints dla max_ts (żeby nie dublić przy kolejnym ticku)
        for e in to_write:
            if e.ts == self._last_flushed_ts:
                self._last_ts_fingerprints.add(self._fingerprint(e))

    # ---------- CONFIG (schema + values) ----------

    def get_config_schema(self) -> Dict[str, Any]:
        if not self._schema_path.exists():
            return {}
        with self._schema_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return {
            "log_dir": self._config.log_dir,
            "file_prefix": self._config.file_prefix,
            "rotate": self._config.rotate,
        }

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        if "log_dir" in values:
            self._config.log_dir = str(values["log_dir"])
            self._log_dir = (self._base_path / self._config.log_dir).resolve()

        if "file_prefix" in values:
            self._config.file_prefix = str(values["file_prefix"])

        if "rotate" in values:
            rot = str(values["rotate"]).lower()
            if rot not in ("hour", "day"):
                rot = "hour"
            self._config.rotate = rot

        if persist:
            self._save_config_to_file()

    def reload_config_from_file(self) -> None:
        self._load_config_from_file()
        self._log_dir = (self._base_path / self._config.log_dir).resolve()

    def _load_config_from_file(self) -> None:
        if not self._config_path.exists():
            return
        with self._config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "log_dir" in data:
            self._config.log_dir = str(data["log_dir"])
        if "file_prefix" in data:
            self._config.file_prefix = str(data["file_prefix"])
        if "rotate" in data:
            rot = str(data["rotate"]).lower()
            self._config.rotate = rot if rot in ("hour", "day") else "hour"

    def _save_config_to_file(self) -> None:
        data = asdict(self._config)
        with self._config_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True, allow_unicode=True)
