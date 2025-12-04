# backend/main.py
from __future__ import annotations

import threading
import time
from pathlib import Path  # <-- NOWE

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.kernel import Kernel
from .core.aux_runner import AuxRunner
from .config.modules_loader import load_modules_split

from .api.state_api import create_state_router
from .api.config_api import create_config_router

from .core.config_store import ConfigStore          

from .hw.mock import MockHardware as Hardware

hardware = Hardware()

critical_modules, aux_modules = load_modules_split()

kernel = Kernel(
    hardware=hardware,
    modules=critical_modules,
    safety_module=None,
)

aux_runner = AuxRunner(kernel=kernel, modules=aux_modules)

# --- CONFIG STORE ---

BACKEND_ROOT = Path(__file__).resolve().parent
config_store = ConfigStore(BACKEND_ROOT / "modules")


# --- PĘTLE ---

def control_loop() -> None:
    TICK_INTERVAL = 0.5
    while True:
        start = time.time()
        try:
            kernel.step()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[CONTROL LOOP ERROR] {type(exc).__name__}: {exc}")
        elapsed = time.time() - start
        time.sleep(max(0.0, TICK_INTERVAL - elapsed))


def aux_loop() -> None:
    TICK_INTERVAL = 2.0
    while True:
        start = time.time()
        try:
            aux_runner.step()
        except Exception as exc:  # pylint: disable=broad-except
            print(f"[AUX LOOP ERROR] {type(exc).__name__}: {exc}")
        elapsed = time.time() - start
        time.sleep(max(0.0, TICK_INTERVAL - elapsed))


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
    t_control = threading.Thread(target=control_loop, daemon=True)
    t_control.start()
    print("[KERNEL] Control loop started.")

    t_aux = threading.Thread(target=aux_loop, daemon=True)
    t_aux.start()
    print("[AUX] Aux loop started.")


# --- TU SKŁADASZ ROUTERY ---

state_router = create_state_router(kernel=kernel)
config_router = create_config_router(config_store=config_store)

app.include_router(state_router, prefix="/api")
app.include_router(config_router, prefix="/api")
