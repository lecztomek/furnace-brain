# backend/main.py
from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.kernel import Kernel
from .core.aux_runner import AuxRunner
from .config.modules_loader import load_modules_split
from .api.http_api import create_api_router

# --- HARDWARE: JEDNA LINIJKA ---

from .hw.mock import MockHardware as Hardware
# później zmienisz na:
# from .hw.rpi import RpiHardware as Hardware

hardware = Hardware()

# --- MODUŁY: KRYTYCZNE + AUX ---

critical_modules, aux_modules = load_modules_split()

kernel = Kernel(
    hardware=hardware,
    modules=critical_modules,
    safety_module=None,   # safety możesz dodać później
)

aux_runner = AuxRunner(kernel=kernel, modules=aux_modules)


# --- PĘTLA KRYTYCZNA ---

def control_loop() -> None:
    """
    Pętla sterująca bezpieczeństwem/ogniem – tylko moduły krytyczne.
    """
    TICK_INTERVAL = 0.5  # sekundy

    while True:
        start = time.time()
        try:
            kernel.step()
        except Exception as exc:  # pylint: disable=broad-except
            # TODO: logi do historii
            print(f"[CONTROL LOOP ERROR] {type(exc).__name__}: {exc}")
        elapsed = time.time() - start
        time.sleep(max(0.0, TICK_INTERVAL - elapsed))


# --- PĘTLA AUX (NIEKRYTYCZNA) ---

def aux_loop() -> None:
    """
    Pętla dla modułów NIEkrytycznych (historia, statystyki, itp.).
    Może chodzić wolniej, np. co 2–5 s.
    """
    TICK_INTERVAL = 2.0  # sekundy

    while True:
        start = time.time()
        try:
            aux_runner.step()
        except Exception as exc:  # pylint: disable=broad-except
            # Nie rusza sterowania – najwyżej nie zapiszą się logi.
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
    """
    Przy starcie: odpalamy DWIE pętle – krytyczną i aux – w osobnych wątkach.
    """
    t_control = threading.Thread(target=control_loop, daemon=True)
    t_control.start()
    print("[KERNEL] Control loop started.")

    t_aux = threading.Thread(target=aux_loop, daemon=True)
    t_aux.start()
    print("[AUX] Aux loop started.")


api_router = create_api_router(kernel)
app.include_router(api_router, prefix="/api")
