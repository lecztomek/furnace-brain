"""
Settings history: every setting of every module with the app version, on the USB stick
(modules/settings_log/data/settings_YYYYMM.jsonl, one JSON line per entry).
An entry at every start (each update), after every change from the GUI and once a day,
so any past day can be matched with how the furnace was set up and which version ran.
A few lines a day; keys that look like secrets are left out.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

SECRET_WORDS = ("secret", "token", "password", "passwd", "apikey", "api_key")


def _clean(snapshot: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {m: {k: v for k, v in (vals or {}).items() if not any(w in k.lower() for w in SECRET_WORDS)}
            for m, vals in snapshot.items()}


def git_commit(app_root: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(app_root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def diff(old: Dict[str, Dict[str, Any]], new: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, List[Any]]]:
    """{module: {key: [old, new]}} of what differs."""
    out: Dict[str, Dict[str, List[Any]]] = {}
    for m in sorted(set(old) | set(new)):
        a, b = old.get(m) or {}, new.get(m) or {}
        ch = {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
        if ch:
            out[m] = ch
    return out


class SettingsLog:
    def __init__(self, data_dir: Path, snapshot: Callable[[], Dict[str, Dict[str, Any]]],
                 version: str, commit: Optional[str] = None,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self.data_dir = data_dir
        self._snapshot = snapshot
        self.version = version
        self.commit = commit
        self._now = now
        self._lock = threading.Lock()
        self._last: Optional[Dict[str, Dict[str, Any]]] = None
        self._last_day: Optional[str] = None
        last = self._read_last()
        if last is not None:
            self._last = last.get("settings")

    def _path(self, when: datetime) -> Path:
        return self.data_dir / f"settings_{when:%Y%m}.jsonl"

    def _read_last(self) -> Optional[Dict[str, Any]]:
        try:
            files = sorted(self.data_dir.glob("settings_*.jsonl"))
        except OSError:
            return None
        for path in reversed(files):
            try:
                lines = path.read_text(encoding="utf-8").strip().splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    return json.loads(line)
                except ValueError:
                    continue      # a line cut by a power loss
        return None

    def record(self, reason: str) -> Optional[Dict[str, Any]]:
        """reason: start / change / daily. Never raises - the settings log must not stop anything."""
        try:
            with self._lock:
                when = self._now()
                settings = _clean(self._snapshot())
                changed = diff(self._last, settings) if self._last is not None else {}
                if reason == "change" and not changed:
                    return None
                entry = {"ts": when.isoformat(timespec="seconds"), "reason": reason, "version": self.version,
                         "commit": self.commit, "changed": changed, "settings": settings}
                self.data_dir.mkdir(parents=True, exist_ok=True)
                with self._path(when).open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                self._last = settings
                self._last_day = when.date().isoformat()
                return entry
        except Exception as exc:  # pylint: disable=broad-except
            log.error("Settings log: %s", exc)
            return None

    def maybe_daily(self) -> None:
        """Once a day (the first call after midnight) - cheap to call every aux step."""
        if self._last_day != self._now().date().isoformat():
            self.record("daily")


def read_entries(data_dir: Path, since: datetime) -> List[Dict[str, Any]]:
    """Entries from `since` on, oldest first (only the month files that can hold them)."""
    out: List[Dict[str, Any]] = []
    first = f"settings_{since:%Y%m}.jsonl"
    try:
        files = sorted(p for p in data_dir.glob("settings_*.jsonl") if p.name >= first)
    except OSError:
        return out
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
                if datetime.fromisoformat(e["ts"]) >= since:
                    out.append(e)
            except (ValueError, KeyError, TypeError):
                continue
    return out
