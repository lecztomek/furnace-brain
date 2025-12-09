from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import csv
from fastapi import APIRouter, HTTPException, Query


def create_history_router(
    base_dir: Path,
    log_dir: str = "history",
    file_prefix: str = "boiler",
) -> APIRouter:
    """
    Router z endpointami do odczytu historii pracy kotła.

    Zakładamy, że moduł `history` zapisuje pliki:
      <base_dir>/<log_dir>/<file_prefix>_YYYYMMDD_HH.csv
    z nagłówkiem:
      data_czas;temp_pieca;power;temp_grzejnikow;temp_spalin;tryb_pracy
    """
    router = APIRouter(prefix="/history", tags=["history"])
    history_path = (base_dir / log_dir).resolve()

    @router.get("/data")
    def get_history_data(
        from_ts: datetime = Query(
            ...,
            description="Początek zakresu czasu (ISO 8601, np. 2025-01-01T00:00:00)",
        ),
        to_ts: datetime = Query(
            ...,
            description="Koniec zakresu czasu (ISO 8601, np. 2025-01-01T23:59:59)",
        ),
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista nazw pól do zwrócenia, np. "
                "fields=temp_pieca&fields=power. "
                "Zawsze zwracane jest pole data_czas. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca dane historyczne z plików CSV w zadanym zakresie czasu.

        Przykład wywołania:
          GET /history/data?from_ts=2025-01-01T00:00:00&to_ts=2025-01-01T23:59:59
          GET /history/data?from_ts=...&to_ts=...&fields=temp_pieca&fields=power
        """
        if from_ts >= to_ts:
            raise HTTPException(
                status_code=400,
                detail="Parametr from_ts musi być wcześniejszy niż to_ts.",
            )

        if not history_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Katalog historii nie istnieje: {history_path}",
            )

        items: List[Dict[str, Any]] = []

        # Przechodzimy po wszystkich plikach z prefixem, sortujemy po nazwie
        # (co mniej więcej odpowiada porządkowi czasowemu).
        for path in sorted(history_path.glob(f"{file_prefix}_*.csv")):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    for row in reader:
                        ts_str = row.get("data_czas")
                        if not ts_str:
                            continue

                        try:
                            ts = datetime.fromisoformat(ts_str)
                        except ValueError:
                            # Pomijamy wiersze z błędnym timestampem
                            continue

                        if ts < from_ts or ts > to_ts:
                            continue

                        if fields:
                            # Zawsze zwracamy data_czas
                            filtered: Dict[str, Any] = {
                                "data_czas": ts_str,
                            }
                            for key in fields:
                                if key in row:
                                    filtered[key] = row[key]
                            items.append(filtered)
                        else:
                            # Zwracamy cały wiersz z CSV
                            items.append(row)
            except OSError as exc:
                # Błąd odczytu pojedynczego pliku – logujemy w przyszłości
                # (na razie tylko przeskakujemy ten plik).
                continue

        return {
            "from_ts": from_ts.isoformat(),
            "to_ts": to_ts.isoformat(),
            "count": len(items),
            "items": items,
        }

    @router.get("/fields")
    def get_available_fields():
        """
        Zwraca listę dostępnych pól (kolumn) na podstawie pierwszego znalezionego pliku CSV.

        Może się przydać GUI, żeby wiedziało, jakie parametry można odpytywać.
        """
        if not history_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Katalog historii nie istnieje: {history_path}",
            )

        for path in sorted(history_path.glob(f"{file_prefix}_*.csv")):
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
