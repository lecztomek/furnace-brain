"""
Historia ustawień: keeps a record of how the furnace was set up. Every setting of every
module with the app version and commit goes to the USB stick (backend.core.settings_log):
at the start (each update), when a values.yaml changes (from the GUI or edited by hand -
the files' times are checked every check_every_s) and once a day. Nothing else.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from backend.config.modules_loader import load_module_descriptors
from backend.core.config_store import ConfigStore
from backend.core.module_interface import ModuleInterface, ModuleTickResult
from backend.core.settings_log import SettingsLog, git_commit
from backend.core.state import ModuleStatus, PartialOutputs, Sensors, SystemState
from backend.core.version import detect_version

DEFAULTS = {"enabled": True, "check_every_s": 10.0}


class SettingsLogModule(ModuleInterface):
    def __init__(self, base_path: Optional[Path] = None, data_root: Optional[Path] = None) -> None:
        self._base_path = base_path or Path(__file__).resolve().parent
        self._modules_root = self._base_path.parent
        self._values: Dict[str, Any] = dict(DEFAULTS)
        self.reload_config_from_file()
        root = Path(data_root) if data_root is not None else self._base_path
        app_root = self._modules_root.parent.parent
        self._store = ConfigStore(self._modules_root, [d.id for d in load_module_descriptors()])
        self._log = SettingsLog(
            root / "modules" / "settings_log" / "data" if data_root is not None else root / "data",
            snapshot=lambda: {m: self._store.get_values(m) for m in self._store.module_ids},
            version=detect_version(app_root),
            commit=git_commit(app_root),
        )
        self._next_check = 0.0
        self._files: Optional[Tuple] = None

    @property
    def id(self) -> str:
        return "settings_log"

    def _file_times(self) -> Tuple:
        out = []
        for m in self._store.module_ids:
            try:
                out.append((m, (self._modules_root / m / "values.yaml").stat().st_mtime))
            except OSError:
                out.append((m, None))
        return tuple(out)

    def tick(self, now: float, sensors: Sensors, system_state: SystemState) -> ModuleTickResult:
        status = system_state.modules.get(self.id) or ModuleStatus(id=self.id)
        if self._values.get("enabled", True) and now >= self._next_check:
            self._next_check = now + max(1.0, float(self._values.get("check_every_s", 10.0)))
            files = self._file_times()
            if self._files is None:
                self._log.record("start")
            elif files != self._files:
                self._log.record("change")      # writes only when a value really differs
            self._files = files
            self._log.maybe_daily()
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
