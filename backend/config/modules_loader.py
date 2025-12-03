# backend/config/modules_loader.py
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import List, Tuple

import yaml  # pip install pyyaml

from ..core.kernel import ModuleInterface


CONFIG_PATH = Path(__file__).with_name("modules.yaml")


@dataclass
class ModuleDescriptor:
    id: str
    path: str
    enabled: bool = True
    critical: bool = True


def _load_yaml_config(path: Path) -> List[ModuleDescriptor]:
    if not path.exists():
        raise FileNotFoundError(f"Modules config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    modules_raw = data.get("modules", [])
    descriptors: List[ModuleDescriptor] = []

    for item in modules_raw:
        descriptors.append(
            ModuleDescriptor(
                id=item["id"],
                path=item["path"],
                enabled=item.get("enabled", True),
                critical=item.get("critical", True),
            )
        )

    return descriptors


def _load_module_class(path: str):
    """
    Ładuje klasę modułu na podstawie ścieżki:
    "backend.modules.blower:BlowerModule"
    """
    module_path, class_name = path.split(":")
    module = import_module(module_path)
    cls = getattr(module, class_name)
    return cls


def load_modules_split() -> Tuple[List[ModuleInterface], List[ModuleInterface]]:
    """
    Czyta modules.yaml i tworzy DWIE listy instancji:
    - critical_modules: critical=true
    - aux_modules: critical=false
    """
    descriptors = _load_yaml_config(CONFIG_PATH)

    critical: List[ModuleInterface] = []
    aux: List[ModuleInterface] = []

    for desc in descriptors:
        if not desc.enabled:
            continue

        cls = _load_module_class(desc.path)
        module_instance: ModuleInterface = cls()  # zakładamy pusty konstruktor

        # lekka walidacja spójności
        if getattr(module_instance, "id", None) != desc.id:
            raise ValueError(
                f"Module id mismatch: config id={desc.id}, class id={getattr(module_instance, 'id', None)}"
            )

        if desc.critical:
            critical.append(module_instance)
        else:
            aux.append(module_instance)

    return critical, aux
