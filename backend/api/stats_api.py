from __future__ import annotations

from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import csv
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from backend.core.state import SystemState
from backend.core.state_store import StateStore


def create_stats_router(
    store: StateStore,
    base_dir: Path,
    log_dir: str = "data",
    file_prefix_5m: str = "stats5m",
    daily_file: str = "stats_daily.csv",
    module_id: str = "stats",
    timezone: str = "Europe/Warsaw",
) -> APIRouter:
    """
    Backward compatible API:

    - /stats/data   -> LIVE runtime (tak jak stare GUI oczekuje)
    - /stats/series -> SERIA 5m z CSV w zakresie czasu (history-like)
    - /stats/daily  -> SERIA dzienna z stats_daily.csv
    - /stats/fields -> kolumny dla CSV 5m
    - /stats/daily/fields -> kolumny dla daily
    """
    router = APIRouter(prefix="/stats", tags=["stats"])
    stats_path = (base_dir / log_dir).resolve()
    daily_path = stats_path / daily_file
    tz = ZoneInfo(timezone)

    # ---------------- helpers ----------------

    def _ensure_dir() -> None:
        if not stats_path.exists():
            raise HTTPException(status_code=404, detail=f"Katalog stats nie istnieje: {stats_path}")

    def _coerce_dt(d: datetime) -> datetime:
        # jeśli przyjdzie naive -> traktuj jako lokalny czas routera
        if d.tzinfo is None:
            return d.replace(tzinfo=tz)
        return d

    def _get_stats_dict(state: SystemState) -> Dict[str, Any]:
        runtime = getattr(state, "runtime", None)
        if not isinstance(runtime, dict):
            raise HTTPException(
                status_code=500,
                detail="SystemState.runtime nie jest dostępne. Dodaj pole runtime do SystemState.",
            )

        data = runtime.get(module_id)
        if not isinstance(data, dict):
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Brak danych runtime dla modułu '{module_id}'. "
                    "Moduł może być wyłączony lub jeszcze nie policzył statystyk."
                ),
            )
        return dict(data)

    def _to_float(v: Any) -> Optional[float]:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace(",", ".")
        if s == "":
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _strip_power_energy(obj: Any) -> Any:
        """
        Usuwa z payloadu wszystko związane z power_kw* oraz energy_kwh* (rekurencyjnie).
        Zostawiamy tylko rzeczy potrzebne pod coal i burn (oraz resztę nie-power/energy).
        """
        if isinstance(obj, dict):
            out: Dict[str, Any] = {}
            for k, v in obj.items():
                if k.startswith("power_kw") or k.startswith("energy_kwh"):
                    continue
                out[k] = _strip_power_energy(v)
            return out
        if isinstance(obj, list):
            return [_strip_power_energy(x) for x in obj]
        return obj

    def _last_5m_bars(count: int, now_dt: datetime) -> List[Dict[str, Any]]:
        """
        Zwraca ostatnie `count` bucketów 5m na podstawie CSV stats5m_*.csv.

        Celowo czytamy tylko wartości związane z coal i burn (oraz pomocnicze seconds/active),
        a pola power/energy pomijamy całkowicie.
        """
        _ensure_dir()

        paths = sorted(stats_path.glob(f"{file_prefix_5m}_*.csv"))
        if not paths:
            return []

        rows: List[Dict[str, Any]] = []

        # czytamy od końca (zwykle wystarczy ostatni plik)
        for path in reversed(paths):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    for row in reader:
                        ts_str = (row.get("ts_end_iso") or "").strip()
                        if not ts_str:
                            continue
                        try:
                            ts_end = datetime.fromisoformat(ts_str)
                        except ValueError:
                            continue
                        if ts_end.tzinfo is None:
                            ts_end = ts_end.replace(tzinfo=tz)

                        # tylko dane do "teraz"
                        if ts_end > now_dt:
                            continue

                        rows.append(row)
            except OSError:
                continue

            # zapas, żeby spokojnie wyciąć ostatnie N po sortowaniu
            if len(rows) >= count * 3:
                break

        if not rows:
            return []

        def _row_ts_end_unix(r: Dict[str, Any]) -> float:
            ts_str = (r.get("ts_end_iso") or "").strip()
            try:
                dt = datetime.fromisoformat(ts_str)
            except ValueError:
                return 0.0
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.timestamp()

        rows.sort(key=_row_ts_end_unix)
        rows = rows[-count:]

        bars: List[Dict[str, Any]] = []
        for r in rows:
            ts_end_iso = (r.get("ts_end_iso") or "").strip()
            try:
                ts_end = datetime.fromisoformat(ts_end_iso)
            except ValueError:
                continue
            if ts_end.tzinfo is None:
                ts_end = ts_end.replace(tzinfo=tz)

            ts_start = ts_end - timedelta(minutes=5)

            seconds_sum = _to_float(r.get("seconds_sum")) or _to_float(r.get("seconds")) or 300.0

            # only coal + burn (różne warianty nazw w CSV)
            coal = _to_float(r.get("coal_kg_sum"))
            if coal is None:
                coal = _to_float(r.get("coal_kg"))
            if coal is None:
                coal = 0.0

            burn = _to_float(r.get("burn_kgph_avg"))
            if burn is None:
                burn = _to_float(r.get("burn_kgph"))
            if burn is None:
                burn = 0.0

            active_ratio = _to_float(r.get("active_ratio"))
            if active_ratio is None:
                active_ratio = 1.0 if (seconds_sum or 0.0) > 0 else 0.0

            bar: Dict[str, Any] = {
                "ts_start_unix": ts_start.timestamp(),
                "ts_end_unix": ts_end.timestamp(),
                "ts_start_iso": ts_start.isoformat(),
                "ts_end_iso": ts_end.isoformat(),
                "seconds_sum": float(seconds_sum),
                "coal_kg_sum": float(coal),
                "burn_kgph_avg": float(burn),
                "active_ratio": float(active_ratio),
                # jeśli w CSV są max/min coal/burn – przepuść; power/energy nie bierzemy w ogóle
                "burn_kgph_max_5m": _to_float(r.get("burn_kgph_max_5m")),
                "burn_kgph_min_active_5m": _to_float(r.get("burn_kgph_min_active_5m")),
                "coal_kg_max_5m": _to_float(r.get("coal_kg_max_5m")) or float(coal),
            }
            bars.append(bar)

        # label jak było: -100m ... -5m (od najstarszego do najnowszego)
                
        for b in bars:
            try:
                dt_end = datetime.fromisoformat(b["ts_end_iso"])
                if dt_end.tzinfo is None:
                    dt_end = dt_end.replace(tzinfo=tz)
                else:
                    dt_end = dt_end.astimezone(tz)
                b["label"] = dt_end.strftime("%H:%M")
            except Exception:
                b["label"] = ""


        return bars

    # ---------------- LIVE (GUI endpoint) ----------------

    @router.get("/data")
    def get_stats_data(
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista kluczy statystyk do zwrócenia, np. "
                "fields=burn_kgph_5m&fields=calendar&fields=compare_bars. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca aktualne statystyki z system_state.runtime['stats'].
        (Backward compatible z GUI, które woła /stats/data bez from/to.)
        """
        try:
            state = store.snapshot()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Nie udało się pobrać SystemState.") from exc

        stats = _get_stats_dict(state)

        ts_unix = float(getattr(state, "ts", 0.0) or 0.0)
        ts_iso = datetime.fromtimestamp(ts_unix).isoformat() if ts_unix > 0 else None

        if fields:
            payload: Dict[str, Any] = {"ts_unix": ts_unix, "ts_iso": ts_iso}
            for key in fields:
                if key in stats:
                    payload[key] = stats[key]
        else:
            payload = {"ts_unix": ts_unix, "ts_iso": ts_iso, **stats}

        # --- 5m: zwiększamy liczbę słupków do 20, reszta przedziałów bez zmian ---
        now_dt = datetime.fromtimestamp(ts_unix, tz=tz) if ts_unix > 0 else datetime.now(tz)

        existing_compare = payload.get("compare_bars")
        if not isinstance(existing_compare, dict):
            existing_compare = {}

        existing_compare["minutes_5m"] = _last_5m_bars(20, now_dt)
        payload["compare_bars"] = existing_compare

        # --- usuń wszystko power/energy z całego payloadu (łącznie z calendar / compare_bars / top-level) ---
        payload = _strip_power_energy(payload)

        return {"module_id": module_id, "count": len(payload), "data": payload}

    # ---------------- SERIA 5m z CSV (history-like) ----------------

    @router.get("/series")
    def get_stats_series(
        from_ts: datetime = Query(
            ...,
            description="Początek zakresu czasu (ISO 8601). Najlepiej z offsetem, np. 2025-01-01T00:00:00+01:00",
        ),
        to_ts: datetime = Query(
            ...,
            description="Koniec zakresu czasu (ISO 8601). Najlepiej z offsetem.",
        ),
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista nazw pól do zwrócenia z CSV 5m, np. "
                "fields=coal_kg&fields=burn_kgph. "
                "Zawsze zwracane jest ts_end_iso. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca buckety 5m z plików CSV w zadanym zakresie czasu.
        """
        _ensure_dir()

        from_dt = _coerce_dt(from_ts)
        to_dt = _coerce_dt(to_ts)

        if from_dt >= to_dt:
            raise HTTPException(status_code=400, detail="Parametr from_ts musi być wcześniejszy niż to_ts.")

        items: List[Dict[str, Any]] = []

        for path in sorted(stats_path.glob(f"{file_prefix_5m}_*.csv")):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    for row in reader:
                        ts_str = (row.get("ts_end_iso") or "").strip()
                        if not ts_str:
                            continue
                        try:
                            ts = datetime.fromisoformat(ts_str)
                        except ValueError:
                            continue

                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=tz)

                        if ts < from_dt or ts > to_dt:
                            continue

                        if fields:
                            filtered: Dict[str, Any] = {"ts_end_iso": ts_str}
                            for key in fields:
                                if key.startswith("power_kw") or key.startswith("energy_kwh"):
                                    continue
                                if key in row:
                                    filtered[key] = row[key]
                            items.append(filtered)
                        else:
                            # bez fields: zwracamy wszystko poza power/energy
                            filtered_all: Dict[str, Any] = {}
                            for k, v in row.items():
                                if k.startswith("power_kw") or k.startswith("energy_kwh"):
                                    continue
                                filtered_all[k] = v
                            items.append(filtered_all)
            except OSError:
                continue

        return {
            "from_ts": from_dt.isoformat(),
            "to_ts": to_dt.isoformat(),
            "count": len(items),
            "items": items,
        }

    @router.get("/fields")
    def get_stats_fields():
        """
        Zwraca listę kolumn dla CSV 5m na podstawie pierwszego znalezionego pliku.
        """
        _ensure_dir()

        for path in sorted(stats_path.glob(f"{file_prefix_5m}_*.csv")):
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.reader(f, delimiter=";")
                    header = next(reader, None)
                    if header:
                        header = [h for h in header if not (h.startswith("power_kw") or h.startswith("energy_kwh"))]
                        return {"fields": header}
            except OSError:
                continue

        return {"fields": []}

    # ---------------- SERIA DZIENNA (stats_daily.csv) ----------------

    @router.get("/daily")
    def get_stats_daily(
        from_date: date = Query(..., description="Początek zakresu dat (YYYY-MM-DD)"),
        to_date: date = Query(..., description="Koniec zakresu dat (YYYY-MM-DD), włącznie"),
        fields: Optional[List[str]] = Query(
            None,
            description=(
                "Lista nazw pól do zwrócenia z CSV dziennego, np. "
                "fields=coal_kg_sum&fields=burn_kgph_avg. "
                "Zawsze zwracane jest date. "
                "Jeśli brak – zwracane są wszystkie pola."
            ),
        ),
    ):
        """
        Zwraca agregaty dzienne z stats_daily.csv w zadanym zakresie dat.
        """
        _ensure_dir()

        if from_date > to_date:
            raise HTTPException(status_code=400, detail="Parametr from_date musi być <= to_date.")

        if not daily_path.exists():
            raise HTTPException(status_code=404, detail=f"Plik cache dziennego nie istnieje: {daily_path}")

        items: List[Dict[str, Any]] = []

        try:
            with daily_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    d_str = (row.get("date") or "").strip()
                    if not d_str:
                        continue
                    try:
                        d = date.fromisoformat(d_str)
                    except ValueError:
                        continue

                    if d < from_date or d > to_date:
                        continue

                    if fields:
                        filtered: Dict[str, Any] = {"date": d_str}
                        for key in fields:
                            if key.startswith("power_kw") or key.startswith("energy_kwh"):
                                continue
                            if key in row:
                                filtered[key] = row[key]
                        items.append(filtered)
                    else:
                        filtered_all: Dict[str, Any] = {}
                        for k, v in row.items():
                            if k.startswith("power_kw") or k.startswith("energy_kwh"):
                                continue
                            filtered_all[k] = v
                        items.append(filtered_all)
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"Nie udało się odczytać {daily_path}") from exc

        return {
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "count": len(items),
            "items": items,
        }

    @router.get("/daily/fields")
    def get_stats_daily_fields():
        """
        Zwraca listę kolumn dla stats_daily.csv.
        """
        _ensure_dir()

        if not daily_path.exists():
            return {"fields": []}

        try:
            with daily_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f, delimiter=";")
                header = next(reader, None)
                if not header:
                    return {"fields": []}
                header = [h for h in header if not (h.startswith("power_kw") or h.startswith("energy_kwh"))]
                return {"fields": header}
        except OSError:
            return {"fields": []}

    return router

