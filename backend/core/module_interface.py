# backend/core/module_interface.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from typing_extensions import Protocol

from backend.core.state import (
    Event,
    ModuleStatus,
    PartialOutputs,
    Sensors,
    SystemState,
)


@dataclass
class ModuleTickResult:
    """
    Wynik pojedynczego wywołania modułu w jednym kroku pętli.
    """
    partial_outputs: PartialOutputs   # None = nie ruszaj, wartość = ustaw (nawet False/0)
    events: List[Event]
    status: ModuleStatus


class ModuleInterface(Protocol):
    @property
    def id(self) -> str:
        ...

    def tick(
        self,
        now: float,
        sensors: Sensors,
        system_state: SystemState,
    ) -> ModuleTickResult:
        ...

    def get_config_schema(self) -> Dict[str, Any]:
        ...

    def get_config_values(self) -> Dict[str, Any]:
        ...

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        ...

    def reload_config_from_file(self) -> None:
        ...
