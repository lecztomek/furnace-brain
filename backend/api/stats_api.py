from __future__ import annotations

from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import csv
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from backend.core.state import SystemState
from backend.core.state_store import StateStore


def create_stats_router(
    store: StateStore,
    base_dir: Path,
    log_dir: str = "data",
    file_prefix_5m: str = "stats5m",
    daily_file: str = "stats_daily.csv",
    module_id: str = "stats",
    timezone: str = "Europe/Warsaw",
) -> APIRouter:
    """
    Backward compatible API:

    - /stats/data   -> LIVE runtime (tak jak stare GUI oczekuje)
    - /stats/series -> SERIA 5m z CSV w zakresie czasu (history-like)
    - /stats/daily  -> SERIA dzienna z stats_daily.csv
    - /stats/fields -> kolumny dla CSV 5m
    - /stats/daily/fields -> kolumny dla daily
    """
    router = APIRouter(prefix="/stats", tags=["stats"])
    stats_path = (base_dir / log_dir).resolve()
    daily_path = stats_path / daily_file
    tz = ZoneInfo(timezone)

    # ---------------- helpers ----------------

    def _ensure_dir() -> None:
        if not stats_path.exists():
            raise HTTPException(status_code=404, detail=f"Katalog stats nie istnieje: {stats_path}")

    def _coerce_dt(d: datetime) -> datetime:
        # jeśli przyjdzie naive -> traktuj jako lokalny czas routera
        if d.tzinfo is None:
            return d.replace(tzinfo=tz)
        return d

    def _get_stats_dict(state: SystemState) -> Dict[str, Any]:
        runtime = getattr(state, "runtime", None)
        if not isinstance(runtime, dict):
            raise HTTPException(
                status_code=500,
                detail="SystemState.runtime nie jest dostępne. Dodaj pole runtime do SystemState.",
            )

        data = runtime.get(module_id)
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Brak danych runtime dla modułu '{module_id}'. "
                    "Moduł może być wyłączony lub jeszcze nie policzył statystyk."
                ),
            )
        return dict(data)

    # ---------------- LIVE (GUI endpoint) ----------------

    @router.get("/data")
    def get_stats_data(
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista kluczy statystyk do zwrócenia, np. "
                "fields=burn_kgph_5m&fields=calendar&fields=compare_bars. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca aktualne statystyki z system_state.runtime['stats'].
        (Backward compatible z GUI, które woła /stats/data bez from/to.)
        """
        try:
            state = store.snapshot()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Nie udało się pobrać SystemState.") from exc

        stats = _get_stats_dict(state)

        ts_unix = float(getattr(state, "ts", 0.0) or 0.0)
        ts_iso = datetime.fromtimestamp(ts_unix).isoformat() if ts_unix > 0 else None

        if fields:
            payload: Dict[str, Any] = {"ts_unix": ts_unix, "ts_iso": ts_iso}
            for key in fields:
                if key in stats:
                    payload[key] = stats[key]
        else:
            payload = {"ts_unix": ts_unix, "ts_iso": ts_iso, **stats}

        return {"module_id": module_id, "count": len(payload), "data": payload}

    # ---------------- SERIA 5m z CSV (history-like) ----------------

    @router.get("/series")
    def get_stats_series(
        from_ts: datetime = Query(
            ...,
            description="Początek zakresu czasu (ISO 8601). Najlepiej z offsetem, np. 2025-01-01T00:00:00+01:00",
        ),
        to_ts: datetime = Query(
            ...,
            description="Koniec zakresu czasu (ISO 8601). Najlepiej z offsetem.",
        ),
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista nazw pól do zwrócenia z CSV 5m, np. "
                "fields=coal_kg&fields=burn_kgph. "
                "Zawsze zwracane jest ts_end_iso. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca buckety 5m z plików CSV w zadanym zakresie czasu.
        """
        _ensure_dir()

        from_dt = _coerce_dt(from_ts)
        to_dt = _coerce_dt(to_ts)

        if from_dt >= to_dt:
            raise HTTPException(status_code=400, detail="Parametr from_ts musi być wcześniejszy niż to_ts.")

        items: List[Dict[str, Any]] = []

        for path in sorted(stats_path.glob(f"{file_prefix_5m}_*.csv")):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    for row in reader:
                        ts_str = (row.get("ts_end_iso") or "").strip()
                        if not ts_str:
                            continue
                        try:
                            ts = datetime.fromisoformat(ts_str)
                        except ValueError:
                            continue

                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=tz)

                        if ts < from_dt or ts > to_dt:
                            continue

                        if fields:
                            filtered: Dict[str, Any] = {"ts_end_iso": ts_str}
                            for key in fields:
                                if key in row:
                                    filtered[key] = row[key]
                            items.append(filtered)
                        else:
                            items.append(row)
            except OSError:
                continue

        return {
            "from_ts": from_dt.isoformat(),
            "to_ts": to_dt.isoformat(),
            "count": len(items),
            "items": items,
        }

    @router.get("/fields")
    def get_stats_fields():
        """
        Zwraca listę kolumn dla CSV 5m na podstawie pierwszego znalezionego pliku.
        """
        _ensure_dir()

        for path in sorted(stats_path.glob(f"{file_prefix_5m}_*.csv")):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.reader(f, delimiter=";")
                    header = next(reader, None)
                    if header:
                        return {"fields": header}
            except OSError:
                continue

        return {"fields": []}

    # ---------------- SERIA DZIENNA (stats_daily.csv) ----------------

    @router.get("/daily")
    def get_stats_daily(
        from_date: date = Query(..., description="Początek zakresu dat (YYYY-MM-DD)"),
        to_date: date = Query(..., description="Koniec zakresu dat (YYYY-MM-DD), włącznie"),
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista nazw pól do zwrócenia z CSV dziennego, np. "
                "fields=coal_kg_sum&fields=burn_kgph_avg. "
                "Zawsze zwracane jest date. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca agregaty dzienne z stats_daily.csv w zadanym zakresie dat.
        """
        _ensure_dir()

        if from_date > to_date:
            raise HTTPException(status_code=400, detail="Parametr from_date musi być <= to_date.")

        if not daily_path.exists():
            raise HTTPException(status_code=404, detail=f"Plik cache dziennego nie istnieje: {daily_path}")

        items: List[Dict[str, Any]] = []

        try:
            with daily_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    d_str = (row.get("date") or "").strip()
                    if not d_str:
                        continue
                    try:
                        d = date.fromisoformat(d_str)
                    except ValueError:
                        continue

                    if d < from_date or d > to_date:
                        continue

                    if fields:
                        filtered: Dict[str, Any] = {"date": d_str}
                        for key in fields:
                            if key in row:
                                filtered[key] = row[key]
                        items.append(filtered)
                    else:
                        items.append(row)
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"Nie udało się odczytać {daily_path}") from exc

        return {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "count": len(items),
            "items": items,
        }

    @router.get("/daily/fields")
    def get_stats_daily_fields():
        """
        Zwraca listę kolumn dla stats_daily.csv.
        """
        _ensure_dir()

        if not daily_path.exists():
            return {"fields": []}

        try:
            with daily_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f, delimiter=";")
                header = next(reader, None)
                return {"fields": header or []}
        except OSError:
            return {"fields": []}

    return router
