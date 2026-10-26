"""
The boiler mode survives a restart (update, power cut): it is saved to a file
on every change and restored at start-up.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from backend.core.state import BoilerMode

logger = logging.getLogger(__name__)

# what a saved mode becomes after a restart
RESTORE = {
    BoilerMode.OFF: BoilerMode.OFF,
    BoilerMode.WORK: BoilerMode.WORK,
    BoilerMode.IGNITION: BoilerMode.IGNITION,
    # manual outputs (e.g. the feeder switched on by hand) must not come back
    # by themselves after a restart
    BoilerMode.MANUAL: BoilerMode.OFF,
}


class ModeFile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._saved: Optional[BoilerMode] = None

    def load(self) -> Optional[BoilerMode]:
        """Mode to start in, or None (no file / unreadable -> default mode)."""
        try:
            name = json.loads(self.path.read_text(encoding="utf-8")).get("mode")
            saved = BoilerMode[name]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("Mode file %s unreadable (%s) - default mode", self.path, exc)
            return None
        self._saved = saved
        return RESTORE.get(saved)

    def save_if_changed(self, mode: BoilerMode) -> None:
        """Called after every control step - writes only when the mode changed."""
        if mode == self._saved:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump({"mode": mode.name}, f)
                f.flush()
                os.fsync(f.fileno())  # survives a power cut right after the change
            tmp.replace(self.path)
            self._saved = mode
        except OSError as exc:
            # never break the control loop because of the file; retried next step
            logger.warning("Saving mode to %s failed: %s", self.path, exc)
