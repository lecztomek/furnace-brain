from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import csv
import json
from fastapi import APIRouter, HTTPException, Query


def create_logs_router(
    base_dir: Path,
    log_dir: str = "data",
    file_prefix: str = "events",
) -> APIRouter:
    """
    Router do odczytu logów zapisanych na dysk przez EventLogModule.

    Pliki:
      <base_dir>/<log_dir>/<file_prefix>_YYYYMMDD_HH.csv  (rotacja godzinowa)
      <base_dir>/<log_dir>/<file_prefix>_YYYYMMDD.csv     (rotacja dzienna)

    Nagłówek:
      data_czas;ts_epoch;level;source;type;message;data_json
    """
    router = APIRouter(prefix="/logs", tags=["logs"])
    logs_path = (base_dir / log_dir).resolve()

    def _ensure_dir():
        if not logs_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Katalog logów nie istnieje: {logs_path}",
            )

    def _iter_files_sorted(reverse: bool = False):
        # sort po nazwie ~= sort czasowy, jak w historii
        return sorted(logs_path.glob(f"{file_prefix}_*.csv"), reverse=reverse)

    def _parse_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        ts_epoch_raw = row.get("ts_epoch") or ""
        try:
            ts_epoch = float(ts_epoch_raw)
        except (TypeError, ValueError):
            # fallback na data_czas
            ts_str = row.get("data_czas")
            if not ts_str:
                return None
            try:
                ts_dt = datetime.fromisoformat(ts_str)
            except ValueError:
                return None
            ts_epoch = ts_dt.timestamp()

        # data_czas w pliku jest lokalna (tak jak moduł historii)
        # zwróćmy też iso z datetime (też lokalny, bez strefy)
        ts_dt_local = datetime.fromtimestamp(ts_epoch)
        ts_iso = ts_dt_local.isoformat(timespec="seconds")

        data_json_raw = row.get("data_json") or "{}"
        try:
            data_obj = json.loads(data_json_raw) if data_json_raw else {}
        except json.JSONDecodeError:
            data_obj = {"_raw": data_json_raw}

        return {
            "ts": ts_epoch,
            "ts_iso": ts_iso,
            "data_czas": row.get("data_czas") or ts_iso,
            "level": row.get("level") or "",
            "source": row.get("source") or "",
            "type": row.get("type") or "",
            "message": row.get("message") or "",
            "data": data_obj,
        }

    @router.get("/data")
    def get_logs_data(
        from_ts: datetime = Query(
            ...,
            description="Początek zakresu czasu (ISO 8601, np. 2025-01-01T00:00:00)",
        ),
        to_ts: datetime = Query(
            ...,
            description="Koniec zakresu czasu (ISO 8601, np. 2025-01-01T23:59:59)",
        ),
        level: Optional[str] = Query(None, description="INFO/WARNING/ERROR/ALARM (opcjonalnie)"),
        source: Optional[str] = Query(None, description="np. kernel/blower/feeder/safety (opcjonalnie)"),
        type: Optional[str] = Query(None, description="np. MODULE_ERROR (opcjonalnie)"),
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista nazw pól do zwrócenia. Zawsze zwracane jest data_czas. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca logi z plików CSV w zadanym zakresie czasu.

        Przykłady:
          GET /logs/data?from_ts=2025-01-01T00:00:00&to_ts=2025-01-01T23:59:59
          GET /logs/data?from_ts=...&to_ts=...&level=ERROR
          GET /logs/data?from_ts=...&to_ts=...&fields=data_czas&fields=level&fields=message
        """
        if from_ts >= to_ts:
            raise HTTPException(
                status_code=400,
                detail="Parametr from_ts musi być wcześniejszy niż to_ts.",
            )

        _ensure_dir()

        items: List[Dict[str, Any]] = []
        from_epoch = from_ts.timestamp()
        to_epoch = to_ts.timestamp()

        for path in _iter_files_sorted(reverse=False):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    for row in reader:
                        parsed = _parse_row(row)
                        if not parsed:
                            continue

                        ts = parsed["ts"]
                        if ts < from_epoch or ts > to_epoch:
                            continue

                        if level and (parsed["level"] != level):
                            continue
                        if source and (parsed["source"] != source):
                            continue
                        if type and (parsed["type"] != type):
                            continue

                        if fields:
                            filtered: Dict[str, Any] = {"data_czas": parsed["data_czas"]}
                            for key in fields:
                                if key == "data_json":
                                    filtered["data"] = parsed["data"]
                                elif key in parsed:
                                    filtered[key] = parsed[key]
                            items.append(filtered)
                        else:
                            items.append(parsed)

            except OSError:
                continue

        return {
            "from_ts": from_ts.isoformat(),
            "to_ts": to_ts.isoformat(),
            "count": len(items),
            "items": items,
        }

    @router.get("/recent")
    def get_logs_recent(
        limit: int = Query(100, ge=1, le=2000),
        level: Optional[str] = Query(None, description="INFO/WARNING/ERROR/ALARM (opcjonalnie)"),
        source: Optional[str] = Query(None, description="np. kernel/blower/feeder/safety (opcjonalnie)"),
        type: Optional[str] = Query(None, description="np. MODULE_ERROR (opcjonalnie)"),
    ):
        """
        Zwraca najnowsze wpisy z plików CSV (od końca), bez pollingu po stronie klienta.

        Przykłady:
          GET /logs/recent?limit=200
          GET /logs/recent?level=ERROR
        """
        _ensure_dir()

        out: List[Dict[str, Any]] = []

        # czytamy pliki od najnowszego
        for path in _iter_files_sorted(reverse=True):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    rows = list(reader)
            except OSError:
                continue

            # od końca pliku (najnowsze wiersze zwykle na końcu)
            for row in reversed(rows):
                parsed = _parse_row(row)
                if not parsed:
                    continue

                if level and (parsed["level"] != level):
                    continue
                if source and (parsed["source"] != source):
                    continue
                if type and (parsed["type"] != type):
                    continue

                out.append(parsed)
                if len(out) >= limit:
                    break

            if len(out) >= limit:
                break

        # już jest w kolejności newest->oldest
        return {
            "count": len(out),
            "items": out,
        }

    @router.get("/fields")
    def get_available_fields():
        """
        Zwraca listę dostępnych pól (kolumn) na podstawie pierwszego znalezionego pliku CSV.
        """
        _ensure_dir()

        for path in _iter_files_sorted(reverse=False):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.reader(f, delimiter=";")
                    header = next(reader, None)
                    if header:
                        return {"fields": header}
            except OSError:
                continue

        return {"fields": []}

    return router
