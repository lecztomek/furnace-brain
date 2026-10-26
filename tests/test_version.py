import re
from pathlib import Path

from backend.core.version import detect_version


def test_env_override_wins(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("1.0.0\n")
    monkeypatch.setenv("FURNACE_BRAIN_VERSION", "9.9.9")
    assert detect_version(tmp_path) == "9.9.9"


def test_reads_version_file(tmp_path, monkeypatch):
    monkeypatch.delenv("FURNACE_BRAIN_VERSION", raising=False)
    (tmp_path / "VERSION").write_text("1.2.3\n")
    assert detect_version(tmp_path) == "1.2.3"


def test_fallback_dev(tmp_path, monkeypatch):
    monkeypatch.delenv("FURNACE_BRAIN_VERSION", raising=False)
    assert detect_version(tmp_path) == "dev"


def test_repo_version_is_semver():
    version = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
