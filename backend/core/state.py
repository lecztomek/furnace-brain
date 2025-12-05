# backend/core/state.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
import time


class EventLevel(Enum):
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    ALARM = auto()


@dataclass
class Event:
    """
    Wewnętrzne zdarzenie generowane przez moduły lub kernel.
    Trafia potem do modułu historii/logów.
    """
    ts: float
    source: str          # np. "blower", "feeder", "kernel", "safety"
    level: EventLevel
    type: str            # np. "FEEDER_CYCLE_START", "OVERHEAT_ALARM"
    message: str         # krótki opis dla człowieka
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Sensors:
    """
    Snapshot wszystkich odczytów z czujników, które widzi sterownik.
    To zwraca warstwa sprzętowa (mock lub RPi).
    Dodawaj tu pola w miarę rozwoju projektu.
    """
    boiler_temp: Optional[float] = None        # temp. kotła
    return_temp: Optional[float] = None        # temp. powrotu
    radiators_temp: Optional[float] = None     # temp. zasilania obiegu grzejników
    cwu_temp: Optional[float] = None           # temp. zasobnika CWU
    flue_gas_temp: Optional[float] = None      # temp. spalin
    hopper_temp: Optional[float] = None        # temp. zasobnika / podajnika
    outside_temp: Optional[float] = None       # temp. zewnętrzna
    mixer_temp: Optional[float] = None         # temp. za zaworem mieszającym / obiegu zmieszanego

    # wejścia cyfrowe itp. (krańcówki, STB, itp.) – dodasz jak będzie potrzeba
    stb_triggered: Optional[bool] = None       # czy termostat bezpieczeństwa zadziałał
    door_open: Optional[bool] = None           # np. drzwiczki kotła


@dataclass
class Outputs:
    """
    Wyjścia sterujące – to, co finalnie trafi na SSR/YYAC/etc.
    Kernel zbiera outputs cząstkowe z modułów, przepuszcza przez safety
    i przekazuje finalny Outputs do warstwy sprzętowej.
    """
    fan_power: int = 0            # 0–100 %, dmuchawa / YYAC-3S
    feeder_on: bool = False       # ślimak podajnika
    pump_co_on: bool = False      # pompa CO
    pump_cwu_on: bool = False     # pompa CWU
    pump_circ_on: bool = False    # pompa cyrkulacji (jeśli będzie)
    mixer_open_on: bool = False   # zawór mieszający – sygnał OTWÓRZ
    mixer_close_on: bool = False  # zawór mieszający – sygnał ZAMKNIJ
    alarm_buzzer_on: bool = False # sygnał akustyczny alarmu
    alarm_relay_on: bool = False  # ew. przekaźnik alarmowy


class ModuleHealth(Enum):
    OK = auto()
    WARNING = auto()
    ERROR = auto()
    DISABLED = auto()


class BoilerMode(Enum):
    """
    Tryb pracy kotła:
    - IGNITION: rozpalanie
    - WORK: praca
    - OFF: wyłączony
    - MANUAL: ręczne sterowanie
    """
    IGNITION = auto()
    WORK = auto()
    OFF = auto()
    MANUAL = auto()


@dataclass
class ModuleStatus:
    """
    Stan pojedynczego modułu widziany przez kernel.
    Uzupełniany przez kernel na podstawie wyników ticków i wyjątków.
    """
    id: str
    health: ModuleHealth = ModuleHealth.OK
    last_error: Optional[str] = None
    last_tick_duration: float = 0.0
    last_updated: float = field(default_factory=time.time)


@dataclass
class SystemState:
    """
    Globalny stan systemu – snapshot, który kernel przekazuje modułom
    jako tylko-do-odczytu; służy też do serwowania /api/state do GUI.
    """
    ts: float = field(default_factory=time.time)

    # Ostatnie odczyty z czujników:
    sensors: Sensors = field(default_factory=Sensors)

    # Aktualne wyjścia (po safety) – to, co naprawdę jest podawane na hardware:
    outputs: Outputs = field(default_factory=Outputs)

    # Tryb pracy kotła:
    mode: BoilerMode = BoilerMode.OFF

    alarm_active: bool = False
    alarm_message: Optional[str] = None

    # Statusy poszczególnych modułów (id -> status):
    modules: Dict[str, ModuleStatus] = field(default_factory=dict)

    # Ostatnie wygenerowane eventy (np. z bieżącego ticka):
    recent_events: List[Event] = field(default_factory=list)

    # Dodatkowe dane do /api/state możesz tu dopisywać w miarę potrzeb.
