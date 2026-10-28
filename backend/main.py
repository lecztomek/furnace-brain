# backend/main.py
from __future__ import annotations

import threading
import time
import datetime as dt
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .core.kernel import Kernel
from .core.aux_runner import AuxRunner
from .config.modules_loader import load_modules_split, load_module_descriptors

from .api.state_api import create_state_router
from .api.config_api import create_config_router
from backend.api.history_api import create_history_router
from backend.api.manual_api import create_manual_router
from backend.api.stats_api import create_stats_router
from backend.api.logs_api import create_logs_router
from backend.api.analytics_api import create_analytics_router
from backend.api.service_api import create_service_router, LoopStats
from backend.core.state_store import StateStore

from backend.core.clock import RealClock, SimClock
from backend.core.version import detect_version
from backend.core.mode_file import ModeFile

from .core.config_store import ConfigStore
import faulthandler
import sys
import os


import logging

from .hw.mock import MockHardware as Hardware
from .hw.rpi_hw import RpiHardware, HardwareConfig, Ds18b20Config, Max6675Config, PinConfig

faulthandler.enable()
logging.basicConfig(
    level=logging.INFO,  # bazowy poziom
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logging.getLogger("backend.hw.mock.mixer").setLevel(logging.DEBUG)
# no line per HTTP request: the GUI and the remote view poll every few
# seconds, and the system log on the Pi lives on the SD card (wear). API
# errors are still logged (API_ERROR in the furnace log on the USB drive).
logging.getLogger("uvicorn.access").disabled = True
logger = logging.getLogger(__name__)

DATA_ROOT = Path(os.getenv("FURNACE_BRAIN_DATA_ROOT", str(Path(__file__).resolve().parent)))

# --- INICJALIZACJA SPRZĘTU I MODUŁÓW ---

cfg = HardwareConfig(
    ds18b20=Ds18b20Config(
        rom_to_field={
            # Dopasuj nazwy pól do Twojego backend.core.state.Sensors
            "28-000000506777": "boiler_temp",     # np. kocioł
            "28-00000052b809": "hopper_temp",        # np. CWU
            "28-0000006f0738": "radiators_temp",  # np. CO / powrót / grzejniki
        },
        base_path="/sys/bus/w1/devices",
    ),

    # Spaliny z termopary (MAX6675)
    # Jeśli masz na CE0 -> spidev0.0; jeśli na CE1 -> spi_dev=1 (spidev0.1)
    max6675=Max6675Config(
        spi_bus=0,
        spi_dev=0,
        max_hz=4_000_000,
        mode=0,
    ),

    pins=PinConfig(
        # wyjścia SSR/ULN zgodnie z Twoją wiązką:
        feeder_pin=16,       # ŚLIMAK: pin 36 -> GPIO16
        pump_cwu_pin=20,     # CWU:    pin 38 -> GPIO20
        pump_co_pin=21,      # CO:     pin 40 -> GPIO21
        mixer_open_pin=6,    # ZAWÓR:  pin 31 -> GPIO6
        mixer_close_pin=12,  # ZAWÓR:  pin 32 -> GPIO12

        # dmuchawa YYAC-3S
        fan_pwm_pin=19,      # PWM:    pin 35 -> GPIO19
        fan_pwm_freq_hz=200,
    ),

    fan_inverted=False,
    sensors_poll_interval_s=5.0,
    sensors_stale_after_s=20.0,
)

# --- WYBÓR HARDWARE NA PODSTAWIE ENV (JEDYNA ZMIANA) ---

def _env_truthy(name: str) -> bool:
    v = os.getenv(name)
    if v is None:
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}

if _env_truthy("FURNACE_BRAIN_HW_RPI"):
    clock = RealClock()
    hardware = RpiHardware(cfg)
else:
    clock = SimClock(scale=float(os.getenv("FURNACE_BRAIN_TIME_SCALE", "10")))
    hardware = Hardware(clock=clock)

is_rpi = _env_truthy("FURNACE_BRAIN_HW_RPI")
if is_rpi:
    run_root = DATA_ROOT
else:
    run_id = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = DATA_ROOT / "sim_runs" / run_id

    # test environment: past days of demo data for the statistics page
    if os.getenv("FURNACE_BRAIN_DEMO_DAYS"):
        from backend.core.demo_history import seed as seed_demo_history
        seed_demo_history(run_root, int(os.getenv("FURNACE_BRAIN_DEMO_DAYS", "0")))

BACKEND_ROOT = Path(__file__).resolve().parent

# new fields from schema.yaml -> append them to values.yaml before modules load their settings
ConfigStore(
    BACKEND_ROOT / "modules",
    module_ids_in_order=[d.id for d in load_module_descriptors()],
).fill_missing_defaults()

critical_modules, aux_modules = load_modules_split(data_root=run_root)

store = StateStore(event_buffer_size=1000)

kernel = Kernel(
    hardware=hardware,
    modules=critical_modules,
    safety_module=None,
    store=store,
    clock=clock,
)

aux_runner = AuxRunner(store=store, modules=aux_modules)

# the mode survives restarts (updates, power cuts) - saved on every change
mode_file = ModeFile(run_root / "mode_state.json")
restored_mode = mode_file.load()
if restored_mode is not None:
    with store.locked() as st:
        st.mode = restored_mode
    logger.info("Mode restored after restart: %s", restored_mode.name)

# --- CONFIG STORE ---

all_modules = critical_modules + aux_modules
config_store = ConfigStore(
        BACKEND_ROOT / "modules",
        module_ids_in_order=[m.id for m in all_modules],
    )

# --- FLAGI STOPU + REFERENCJE DO WĄTKÓW ---

control_stop_event = threading.Event()
aux_stop_event = threading.Event()

control_thread: threading.Thread | None = None
aux_thread: threading.Thread | None = None

critical_ids = {m.id for m in critical_modules}
aux_ids = {m.id for m in aux_modules}

def reload_any_module(module_id: str) -> None:
    if module_id in critical_ids:
        kernel.reload_module_config_from_file(module_id)
        return
    if module_id in aux_ids:
        aux_runner.reload_module_config_from_file(module_id)
        return
    raise KeyError(f"Unknown module '{module_id}'")

# --- PĘTLE ---

# step durations for the diagnostics page
LOOP_STATS = {"control": LoopStats(0.5), "aux": LoopStats(2.0)}

def control_loop(stop_event: threading.Event) -> None:
    TICK_INTERVAL = 0.5
    while not stop_event.is_set():
        start = time.time()
        try:
            kernel.step()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[CONTROL LOOP ERROR] {type(exc).__name__}: {exc}")
        with store.locked() as st:
            mode_now = st.mode
        mode_file.save_if_changed(mode_now)
        elapsed = time.time() - start
        LOOP_STATS["control"].add(elapsed)
        remaining = max(0.0, TICK_INTERVAL - elapsed)
        # wait zamiast time.sleep -> szybka reakcja na sygnał stop
        if stop_event.wait(timeout=remaining):
            break


def aux_loop(stop_event: threading.Event) -> None:
    TICK_INTERVAL = 2.0
    while not stop_event.is_set():
        start = time.time()
        try:
            aux_runner.step()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[AUX LOOP ERROR] {type(exc).__name__}: {exc}")
        elapsed = time.time() - start
        LOOP_STATS["aux"].add(elapsed)
        remaining = max(0.0, TICK_INTERVAL - elapsed)
        if stop_event.wait(timeout=remaining):
            break


# --- FASTAPI / HTTP API ---

app = FastAPI(
    title="Sterownik kotła",
    version="0.1.0",
)

# Any unexpected error in the API goes to the logs page with its cause (the
# Pi's system journal may not be available - the logs page is what's seen
# remotely). The same error on the same path is logged at most every 10 min.
_api_error_logged: dict = {}


@app.exception_handler(Exception)
async def log_unhandled_api_error(request: Request, exc: Exception):
    import traceback
    from fastapi.responses import JSONResponse
    from backend.core.state import Event, EventLevel

    error = f"{type(exc).__name__}: {exc}"
    where = f"{request.method} {request.url.path}"
    key = (where, error)
    now = time.time()
    if now - _api_error_logged.get(key, 0.0) >= 600:
        _api_error_logged[key] = now
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        store.publish_events([Event(
            ts=now, source="api", level=EventLevel.ERROR, type="API_ERROR",
            message=f"Błąd API {where}: {error}",
            data={"path": request.url.path, "error": error, "traceback": "".join(tb[-6:])[-3000:]},
        )])
    logger.exception("API error %s", where)
    return JSONResponse(status_code=500, content={"detail": error})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    global control_thread, aux_thread

    control_stop_event.clear()
    aux_stop_event.clear()

    control_thread = threading.Thread(target=control_loop, args=(control_stop_event,), daemon=True, name="control_loop")
    control_thread.start()

    aux_thread = threading.Thread(target=aux_loop, args=(aux_stop_event,), daemon=True, name="aux_loop")
    aux_thread.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global control_thread, aux_thread

    logger.info("Shutdown requested: stopping loops...")

    control_stop_event.set()
    aux_stop_event.set()

    if control_thread is not None:
        control_thread.join(timeout=5.0)
        logger.info("[KERNEL] alive=%s", control_thread.is_alive())

    if aux_thread is not None:
        aux_thread.join(timeout=5.0)
        logger.info("[AUX] alive=%s", aux_thread.is_alive())

    # feeder and blower off for the time until the new process takes over
    # (an update): the pins keep their last state, a feeder mid-dose would run on
    try:
        from backend.core.shutdown import parking_outputs
        with store.locked() as st:
            parked = parking_outputs(st.outputs, st.sensors.boiler_temp)
        hardware.apply_outputs(parked)
        logger.info("Outputs parked: feeder off, fan off, pumps CO=%s CWU=%s", parked.pump_co_on, parked.pump_cwu_on)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Parking the outputs failed")

    # pokaż wątki
    for t in threading.enumerate():
        logger.info("Alive thread: name=%s daemon=%s ident=%s", t.name, t.daemon, t.ident)

    logger.info("Shutdown handler finished.")

# --- ROUTERY ---

APP_INFO = {
    "version": detect_version(BACKEND_ROOT.parent),
    "started_at": time.time(),  # wall clock, also in simulation
}
logger.info("Furnace Brain version %s", APP_INFO["version"])

state_router = create_state_router(store=store, config_store=config_store, app_info=APP_INFO)
manual_router = create_manual_router(store=store)
config_router = create_config_router(
    config_store=config_store,
    reload_module_config=reload_any_module,
    publish_events=store.publish_events,
)

API_ROOT = run_root

history_base_dir = Path(os.getenv(
    "FURNACE_BRAIN_HISTORY_DIR",
    str(API_ROOT / "modules" / "history")
))

eventlog_base_dir = Path(os.getenv(
    "FURNACE_BRAIN_EVENTLOG_DIR",
    str(API_ROOT / "modules" / "eventlog")
))

stats_base_dir = Path(os.getenv(
    "FURNACE_BRAIN_STATS_DIR",
    str(API_ROOT / "modules" / "stats")
))

app.include_router(
    create_logs_router(base_dir=eventlog_base_dir, log_dir="data", file_prefix="events"), prefix="/api")

app.include_router(
    create_stats_router(
        store=store,
        base_dir=stats_base_dir,
        log_dir="data",
        file_prefix_5m="stats5m",
        daily_file="stats_daily.csv",
        module_id="stats",
        timezone="Europe/Warsaw",
    ),
    prefix="/api",
)

app.include_router(
    create_history_router(
        base_dir=history_base_dir,
        log_dir="data",
        file_prefix="boiler"
    ), prefix="/api")
    
app.include_router(create_analytics_router(data_root=API_ROOT, config_store=config_store, store=store), prefix="/api")
app.include_router(create_service_router(store=store, config_store=config_store, data_root=API_ROOT,
                                         app_info=APP_INFO, loop_stats=LOOP_STATS), prefix="/api")
app.include_router(state_router, prefix="/api")
app.include_router(manual_router, prefix="/api")
app.include_router(config_router, prefix="/api")


# Cloud test environment: a single process also serves the frontend (on the Pi gateway.py does that)
SERVE_FRONTEND_DIR = os.getenv("FURNACE_BRAIN_SERVE_FRONTEND")
if SERVE_FRONTEND_DIR:
    from fastapi.staticfiles import StaticFiles

    class NoCacheStaticFiles(StaticFiles):
        # browsers revalidate every time, so a new version is never mixed with cached old files
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache"
            return response

    app.mount("/", NoCacheStaticFiles(directory=SERVE_FRONTEND_DIR, html=True), name="frontend")
