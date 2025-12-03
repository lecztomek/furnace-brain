# backend/hw/interface.py
from __future__ import annotations

from typing import Protocol

from backend.core.state import Sensors, Outputs


class HardwareInterface(Protocol):
    """
    Interfejs warstwy sprzętowej.
    Implementuje go zarówno mock (symulator), jak i prawdziwe RPi.
    """

    def read_sensors(self) -> Sensors:
        """
        Zwraca aktualne odczyty wszystkich czujników jako obiekt Sensors.
        Nie powinna robić długich, blokujących operacji – kernel zakłada,
        że to jest szybki snapshot.
        """
        ...

    def apply_outputs(self, outputs: Outputs) -> None:
        """
        Ustawia fizyczne wyjścia według obiektu Outputs:
        - PWM dmuchawy,
        - SSR pomp, ślimaka, zaworu, syreny itd.

        IMPORTANT:
        - ta metoda powinna być idempotentna (kilka wywołań z tymi samymi
          danymi nie powinno szkodzić),
        - nie powinna rzucać wyjątków przy drobnych problemach – zamiast tego
          lepiej raportować problemy innym kanałem (np. Event).
        """
        ...
