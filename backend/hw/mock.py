from __future__ import annotations

import time
from dataclasses import dataclass

from backend.hw.interface import HardwareInterface
from backend.core.state import Sensors, Outputs


__all__ = ["MockHardware"]


@dataclass
class _ThermalState:
    """
    Wewnętrzny stan termiczny pieca i instalacji w realnych jednostkach.

    Temperatura w °C, paliwo w kg.
    """
    boiler_temp: float = 40.0        # °C – woda w kotle (~50 l)
    return_temp: float = 35.0        # °C – powrót z instalacji
    radiators_temp: float = 30.0     # °C – woda w obiegu CO (~150 l)
    cwu_temp: float = 35.0           # °C – zasobnik CWU (np. 120 l)
    flue_gas_temp: float = 30.0      # °C – temp. spalin
    hopper_temp: float = 25.0        # °C – zasobnik / podajnik
    outside_temp: float = 5.0        # °C – na zewnątrz

    # PALIWO:
    fuel_buffer: float = 0.0         # kg świeżego paliwa na palniku (nie-aktywne)
    active_fuel: float = 0.0         # kg „żaru” zdolnego do spalania

    stb_triggered: bool = False
    door_open: bool = False


class MockHardware(HardwareInterface):
    """
    Symulator warstwy sprzętowej z dwustanowym paliwem i wpływem dmuchawy na spaliny:

    - Ślimak:
        feeder_on=True → dosypuje ~3.6 g/s do fuel_buffer (świeże paliwo).
    - Paliwo:
        fuel_buffer → (powoli) → active_fuel (suszenie/nagrzewanie).
    - Spalanie:
        active_fuel spala się z szybkością zależną od nadmuchu (powietrze),
        plus wolne dogorywanie, z ograniczeniem ilością powietrza (stoichiometria).
    - Niedopalanie:
        przy pracującym ślimaku i niskim nadmuchu część active_fuel
        jest „wyrzucana” bez spalania (symulacja czarnego groszku w popielniku).
    - Energia:
        Q_chem = m_spalone * 29 MJ/kg → część do wody w kotle, część do spalin,
        straty do kotłowni / domu / komina.
    """

    # --- Stałe fizyczne / parametry kotła ---

    C_WATER = 4180.0  # [J/(kg*K)]

    M_BOILER_WATER = 50.0    # [kg] ~ 50 l w kotle
    M_CO_WATER = 150.0       # [kg] ~ 150 l w instalacji CO
    M_CWU_WATER = 120.0      # [kg] – przyjęty zasobnik 120 l

    C_FLUE = 5000.0          # [J/K] – efektywna pojemność cieplna „spalin”

    ROOM_TEMP = 20.0         # [°C]
    MAX_DT = 5.0             # [s]

    # --- Paliwo / spalanie ---

    FUEL_FEED_RATE = 0.0036      # [kg/s] – ~3.6 g/s

    FUEL_LHV = 29_000_000.0      # [J/kg] – wartość opałowa ekogroszku 29 MJ/kg

    # Powietrze / dmuchawa
    MAX_AIR_FLOW_M3_H = 255.0
    MAX_AIR_FLOW_M3_S = MAX_AIR_FLOW_M3_H / 3600.0  # [m³/s]
    AIR_PER_KG_FUEL_STOICH = 9.0  # [m³/kg]

    BOILER_EFFICIENCY = 0.80      # 80% energii zostaje w kotle+spalinach
    FRACTION_TO_WATER = 0.75      # z tej części tyle w wodzie, reszta w spalinach

    # --- 2-etapowy model paliwa ---

    FUEL_DRYING_TIME_S = 90.0     # czas przejścia świeżego paliwa w żar
    NATURAL_BURN_TIME_S = 900.0   # ~15 min dogorywania całego żaru bez nadmuchu

    # --- Straty ciepła ---

    U_BOILER_LOSS_W_PER_K = 50.0   # kocioł -> kotłownia
    U_CO_LOSS_W_PER_K = 400.0      # grzejniki -> dom
    U_CWU_LOSS_W_PER_K = 20.0      # zasobnik -> kotłownia
    U_FLUE_LOSS_W_PER_K = 60.0     # spaliny -> komin/otoczenie
    HOPPER_COUPLING_W_PER_K = 5.0  # kocioł -> zasobnik

    # --- Pompy ---

    # 1320 l/h ≈ 0.3667 kg/s
    PUMP_FLOW_KG_PER_S = 1320.0 / 3600.0

    # --- STB ---

    STB_LIMIT = 95.0  # [°C]

    def __init__(self) -> None:
        self._state = _ThermalState()
        self._outputs = Outputs()
        self._last_update = time.monotonic()

        self._C_boiler = self.M_BOILER_WATER * self.C_WATER
        self._C_co = self.M_CO_WATER * self.C_WATER
        self._C_cwu = self.M_CWU_WATER * self.C_WATER

    # ------------------------------------------------------------------
    #  HardwareInterface
    # ------------------------------------------------------------------
    def read_sensors(self) -> Sensors:
        now = time.monotonic()
        dt = now - self._last_update
        self._last_update = now

        if dt < 0:
            dt = 0.0
        if dt > self.MAX_DT:
            dt = self.MAX_DT

        self._step_physics(dt)

        s = self._state
        return Sensors(
            boiler_temp=s.boiler_temp,
            return_temp=s.return_temp,
            radiators_temp=s.radiators_temp,
            cwu_temp=s.cwu_temp,
            flue_gas_temp=s.flue_gas_temp,
            hopper_temp=s.hopper_temp,
            outside_temp=s.outside_temp,
            stb_triggered=s.stb_triggered,
            door_open=s.door_open,
        )

    def apply_outputs(self, outputs: Outputs) -> None:
        self._outputs = Outputs(**outputs.__dict__)

    # ------------------------------------------------------------------
    #  Fizyka kotła
    # ------------------------------------------------------------------
    def _step_physics(self, dt: float) -> None:
        s = self._state
        o = self._outputs

        # 1) ŚLIMAK – dosypywanie świeżego paliwa
        if o.feeder_on:
            s.fuel_buffer += self.FUEL_FEED_RATE * dt

        # 2) fuel_buffer -> active_fuel (suszenie/nagrzewanie)
        if s.fuel_buffer > 0.0:
            frac = dt / self.FUEL_DRYING_TIME_S
            if frac > 1.0:
                frac = 1.0
            m_to_active = s.fuel_buffer * frac
            s.fuel_buffer -= m_to_active
            s.active_fuel += m_to_active

        # 3) Spalanie active_fuel zależne od nadmuchu
        fan_fraction = max(0.0, min(1.0, o.fan_power / 100.0))

        burn_mass = 0.0
        if s.active_fuel > 0.0:
            # spalanie ograniczone powietrzem:
            air_flow_m3_s = fan_fraction * self.MAX_AIR_FLOW_M3_S
            max_burn_by_air = air_flow_m3_s / self.AIR_PER_KG_FUEL_STOICH  # [kg/s]

            # naturalne dogorywanie przy braku nadmuchu
            natural_burn_rate = s.active_fuel / self.NATURAL_BURN_TIME_S   # [kg/s]

            burn_rate = natural_burn_rate + max_burn_by_air
            burn_mass = min(s.active_fuel, burn_rate * dt)
            s.active_fuel -= burn_mass

        # 3a) Niedopalony węgiel wypychany do popielnika przy małym nadmuchu
        if o.feeder_on and s.active_fuel > 0.0:
            # przy 0% nadmuchu -> maksimum strat, przy 100% -> 0 strat
            loss_factor = max(0.0, 1.0 - fan_fraction)
            loss_rate = loss_factor * self.FUEL_FEED_RATE       # [kg/s]
            lost_mass = min(s.active_fuel, loss_rate * dt)
            s.active_fuel -= lost_mass
            # UWAGA: lost_mass nie daje żadnej energii – to „czarny groszek w popielniku”

        # 4) Energia ze spalania
        Q_chem = burn_mass * self.FUEL_LHV           # [J]
        Q_useful = Q_chem * self.BOILER_EFFICIENCY  # [J]

        Q_to_water = Q_useful * self.FRACTION_TO_WATER
        Q_to_flue = Q_useful * (1.0 - self.FRACTION_TO_WATER)

        if Q_to_water > 0.0:
            dT_boiler = Q_to_water / self._C_boiler
            s.boiler_temp += dT_boiler

        # 5) Pompa CO – transfer kocioł <-> obieg CO
        if o.pump_co_on:
            deltaT = s.boiler_temp - s.radiators_temp
            Q_co = self.PUMP_FLOW_KG_PER_S * self.C_WATER * deltaT * dt
            s.boiler_temp -= Q_co / self._C_boiler
            s.radiators_temp += Q_co / self._C_co
        else:
            sink_temp = (self.ROOM_TEMP + s.outside_temp) / 2.0
            Q_loss_co = self.U_CO_LOSS_W_PER_K * (s.radiators_temp - sink_temp) * dt
            s.radiators_temp -= Q_loss_co / self._C_co

        # 6) Pompa CWU – transfer kocioł <-> zasobnik
        if o.pump_cwu_on:
            deltaT_cwu = s.boiler_temp - s.cwu_temp
            Q_cwu = self.PUMP_FLOW_KG_PER_S * self.C_WATER * deltaT_cwu * dt
            s.boiler_temp -= Q_cwu / self._C_boiler
            s.cwu_temp += Q_cwu / self._C_cwu
        else:
            Q_loss_cwu = self.U_CWU_LOSS_W_PER_K * (s.cwu_temp - self.ROOM_TEMP) * dt
            s.cwu_temp -= Q_loss_cwu / self._C_cwu

        # 7) Cyrkulacja CWU – lekkie dociąganie do ROOM_TEMP
        if o.pump_circ_on:
            Q_circ = self.U_CWU_LOSS_W_PER_K * (self.ROOM_TEMP - s.cwu_temp) * dt * 0.5
            s.cwu_temp += Q_circ / self._C_cwu

        # 8) Straty kotła do kotłowni
        Q_loss_boiler = self.U_BOILER_LOSS_W_PER_K * (s.boiler_temp - self.ROOM_TEMP) * dt
        s.boiler_temp -= Q_loss_boiler / self._C_boiler

        # 9) Spaliny – podgrzewanie i chłodzenie
        if Q_to_flue > 0.0:
            # większy nadmuch → więcej energii „widzi” czujnik spalin
            flue_coupling = 0.3 + 0.7 * fan_fraction  # 30% przy 0%, 100% przy 100%
            dT_flue = (Q_to_flue * flue_coupling) / self.C_FLUE
            s.flue_gas_temp += dT_flue

        sink_temp_flue = s.outside_temp
        Q_loss_flue = self.U_FLUE_LOSS_W_PER_K * (s.flue_gas_temp - sink_temp_flue) * dt
        s.flue_gas_temp -= Q_loss_flue / self.C_FLUE

        # 10) Powrót – dociąganie do średniej (boiler + CO)
        mix_target = 0.5 * (s.boiler_temp + s.radiators_temp)
        alpha = 0.2 * dt
        if alpha > 1.0:
            alpha = 1.0
        s.return_temp = (1.0 - alpha) * s.return_temp + alpha * mix_target

        # 11) Hopper – lekko dogrzewa się od kotła
        Q_hopper = self.HOPPER_COUPLING_W_PER_K * (s.boiler_temp - s.hopper_temp) * dt
        C_hopper = 2000.0  # [J/K] – umowne
        s.hopper_temp += Q_hopper / C_hopper

        # 12) STB
        if s.boiler_temp >= self.STB_LIMIT:
            s.stb_triggered = True

        # 13) Ograniczenia
        self._clamp_temps()

    def _clamp_temps(self) -> None:
        s = self._state

        def clamp(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, v))

        s.boiler_temp = clamp(s.boiler_temp, -20.0, 130.0)
        s.return_temp = clamp(s.return_temp, -20.0, 130.0)
        s.radiators_temp = clamp(s.radiators_temp, -20.0, 110.0)
        s.cwu_temp = clamp(s.cwu_temp, -20.0, 110.0)
        s.flue_gas_temp = clamp(s.flue_gas_temp, -20.0, 300.0)
        s.hopper_temp = clamp(s.hopper_temp, -40.0, 100.0)
        s.outside_temp = clamp(s.outside_temp, -40.0, 40.0)
