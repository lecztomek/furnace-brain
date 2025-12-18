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

from .core.config_store import ConfigStore


import logging

from .hw.mock import MockHardware as Hardware
#from .hw.rpi_hw import RpiHardware, HardwareConfig, Ds18b20Config, Max6675Config, PinConfig

logging.basicConfig(
    level=logging.INFO,  # bazowy poziom
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logging.getLogger("backend.hw.mock.mixer").setLevel(logging.DEBUG)

# --- INICJALIZACJA SPRZĘTU I MODUŁÓW ---

#cfg = HardwareConfig(
#    ds18b20=Ds18b20Config(
#        rom_to_field={
#            "28-00000aaaaaaa": "boiler_temp",
#            "28-00000bbbbbbb": "cwu_temp",
#            "28-00000ccccccc": "radiators_temp",
#            "28-00000ddddddd": "return_temp",
#            "28-00000eeeeeee": "hopper_temp",
#        }
#    ),
#    max6675=Max6675Config(spi_bus=0, spi_dev=0),
#    pins=PinConfig(
#        feeder_pin=17,
#        pump_co_pin=27,
#        pump_cwu_pin=22,
#        fan_pwm_pin=18,
#        fan_pwm_freq_hz=200,
#    ),
#    fan_inverted=False,
#)

#hardware = RpiHardware(cfg)
hardware = Hardware()
critical_modules, aux_modules = load_modules_split()

kernel = Kernel(
    hardware=hardware,
    modules=critical_modules,
    safety_module=None,
)

aux_runner = AuxRunner(kernel=kernel, modules=aux_modules)


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
def on_startup() -> None:
    global control_thread, aux_thread

    # upewniamy się, że eventy są w stanie "działaj"
    control_stop_event.clear()
    aux_stop_event.clear()

    control_thread = threading.Thread(
        target=control_loop,
        args=(control_stop_event,),
        daemon=False,  # chcemy kulturalne zamknięcie, będziemy joinować w shutdown
    )
    control_thread.start()
    print("[KERNEL] Control loop started.")

    aux_thread = threading.Thread(
        target=aux_loop,
        args=(aux_stop_event,),
        daemon=False,
    )
    aux_thread.start()
    print("[AUX] Aux loop started.")


@app.on_event("shutdown")
def on_shutdown() -> None:
    # sygnał stopu dla pętli
    control_stop_event.set()
    aux_stop_event.set()

    # czekamy aż się ładnie zakończą
    if control_thread is not None:
        control_thread.join(timeout=5.0)
        print("[KERNEL] Control loop stopped.")

    if aux_thread is not None:
        aux_thread.join(timeout=5.0)
        print("[AUX] Aux loop stopped.")


# --- ROUTERY ---

state_router = create_state_router(kernel=kernel)
manual_router = create_manual_router(kernel=kernel)
config_router = create_config_router(config_store=config_store, kernel=kernel)

history_base_dir = Path(__file__).resolve().parent / "modules" / "history"
eventlog_base_dir = Path(__file__).resolve().parent / "modules" / "eventlog"

stats_router = create_stats_router(get_state=lambda: kernel.state, module_id="stats")
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
