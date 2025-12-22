# backend/main.py
from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.kernel import Kernel
from .core.aux_runner import AuxRunner
from .config.modules_loader import load_modules_split

from .api.state_api import create_state_router
from .api.config_api import create_config_router
from backend.api.history_api import create_history_router
from backend.api.manual_api import create_manual_router
from backend.api.stats_api import create_stats_router
from backend.api.logs_api import create_logs_router
from backend.core.state_store import StateStore


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
    hardware = RpiHardware(cfg)
else:
    hardware = Hardware()

critical_modules, aux_modules = load_modules_split()

store = StateStore(event_buffer_size=1000)

kernel = Kernel(
    hardware=hardware,
    modules=critical_modules,
    safety_module=None,
    store=store,
)

aux_runner = AuxRunner(store=store, modules=aux_modules)

# --- CONFIG STORE ---

all_modules = critical_modules + aux_modules
BACKEND_ROOT = Path(__file__).resolve().parent
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

def control_loop(stop_event: threading.Event) -> None:
    TICK_INTERVAL = 0.5
    while not stop_event.is_set():
        start = time.time()
        try:
            kernel.step()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[CONTROL LOOP ERROR] {type(exc).__name__}: {exc}")
        elapsed = time.time() - start
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
        remaining = max(0.0, TICK_INTERVAL - elapsed)
        if stop_event.wait(timeout=remaining):
            break


# --- FASTAPI / HTTP API ---

app = FastAPI(
    title="Sterownik kotła",
    version="0.1.0",
)

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

    # pokaż wątki
    for t in threading.enumerate():
        logger.info("Alive thread: name=%s daemon=%s ident=%s", t.name, t.daemon, t.ident)

    logger.info("Shutdown handler finished.")

# --- ROUTERY ---

state_router = create_state_router(store=store)
manual_router = create_manual_router(store=store)
config_router = create_config_router(
    config_store=config_store,
    reload_module_config=reload_any_module,
)

history_base_dir = Path(os.getenv(
    "FURNACE_BRAIN_HISTORY_DIR",
    str(Path(DATA_ROOT) / "modules" / "history")
))

eventlog_base_dir = Path(os.getenv(
    "FURNACE_BRAIN_EVENTLOG_DIR",
    str(Path(DATA_ROOT) / "modules" / "eventlog")
))

stats_router = create_stats_router(store=store, module_id="stats")
app.include_router(stats_router, prefix="/api")

app.include_router(
    create_logs_router(base_dir=eventlog_base_dir, log_dir="data", file_prefix="events"), prefix="/api")

app.include_router(
    create_history_router(
        base_dir=history_base_dir,
        log_dir="data",
        file_prefix="boiler"
    ), prefix="/api")
app.include_router(state_router, prefix="/api")
app.include_router(manual_router, prefix="/api")
app.include_router(config_router, prefix="/api")

