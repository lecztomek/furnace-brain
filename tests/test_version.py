import subprocess

from backend.core.version import detect_version


def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("FURNACE_BRAIN_VERSION", "1.2.3")
    assert detect_version(tmp_path) == "1.2.3"


def test_git_tag(tmp_path, monkeypatch):
    monkeypatch.delenv("FURNACE_BRAIN_VERSION", raising=False)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run(git + ["tag", "prod-20261026-120000"], check=True)
    assert detect_version(tmp_path) == "prod-20261026-120000"


def test_render_commit_outside_git(tmp_path, monkeypatch):
    monkeypatch.delenv("FURNACE_BRAIN_VERSION", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef1234567890")
    assert detect_version(tmp_path) == "abcdef1"


def test_fallback_dev(tmp_path, monkeypatch):
    monkeypatch.delenv("FURNACE_BRAIN_VERSION", raising=False)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    assert detect_version(tmp_path) == "dev"
