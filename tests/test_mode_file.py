"""The boiler mode is saved on every change and restored after a restart."""
import json

import pytest

from backend.core.mode_file import ModeFile
from backend.core.state import BoilerMode


@pytest.mark.parametrize("saved, restored", [
    (BoilerMode.OFF, BoilerMode.OFF),
    (BoilerMode.WORK, BoilerMode.WORK),
    (BoilerMode.IGNITION, BoilerMode.IGNITION),
    (BoilerMode.MANUAL, BoilerMode.OFF),  # manual outputs never come back by themselves
])
def test_restore_after_restart(tmp_path, saved, restored):
    path = tmp_path / "data" / "mode_state.json"
    ModeFile(path).save_if_changed(saved)
    assert ModeFile(path).load() == restored


def test_no_file_or_damaged_file_means_default(tmp_path):
    path = tmp_path / "mode_state.json"
    assert ModeFile(path).load() is None
    path.write_text("\x00\x00")  # power cut while writing
    assert ModeFile(path).load() is None
    path.write_text('{"mode": "NOPE"}')
    assert ModeFile(path).load() is None


def test_writes_only_on_change(tmp_path, monkeypatch):
    path = tmp_path / "mode_state.json"
    mf = ModeFile(path)
    writes = []
    real_replace = type(path).replace
    monkeypatch.setattr(type(path), "replace", lambda self, target: writes.append(1) or real_replace(self, target))
    for mode in [BoilerMode.WORK] * 5 + [BoilerMode.OFF] * 5 + [BoilerMode.WORK]:
        mf.save_if_changed(mode)
    assert len(writes) == 3
    assert json.loads(path.read_text()) == {"mode": "WORK"}


def test_manual_saved_as_off_after_restart(tmp_path):
    path = tmp_path / "mode_state.json"
    ModeFile(path).save_if_changed(BoilerMode.MANUAL)
    mf = ModeFile(path)
    mode = mf.load()
    mf.save_if_changed(mode)  # first control step after the restart
    assert json.loads(path.read_text()) == {"mode": "OFF"}


def test_unwritable_location_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    ModeFile(blocker / "mode_state.json").save_if_changed(BoilerMode.WORK)  # parent is a file
