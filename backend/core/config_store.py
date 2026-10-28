from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import copy
import datetime as dt
import logging
import yaml


logger = logging.getLogger(__name__)

# C parser when libyaml is available - the pure Python one is very slow on the Pi
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_yaml_cache: Dict[str, Tuple[int, int, Any]] = {}


def read_yaml(path: Path) -> Any:
    """
    Parsed YAML file, re-read only when the file changes (mtime/size).
    The state API asks for settings on every GUI poll - parsing ~20 files
    each time took seconds on a Raspberry Pi 3 and requests piled up.
    Returns a copy, so callers may modify it.
    """
    st = path.stat()
    key = str(path)
    hit = _yaml_cache.get(key)
    if hit is None or hit[0] != st.st_mtime_ns or hit[1] != st.st_size:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.load(f, Loader=_YAML_LOADER) or {}
        hit = (st.st_mtime_ns, st.st_size, data)
        _yaml_cache[key] = hit
    return copy.deepcopy(hit[2])


@dataclass
class ModuleInfo:
    id: str
    name: str | None = None
    description: str | None = None


class ConfigStore:
    def __init__(self, base_dir: Path, module_ids_in_order: List[str],
                 validators: Optional[Dict[str, Callable[[Dict[str, Any]], List[str]]]] = None):
        self.modules_root = base_dir
        self._module_ids_in_order = module_ids_in_order
        # module id -> check of the whole set of values (e.g. one GPIO for two outputs);
        # problems = the save is refused and the file stays as it was
        self._validators = validators or {}

    def _check_whole(self, module_id: str, values: Dict[str, Any]) -> None:
        check = self._validators.get(module_id)
        problems = check(values) if check else []
        if problems:
            raise ValueError("; ".join(problems))

    @property
    def module_ids(self) -> List[str]:
        return list(self._module_ids_in_order)

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
                data = read_yaml(schema_path)
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
        return read_yaml(path)

    def get_values(self, module_id: str) -> Dict[str, Any]:
        """
        Zwraca scalone values: jeśli czegoś brak w values,
        bierzemy default z schema.
        """
        schema = self.get_schema(module_id)
        fields = schema.get("fields", [])

        vpath = self._values_path(module_id)
        if vpath.exists():
            raw_values = read_yaml(vpath)
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
        current = self._load_raw_values(module_id)

        # Przechodzimy po polach ze schemy – ignorujemy dodatkowe klucze w new_values.
        # Fields missing from new_values keep their current value (not the default),
        # so saving a few fields never resets the rest.
        for key, field in field_map.items():
            if key in new_values:
                raw_value = new_values[key]
            elif key in current:
                raw_value = current[key]
            else:
                raw_value = field.get("default")

            if raw_value is None:
                raise ValueError(f"Brak wartości dla pola '{key}' i brak domyślnej.")

            validated[key] = self._validate_single_value(field, raw_value)

        self._check_whole(module_id, validated)

        # Zapis do pliku
        vpath = self._values_path(module_id)
        vpath.parent.mkdir(parents=True, exist_ok=True)
        with vpath.open("w", encoding="utf-8") as f:
            yaml.safe_dump(validated, f, allow_unicode=True)
        _yaml_cache.pop(str(vpath), None)

        return validated

    def get_value(self, module_id: str, key: str) -> Any:
        """Zwraca pojedynczą wartość (z defaultem jeśli brak w values.yaml)."""
        schema = self.get_schema(module_id)
        fields = schema.get("fields", [])
        field = next((f for f in fields if f.get("key") == key), None)
        if field is None:
            raise KeyError(f"Unknown key '{key}' in module '{module_id}'")

        raw_values = self._load_raw_values(module_id)

        if key in raw_values:
            value = raw_values[key]
        else:
            value = field.get("default")

        if value is None:
            raise ValueError(f"Brak wartości dla pola '{key}' i brak domyślnej.")

        return self._validate_single_value(field, value)

    def set_value(self, module_id: str, key: str, value: Any) -> Any:
        """
        Ustawia pojedynczy parametr w values.yaml.
        - Waliduje tylko ten klucz wg schemy.
        - Pozostałe klucze w values.yaml pozostają nietknięte.
        - Jeśli values.yaml nie istnieje, zostanie utworzony.
        Zwraca zwalidowaną wartość.
        """
        schema = self.get_schema(module_id)
        fields = schema.get("fields", [])
        field = next((f for f in fields if f.get("key") == key), None)
        if field is None:
            raise KeyError(f"Unknown key '{key}' in module '{module_id}'")

        validated_value = self._validate_single_value(field, value)

        raw_values = self._load_raw_values(module_id)
        raw_values[key] = validated_value
        self._check_whole(module_id, {**{f["key"]: f.get("default") for f in fields}, **raw_values})

        vpath = self._values_path(module_id)
        vpath.parent.mkdir(parents=True, exist_ok=True)
        with vpath.open("w", encoding="utf-8") as f:
            yaml.safe_dump(raw_values, f, allow_unicode=True)
        _yaml_cache.pop(str(vpath), None)

        return validated_value

    def fill_missing_defaults(self) -> Dict[str, List[str]]:
        """
        Appends schema fields that are missing from values.yaml (e.g. a new
        setting after an update). Existing lines are left untouched - new keys
        are added at the end of the file.
        Returns {module_id: [added keys]}.
        """
        added_all: Dict[str, List[str]] = {}

        for mid in self._module_ids_in_order:
            if not self._schema_path(mid).exists():
                continue
            fields = self.get_schema(mid).get("fields", [])
            raw_values = self._load_raw_values(mid)

            missing: Dict[str, Any] = {}
            for field in fields:
                key = field["key"]
                if key in raw_values or field.get("default") is None:
                    continue
                missing[key] = self._validate_single_value(field, field["default"])

            if not missing:
                continue

            vpath = self._values_path(mid)
            old_text = vpath.read_text(encoding="utf-8") if vpath.exists() else ""
            if old_text and not old_text.endswith("\n"):
                old_text += "\n"
            stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            header = f"\n# added automatically {stamp} (defaults from schema.yaml)\n"
            body = yaml.safe_dump(missing, allow_unicode=True, sort_keys=False)
            vpath.write_text(old_text + header + body, encoding="utf-8")
            _yaml_cache.pop(str(vpath), None)

            logger.info("values.yaml [%s]: added defaults %s", mid, missing)
            added_all[mid] = list(missing)

        return added_all

    def _load_raw_values(self, module_id: str) -> Dict[str, Any]:
        """Ładuje surowe values.yaml (bez defaultów)."""
        vpath = self._values_path(module_id)
        if vpath.exists():
            return read_yaml(vpath)
        return {}

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

