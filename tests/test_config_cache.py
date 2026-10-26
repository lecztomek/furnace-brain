"""Settings are parsed once and re-read only after a change (Raspberry Pi 3 is slow at YAML)."""
import time

import yaml

from backend.core import config_store as cs
from backend.core.analytics import files_by_day


def test_read_yaml_cached_and_refreshed(tmp_path, monkeypatch):
    p = tmp_path / "values.yaml"
    p.write_text("a: 1\n")
    calls = []
    real_load = yaml.load
    monkeypatch.setattr(cs.yaml, "load", lambda *a, **k: calls.append(1) or real_load(*a, **k))

    assert cs.read_yaml(p) == {"a": 1}
    copy = cs.read_yaml(p)
    copy["a"] = 99  # callers get a copy
    assert cs.read_yaml(p) == {"a": 1}
    assert len(calls) == 1

    time.sleep(0.01)
    p.write_text("a: 22\n")
    assert cs.read_yaml(p) == {"a": 22}
    assert len(calls) == 2


def test_config_store_write_is_seen(tmp_path):
    mod = tmp_path / "m"
    mod.mkdir()
    (mod / "schema.yaml").write_text("fields:\n  - {key: t, type: number, default: 5}\n")
    store = cs.ConfigStore(tmp_path, ["m"])
    assert store.get_value("m", "t") == 5
    store.set_value("m", "t", 7)
    assert store.get_value("m", "t") == 7


def test_files_by_day(tmp_path):
    for name in ["boiler_20261025_10.csv", "boiler_20261025_11.csv", "boiler_20261026_00.csv", "other.txt"]:
        (tmp_path / name).write_text("")
    ix = files_by_day(tmp_path, "boiler")
    assert [p.name for p in ix["20261025"]] == ["boiler_20261025_10.csv", "boiler_20261025_11.csv"]
    assert list(ix) == ["20261025", "20261026"]
