"""
Ustawienia -> Sprzęt: which output (GPIO) drives what and which DS18B20 is which sensor.
The module owns these settings: it checks a save (validate_values - one function on two
outputs etc. is refused) and passes a change to the hardware at once (bind_hardware).
The wiring at the start is read by the hardware layer (backend.hw.wiring.load_file).
The tick does nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.state import ModuleStatus, PartialOutputs, Sensors, SystemState
from backend.hw import wiring


class HardwareModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._values: Dict[str, Any] = wiring.defaults()
        self._hardware: Any = None
        self._problems: Optional[List[str]] = None
        self.reload_config_from_file()

    def bind_hardware(self, hardware: Any, problems: List[str]) -> None:
        """The hardware a saved change goes to, and the list of start-up problems (shown in
        Diagnostyka Pi) that a good save clears."""
        self._hardware = hardware
        self._problems = problems

    @staticmethod
    def validate_values(values: Dict[str, Any]) -> List[str]:
        """Checked by the settings store before a save is written."""
        return wiring.validate(values)

    @property
    def id(self) -> str:
        return "hardware"

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
                self._values = {**wiring.defaults(), **(yaml.safe_load(f) or {})}
        self._apply()

    def _apply(self) -> None:
        """A saved change to the hardware at once (outputs LOW first, see RpiHardware.reconfigure)."""
        if self._hardware is None:
            return
        problems = wiring.validate(self._values)
        if problems:   # the settings page refuses these - a hand-edited file only
            raise ValueError("; ".join(problems))
        reconfigure = getattr(self._hardware, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(wiring.pins(self._values), wiring.rom_to_field(self._values))
        if self._problems is not None:
            del self._problems[:]
