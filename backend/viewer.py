"""
Remote read-only view of the furnace ("podgląd").

A separate app (uvicorn backend.viewer:app) for a public server: the Pi's
remote_view module pushes its data here (state, history, logs, statistics,
settings) and this app serves the unchanged GUI on top of it. Nothing can be
changed: every write request is accepted and ignored (the reply carries the
furnace's real values), and there is no connection back to the Pi at all.

Environment:
  FURNACE_BRAIN_VIEWER_TOKEN     - required; the Pi sends it with every push
  FURNACE_BRAIN_VIEWER_PASSWORD  - optional; password for viewing (user "podglad")
  FURNACE_BRAIN_FRONTEND_DIR     - frontend directory (default: ./frontend)
"""
from __future__ import annotations

import base64
import gzip
import hmac
import io
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

KEEP_DAYS = 14
MAX_PUSH_BYTES = 64 * 1024 * 1024  # after decompression
STALE_AFTER_S = 120

TOKEN = os.getenv("FURNACE_BRAIN_VIEWER_TOKEN", "")
PASSWORD = os.getenv("FURNACE_BRAIN_VIEWER_PASSWORD", "")
FRONT_DIR = Path(os.getenv("FURNACE_BRAIN_FRONTEND_DIR", str(Path(__file__).resolve().parents[1] / "frontend")))


class Store:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: Optional[Dict[str, Any]] = None
        self.state_at = 0.0
        self.manual: Optional[Dict[str, Any]] = None
        self.history: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.logs_meta: Optional[Dict[str, Any]] = None
        self.report: Optional[Dict[str, Any]] = None
        self.config: Dict[str, Any] = {"modules": [], "schemas": {}, "values": {}}

    def push(self, body: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            if body.get("state") is not None:
                self.state, self.state_at = body["state"], now
            if body.get("manual") is not None:
                self.manual = body["manual"]
            if body.get("logs_meta") is not None:
                self.logs_meta = body["logs_meta"]
            if body.get("report") is not None:
                self.report = body["report"]
            if body.get("config") is not None:
                self.config = body["config"]

            rows = [r for r in body.get("history_rows") or [] if r.get("data_czas")]
            if rows:
                last = self.history[-1]["data_czas"] if self.history else ""
                self.history.extend(r for r in rows if r["data_czas"] > last)
                cutoff = datetime.fromtimestamp(now - KEEP_DAYS * 86400).isoformat()
                self.history = [r for r in self.history if r["data_czas"] >= cutoff]

            items = [i for i in body.get("log_items") or [] if i.get("ts") is not None]
            if items:
                last_ts = self.logs[-1]["ts"] if self.logs else 0.0
                self.logs.extend(sorted((i for i in items if i["ts"] > last_ts), key=lambda i: i["ts"]))
                self.logs = [i for i in self.logs if i["ts"] >= now - KEEP_DAYS * 86400]

            return {
                "ok": True,
                "history_last": self.history[-1]["data_czas"] if self.history else None,
                "logs_last": self.logs[-1]["ts"] if self.logs else None,
            }


store = Store()
app = FastAPI(title="Podgląd kotła (tylko odczyt)")


# ---------------------------------------------------------------- access

@app.middleware("http")
async def read_only_and_password(request: Request, call_next):
    path = request.url.path
    if path in ("/api/viewer/push", "/api/viewer/health"):
        return await call_next(request)
    if request.method not in ("GET", "HEAD"):
        return _ignored_write(path)
    if PASSWORD:
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.lower().startswith("basic "):
            try:
                _, _, pwd = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok = hmac.compare_digest(pwd, PASSWORD)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Podglad kotla"'})
    return await call_next(request)


def _ignored_write(path: str) -> JSONResponse:
    """Any change is ignored; answer with the furnace's real values, so the GUI shows them."""
    parts = path.strip("/").split("/")
    with store.lock:
        values = store.config.get("values", {})
        if parts[:3] == ["api", "config", "value"] and len(parts) == 5:
            return JSONResponse({"key": parts[4], "value": values.get(parts[3], {}).get(parts[4])})
        if parts[:3] == ["api", "config", "values"] and len(parts) == 4:
            return JSONResponse(values.get(parts[3], {}))
        if parts[:3] == ["api", "state", "mode"] and store.state is not None:
            return JSONResponse(store.state)
    return JSONResponse({"ok": True, "ignored": True})


@app.get("/api/viewer/health")
def health():
    with store.lock:
        age = time.time() - store.state_at if store.state is not None else None
    return {"ok": True, "data_age_s": None if age is None else round(age, 1)}


@app.post("/api/viewer/push")
async def push(request: Request):
    auth = request.headers.get("authorization", "")
    if not TOKEN or not hmac.compare_digest(auth, f"Bearer {TOKEN}"):
        raise HTTPException(status_code=401, detail="bad token")
    raw = await request.body()
    if request.headers.get("content-encoding", "").lower() == "gzip":
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(MAX_PUSH_BYTES + 1)
        except OSError:
            raise HTTPException(status_code=400, detail="bad gzip")
    if len(raw) > MAX_PUSH_BYTES:
        raise HTTPException(status_code=413, detail="too large")
    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad json")
    return store.push(body)


# ---------------------------------------------------------------- read API (same shapes as the Pi)

@app.get("/api/state/current")
def state_current():
    with store.lock:
        if store.state is None:
            raise HTTPException(status_code=503, detail="Brak danych z pieca")
        state = dict(store.state)
        age = time.time() - store.state_at
    state["read_only"] = True
    state["viewer_data_age_s"] = round(age, 1)
    return state


@app.get("/api/manual/current")
def manual_current():
    with store.lock:
        if store.manual is None:
            raise HTTPException(status_code=503, detail="Brak danych z pieca")
        return store.manual


@app.get("/api/history/fields")
def history_fields():
    with store.lock:
        fields: List[str] = []
        for r in store.history[-500:]:
            fields.extend(k for k in r if k not in fields)
    return {"fields": fields}


@app.get("/api/history/data")
def history_data(from_ts: datetime = Query(...), to_ts: datetime = Query(...),
                 fields: Optional[List[str]] = Query(None)):
    lo, hi = from_ts.isoformat(timespec="seconds"), to_ts.isoformat(timespec="seconds")
    with store.lock:
        rows = [r for r in store.history if lo <= r["data_czas"] <= hi]
    if fields:
        rows = [{"data_czas": r["data_czas"], **{k: r[k] for k in fields if k in r}} for r in rows]
    return {"from_ts": from_ts.isoformat(), "to_ts": to_ts.isoformat(), "count": len(rows), "items": rows}


@app.get("/api/logs/meta")
def logs_meta():
    with store.lock:
        return store.logs_meta or {"levels": [], "sources": []}


@app.get("/api/logs/recent")
def logs_recent(limit: int = Query(100, ge=1, le=2000), level: Optional[str] = None,
                source: Optional[str] = None, type: Optional[str] = None):  # noqa: A002 (same name as the Pi API)
    out = []
    with store.lock:
        for item in reversed(store.logs):
            if level and item.get("level") != level:
                continue
            if source and item.get("source") != source:
                continue
            if type and item.get("type") != type:
                continue
            out.append(item)
            if len(out) >= limit:
                break
    return {"count": len(out), "items": out}


@app.get("/api/analytics/report")
def analytics_report():
    with store.lock:
        if store.report is None:
            raise HTTPException(status_code=503, detail="Statystyki jeszcze nie dotarły")
        return store.report


@app.get("/api/config/modules")
def config_modules():
    with store.lock:
        return store.config.get("modules", [])


@app.get("/api/config/schema/{module_id}")
def config_schema(module_id: str):
    with store.lock:
        schema = store.config.get("schemas", {}).get(module_id)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"Unknown module '{module_id}'")
    return schema


@app.get("/api/config/values/{module_id}")
def config_values(module_id: str):
    with store.lock:
        values = store.config.get("values", {}).get(module_id)
    if values is None:
        raise HTTPException(status_code=404, detail=f"Unknown module '{module_id}'")
    return values


@app.get("/api/config/value/{module_id}/{key}")
def config_value(module_id: str, key: str):
    values = config_values(module_id)
    if key not in values:
        raise HTTPException(status_code=404, detail=f"Unknown key '{key}'")
    return {"key": key, "value": values[key]}


@app.get("/api/{rest:path}")
def unknown_api(rest: str):
    raise HTTPException(status_code=404, detail="Niedostępne w podglądzie")


# ---------------------------------------------------------------- frontend (unchanged GUI)

@app.get("/{path:path}")
def frontend(path: str):
    target = (FRONT_DIR / (path or "index.html")).resolve()
    if FRONT_DIR.resolve() not in target.parents or not target.is_file():
        target = FRONT_DIR / "index.html"
    return FileResponse(target, headers={"Cache-Control": "no-cache"})
