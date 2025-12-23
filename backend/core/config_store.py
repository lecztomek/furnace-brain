from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
import yaml


@dataclass
class ModuleInfo:
    id: str
    name: str | None = None
    description: str | None = None


class ConfigStore:
    def __init__(self, base_dir: Path, module_ids_in_order: List[str]):
        self.modules_root = base_dir
        self._module_ids_in_order = module_ids_in_order

    # ---------- Ścieżki pomocnicze ----------

    def _schema_path(self, module_id: str) -> Path:
        # modules/<module_id>/schema.yaml
        return self.modules_root / module_id / "schema.yaml"

    def _values_path(self, module_id: str) -> Path:
        # modules/<module_id>/values.yaml
        return self.modules_root / module_id / "values.yaml"

    # ---------- API publiczne ----------

    def list_modules(self) -> List[ModuleInfo]:
        modules: List[ModuleInfo] = []

        for mid in self._module_ids_in_order:
            schema_path = self._schema_path(mid)
            name = None
            description = None

            if schema_path.exists():
                with schema_path.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                name = data.get("name")
                description = data.get("description")

            modules.append(
                ModuleInfo(
                    id=mid,
                    name=name,
                    description=description,
                )
            )

        return modules
	
    def get_schema(self, module_id: str) -> Dict[str, Any]:
        path = self._schema_path(module_id)
        if not path.exists():
            raise KeyError(f"Unknown module '{module_id}' (schema not found: {path})")
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data

    def get_values(self, module_id: str) -> Dict[str, Any]:
        """
        Zwraca scalone values: jeśli czegoś brak w values,
        bierzemy default z schema.
        """
        schema = self.get_schema(module_id)
        fields = schema.get("fields", [])

        vpath = self._values_path(module_id)
        if vpath.exists():
            with vpath.open("r", encoding="utf-8") as f:
                raw_values = yaml.safe_load(f) or {}
        else:
            raw_values = {}

        result: Dict[str, Any] = {}
        for field in fields:
            key = field["key"]
            if key in raw_values:
                value = raw_values[key]
            else:
                value = field.get("default")

            if value is None:
                raise ValueError(f"Brak wartości dla pola '{key}' i brak domyślnej.")

            result[key] = self._validate_single_value(field, value)

        return result

    def set_values(self, module_id: str, new_values: Dict[str, Any]) -> Dict[str, Any]:
        """
        Waliduje new_values na podstawie schema i zapisuje do YAML.
        Zwraca zwalidowane wartości.
        """
        schema = self.get_schema(module_id)
        fields = schema.get("fields", [])
        field_map = {f["key"]: f for f in fields}

        validated: Dict[str, Any] = {}

        # Przechodzimy po polach ze schemy – ignorujemy dodatkowe klucze w new_values.
        for key, field in field_map.items():
            if key in new_values:
                raw_value = new_values[key]
            else:
                raw_value = field.get("default")

            if raw_value is None:
                raise ValueError(f"Brak wartości dla pola '{key}' i brak domyślnej.")

            validated[key] = self._validate_single_value(field, raw_value)

        # Zapis do pliku
        vpath = self._values_path(module_id)
        vpath.parent.mkdir(parents=True, exist_ok=True)
        with vpath.open("w", encoding="utf-8") as f:
            yaml.safe_dump(validated, f, allow_unicode=True)

        return validated

    # ---------- Walidacja pojedynczej wartości ----------

    def _validate_single_value(self, field: Dict[str, Any], value: Any) -> Any:
        ftype = field.get("type")

        if ftype == "number":
            try:
                num = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Pole '{field['key']}' oczekuje liczby, dostało: {value!r}"
                )

            min_v = field.get("min")
            max_v = field.get("max")

            if min_v is not None and num < min_v:
                raise ValueError(
                    f"Wartość {num} dla '{field['key']}' jest mniejsza niż min={min_v}"
                )
            if max_v is not None and num > max_v:
                raise ValueError(
                    f"Wartość {num} dla '{field['key']}' jest większa niż max={max_v}"
                )

            return num

        elif ftype == "text":
            # tekst wolny albo wybierany z listy (jeśli options/choices podane)
            options = field.get("options") or field.get("choices")
        
            if value is None:
                raise ValueError(
                    f"Pole '{field['key']}' oczekuje tekstu, dostało: {value!r}"
                )

            s = str(value)

            # jeśli są opcje -> walidujemy jak enum
            if options is not None:
                if not isinstance(options, list):
                    raise ValueError(
                        f"Pole '{field['key']}': options/choices muszą być listą, dostało: {type(options).__name__}"
                    )
                if s not in options:
                    raise ValueError(
                        f"Pole '{field['key']}' może przyjmować tylko: {options}, dostało: {value!r}"
                    )

            return s

        elif ftype == "bool":
            # prosta konwersja do bool:
            # - już bool → zostaje
            # - int/float → bool(value)
            # - string → parsujemy kilka typowych wartości
            if isinstance(value, bool):
                return value

            if isinstance(value, (int, float)):
                return bool(value)

            if isinstance(value, str):
                v = value.strip().lower()
                if v in ("1", "true", "yes", "on"):
                    return True
                if v in ("0", "false", "no", "off"):
                    return False

            raise ValueError(
                f"Pole '{field['key']}' oczekuje wartości typu bool, dostało: {value!r}"
            )

        else:
            raise ValueError(
                f"Nieobsługiwany typ pola '{ftype}' dla '{field['key']}'"
            )

