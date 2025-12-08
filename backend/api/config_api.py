# backend/api/config_api.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Body

from ..core.config_store import ConfigStore


def create_config_router(config_store: ConfigStore, kernel: Kernel) -> APIRouter:
    """
    Router z endpointami:
      GET  /config/modules
      GET  /config/schema/{module_id}
      GET  /config/values/{module_id}
      PUT  /config/values/{module_id}
    """
    router = APIRouter(prefix="/config", tags=["config"])

    @router.get("/modules")
    def list_config_modules():
        """
        Lista modułów, dla których jest dostępna konfiguracja.
        """
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
        """
        Zwraca schemę konfiguracji dla konkretnego modułu.
        """
        try:
            schema = config_store.get_schema(module_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown module '{module_id}'")
        return schema

    @router.get("/values/{module_id}")
    def get_config_values(module_id: str):
        """
        Zwraca aktualne (scalone) wartości konfiguracji dla modułu.
        """
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
        """
        Zapisuje nowe wartości konfiguracji dla modułu.
        """
        try:
            # 1) walidacja + zapis values.yaml
            validated = config_store.set_values(module_id, values)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown module '{module_id}'")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # 2) jeśli mamy kernela – powiadom moduł, żeby sam przeładował values.yaml
        if kernel is not None:
            kernel.reload_module_config_from_file(module_id)

        # 3) zwracamy zwalidowane wartości
        return validated

    return router
