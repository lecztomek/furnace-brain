"""
Wygląd: which GUI the controller's address opens - the current one or the new one (/v2/).
Only a setting, read by the main page of the current GUI (it moves to /v2/ when "nowy").
Drives nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import ModuleStatus, PartialOutputs, Sensors, SystemState

DEFAULTS = {"default_gui": "stary"}


class GuiModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, data_root: Optional[Path] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._values: Dict[str, Any] = dict(DEFAULTS)
        self.reload_config_from_file()

    @property
    def id(self) -> str:
        return "gui"

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        return ModuleTickResult(partial_outputs=PartialOutputs(), events=[], status=status)

    def get_config_schema(self) -> Dict[str, Any]:
        with (self._base_path / "schema.yaml").open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def get_config_values(self) -> Dict[str, Any]:
        return dict(self._values)

    def set_config_values(self, values: Dict[str, Any], persist: bool = True) -> None:
        self._values.update(values)
        if persist:
            with (self._base_path / "values.yaml").open("w", encoding="utf-8") as f:
                yaml.safe_dump(self._values, f, allow_unicode=True)

    def reload_config_from_file(self) -> None:
        path = self._base_path / "values.yaml"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                self._values = {**DEFAULTS, **(yaml.safe_load(f) or {})}
