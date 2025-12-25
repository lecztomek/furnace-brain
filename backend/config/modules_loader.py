# backend/config/modules_loader.py
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import List, Tuple, Optional
import inspect
import logging

import yaml  # pip install pyyaml

from ..core.kernel import ModuleInterface


logger = logging.getLogger(__name__)
CONFIG_PATH = Path(__file__).with_name("modules.yaml")


@dataclass
class ModuleDescriptor:
    id: str
    path: str
    enabled: bool = True
    critical: bool = True


def load_module_descriptors() -> List[ModuleDescriptor]:
    """
    Publiczny helper: czyta modules.yaml i zwraca listę descriptorów
    w KOLEJNOŚCI z pliku.
    """
    return _load_yaml_config(CONFIG_PATH)


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


def _ctor_accepts_data_root(cls) -> bool:
    """
    True jeśli:
    - __init__ ma parametr 'data_root', albo
    - __init__ ma **kwargs (VAR_KEYWORD) -> wtedy bezpiecznie przekażemy data_root
    """
    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return False

    params = sig.parameters
    if "data_root" in params:
        return True

    for p in params.values():
        if p.kind == inspect.Parameter.VAR_KEYWORD:  # **kwargs
            return True

    return False


def load_modules_split(*, data_root: Optional[Path] = None) -> Tuple[List[ModuleInterface], List[ModuleInterface]]:
    """
    Czyta modules.yaml i tworzy DWIE listy instancji:
    - critical_modules: critical=true
    - aux_modules: critical=false

    Zasada (bez flag):
    - jeśli moduł umie przyjąć data_root (param lub **kwargs) i data_root podano -> dostaje data_root
    - w przeciwnym razie tworzymy jak dawniej (bez argumentów)

    Dodatkowo:
    - jeśli data_root podano, a moduł go nie przyjmuje -> logujemy WARNING (lista do migracji)
    """
    descriptors = _load_yaml_config(CONFIG_PATH)

    critical: List[ModuleInterface] = []
    aux: List[ModuleInterface] = []

    legacy_no_data_root: List[str] = []

    for desc in descriptors:
        if not desc.enabled:
            continue

        cls = _load_module_class(desc.path)

        accepts_data_root = _ctor_accepts_data_root(cls)

        if data_root is not None and accepts_data_root:
            module_instance: ModuleInterface = cls(data_root=data_root)
        else:
            module_instance = cls()
            if data_root is not None and not accepts_data_root:
                legacy_no_data_root.append(desc.id)

        # lekka walidacja spójności
        if getattr(module_instance, "id", None) != desc.id:
            raise ValueError(
                f"Module id mismatch: config id={desc.id}, class id={getattr(module_instance, 'id', None)}"
            )

        if desc.critical:
            critical.append(module_instance)
        else:
            aux.append(module_instance)

    if data_root is not None and legacy_no_data_root:
        logger.warning(
            "Some modules do not accept data_root and will use legacy paths (likely relative to module code): %s",
            ", ".join(sorted(legacy_no_data_root)),
        )

    return critical, aux

