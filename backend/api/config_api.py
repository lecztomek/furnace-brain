# backend/api/config_api.py
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Body

from ..core.config_store import ConfigStore
from ..core.state import Event, EventLevel


def _short(v: Any) -> str:
    if isinstance(v, bool):
        return "wł." if v else "wył."
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).replace(".", ",") if isinstance(v, (int, float)) else str(v)


def create_config_router(
    config_store: ConfigStore,
    reload_module_config: Optional[Callable[[str], None]] = None,
    publish_events: Optional[Callable[[list], None]] = None,
    now: Callable[[], float] = time.time,   # the furnace clock: the simulator's runs faster
) -> APIRouter:
    """
    Router z endpointami:
      GET  /config/modules
      GET  /config/schema/{module_id}
      GET  /config/values/{module_id}
      PUT  /config/values/{module_id}
    """
    router = APIRouter(prefix="/config", tags=["config"])

    def current(module_id: str) -> Dict[str, Any]:
        try:
            return dict(config_store.get_values(module_id))
        except (KeyError, ValueError, OSError):
            return {}

    def log_changes(module_id: str, before: Dict[str, Any], after: Dict[str, Any]) -> None:
        """SETTINGS_CHANGED with old -> new of every changed key (the service page compares before / after)."""
        if publish_events is None:
            return
        changes = {k: [before.get(k), v] for k, v in after.items() if k in before and before.get(k) != v}
        if not changes:
            return
        text = ", ".join(f"{k} {_short(o)} → {_short(n)}" for k, (o, n) in changes.items())
        publish_events([Event(ts=now(), source="config", level=EventLevel.INFO, type="SETTINGS_CHANGED",
                              message=f"Zmiana ustawień {module_id}: {text}",
                              data={"module": module_id, "changes": changes})])

    @router.get("/modules")
    def list_config_modules():
        modules = config_store.list_modules()
        return [
            {
                "id": m.id,
                "name": m.name,
                "description": m.description,
            }
            for m in modules
        ]

    @router.get("/schema/{module_id}")
    def get_config_schema(module_id: str):
        try:
            schema = config_store.get_schema(module_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown module '{module_id}'")
        return schema

    @router.get("/values/{module_id}")
    def get_config_values(module_id: str):
        try:
            values = config_store.get_values(module_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown module '{module_id}'")
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return values

    @router.put("/values/{module_id}")
    def set_config_values(
        module_id: str,
        values: dict = Body(..., description="Mapa klucz->wartość zgodna z schema"),
    ):
        before = current(module_id)
        try:
            validated = config_store.set_values(module_id, values)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown module '{module_id}'")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # opcjonalnie: zleć przeładowanie configu modułu (bez zależności API->Kernel)
        if reload_module_config is not None:
            try:
                reload_module_config(module_id)
            except Exception as exc:
                # nie wywalamy całego requestu jeśli reload się wywali,
                # ale informujemy klienta (możesz też zrobić 500 jeśli wolisz)
                raise HTTPException(status_code=500, detail=f"Config saved, but reload failed: {exc}") from exc

        log_changes(module_id, before, validated)
        return validated
    
    @router.get("/value/{module_id}/{key}")
    def get_config_value(module_id: str, key: str):
        try:
            value = config_store.get_value(module_id, key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return {"key": key, "value": value}

    @router.put("/value/{module_id}/{key}")
    def set_config_value(
        module_id: str,
        key: str,
        value: object = Body(..., description="Pojedyncza wartość (np. liczba/tekst/bool)"),
    ):
        before = current(module_id)
        try:
            validated_value = config_store.set_value(module_id, key, value)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if reload_module_config is not None:
            try:
                reload_module_config(module_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Config saved, but reload failed: {exc}",
                ) from exc

        log_changes(module_id, before, {key: validated_value})
        return {"key": key, "value": validated_value}

    return router
