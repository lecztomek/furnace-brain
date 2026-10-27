"""CO pump: settings changed in the GUI apply at once; mode auto / force_on / off."""
import yaml

from backend.core.state import Sensors, SystemState
from backend.modules.pump_co import CoPumpModule


def _tick(m, temp):
    return m.tick(0.0, Sensors(boiler_temp=temp), SystemState()).partial_outputs.pump_co_on


def test_new_threshold_applies_after_reload(tmp_path):
    (tmp_path / "values.yaml").write_text("boiler_on_temp: 35.0\nhysteresis: 3.0\n")
    m = CoPumpModule(base_path=tmp_path)
    assert _tick(m, 36.0) is True          # old threshold 35

    # GUI writes values.yaml, the kernel calls reload_config_from_file()
    (tmp_path / "values.yaml").write_text(yaml.safe_dump({"boiler_on_temp": 40.0, "hysteresis": 1.0, "mode": "auto"}))
    m.reload_config_from_file()
    assert _tick(m, 36.0) is False         # 36 <= 40 - 1: off
    assert _tick(m, 39.5) is False
    assert _tick(m, 40.0) is True
    assert _tick(m, 39.5) is True          # hysteresis
    assert _tick(m, 39.0) is False


def test_modes(tmp_path):
    (tmp_path / "values.yaml").write_text("boiler_on_temp: 40.0\nhysteresis: 1.0\nmode: force_on\n")
    m = CoPumpModule(base_path=tmp_path)
    assert _tick(m, 20.0) is True
    (tmp_path / "values.yaml").write_text("boiler_on_temp: 40.0\nhysteresis: 1.0\nmode: off\n")
    m.reload_config_from_file()
    assert _tick(m, 70.0) is False


def test_every_module_with_settings_reloads():
    """Modules whose settings can be changed in the GUI must re-read them (else: only after a restart)."""
    import importlib
    from backend.config.modules_loader import load_module_descriptors
    from backend.core.module_interface import ModuleInterface
    missing = []
    for d in load_module_descriptors():
        mod_path, cls_name = d.path.split(":")
        cls = getattr(importlib.import_module(mod_path), cls_name)
        if cls.reload_config_from_file is ModuleInterface.reload_config_from_file:
            missing.append(d.id)
    assert missing in ([], ["manual"]), missing   # manual has no settings
