import yaml

from backend.core.config_store import ConfigStore


SCHEMA = {
    "id": "demo",
    "fields": [
        {"key": "target_temp", "type": "number", "default": 40},
        {"key": "enabled", "type": "bool", "default": True},
        {"key": "new_param", "type": "number", "default": 7.5},
    ],
}


def make_store(tmp_path, values_text=None):
    mdir = tmp_path / "demo"
    mdir.mkdir()
    (mdir / "schema.yaml").write_text(yaml.safe_dump(SCHEMA), encoding="utf-8")
    if values_text is not None:
        (mdir / "values.yaml").write_text(values_text, encoding="utf-8")
    return ConfigStore(tmp_path, module_ids_in_order=["demo"]), mdir / "values.yaml"


def test_appends_only_missing_keys_and_keeps_user_lines(tmp_path):
    user_text = "# moje ustawienia\ntarget_temp: 55.0\nenabled: false"
    store, vpath = make_store(tmp_path, user_text)

    assert store.fill_missing_defaults() == {"demo": ["new_param"]}

    text = vpath.read_text(encoding="utf-8")
    assert text.startswith(user_text + "\n")
    assert yaml.safe_load(text) == {"target_temp": 55.0, "enabled": False, "new_param": 7.5}


def test_second_run_changes_nothing(tmp_path):
    store, vpath = make_store(tmp_path, "target_temp: 55.0\n")
    store.fill_missing_defaults()
    before = vpath.read_text(encoding="utf-8")

    assert store.fill_missing_defaults() == {}
    assert vpath.read_text(encoding="utf-8") == before


def test_creates_values_file_when_missing(tmp_path):
    store, vpath = make_store(tmp_path)

    store.fill_missing_defaults()

    assert yaml.safe_load(vpath.read_text(encoding="utf-8")) == {
        "target_temp": 40.0, "enabled": True, "new_param": 7.5,
    }
