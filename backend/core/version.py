from __future__ import annotations

import os
import subprocess
from pathlib import Path


def detect_version(app_root: Path) -> str:
    """
    Version shown in the GUI:
    - FURNACE_BRAIN_VERSION env, if set,
    - git describe (on the Pi: the prod-YYYYMMDD-HHMMSS deploy tag),
    - the commit Render deployed (RENDER_GIT_COMMIT),
    - "dev" otherwise.
    """
    env_version = os.getenv("FURNACE_BRAIN_VERSION")
    if env_version:
        return env_version

    try:
        out = subprocess.run(
            ["git", "-C", str(app_root), "describe", "--tags", "--always"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        if out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    render_commit = os.getenv("RENDER_GIT_COMMIT")
    if render_commit:
        return render_commit[:7]

    return "dev"
