from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.core.state import SystemState
from backend.core.state_store import StateStore


def create_stats_router(
    store: StateStore,
    module_id: str = "stats",
) -> APIRouter:
    """
    Router do odczytu runtime statystyk.

    Wymaganie:
      SystemState.runtime: Dict[str, Dict[str, Any]]
      a moduł "stats" zapisuje:
        system_state.runtime["stats"] = {...}
    """
    router = APIRouter(prefix="/stats", tags=["stats"])

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

        # Kopia, żeby serializacja nie złapała zmian w trakcie ticka
        return dict(data)

    @router.get("/data")
    def get_stats_data(
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista kluczy statystyk do zwrócenia, np. "
                "fields=burn_kgph_5m&fields=power_kw_1h. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca aktualne statystyki z system_state.runtime['stats'].
        """
        try:
            state = store.snapshot()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Nie udało się pobrać SystemState.") from exc

        stats = _get_stats_dict(state)

        ts_unix = float(getattr(state, "ts", 0.0) or 0.0)
        ts_iso = datetime.fromtimestamp(ts_unix).isoformat() if ts_unix > 0 else None

        if fields:
            payload_stats: Dict[str, Any] = {"ts_unix": ts_unix, "ts_iso": ts_iso}
            for key in fields:
                if key in stats:
                    payload_stats[key] = stats[key]
        else:
            payload_stats = {"ts_unix": ts_unix, "ts_iso": ts_iso, **stats}

        return {
            "module_id": module_id,
            "count": len(payload_stats),
            "data": payload_stats,
        }

    @router.get("/fields")
    def get_available_fields():
        """
        Zwraca listę dostępnych pól na podstawie aktualnych danych runtime.
        """
        try:
            state = store.snapshot()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Nie udało się pobrać SystemState.") from exc

        stats = _get_stats_dict(state)
        return {"module_id": module_id, "fields": sorted(stats.keys())}

    return router
