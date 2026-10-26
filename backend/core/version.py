from __future__ import annotations

import os
from pathlib import Path


def detect_version(app_root: Path) -> str:
    """
    Version shown in the GUI: FURNACE_BRAIN_VERSION env if set, otherwise the
    VERSION file in the repo root (bumped with every change, tagged v<version>
    when deployed to prod), otherwise "dev".
    """
    env_version = os.getenv("FURNACE_BRAIN_VERSION")
    if env_version:
        return env_version

    try:
        version = (app_root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    return version or "dev"
