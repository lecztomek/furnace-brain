from __future__ import annotations

import time
from dataclasses import dataclass

from backend.hw.interface import HardwareInterface
from backend.core.state import Sensors, Outputs


__all__ = ["MockHardware"]


@dataclass
class _ThermalState:
    """
    Wewnętrzny stan termiczny pieca i instalacji.
    Wszystko w bardzo uproszczonych 'jednostkach' – ważne są zależności,
    nie ścisła fizyka.
    """
    boiler_temp: float = 40.0        # °C
    return_temp: float = 35.0        # °C
    radiators_temp: float = 30.0     # °C
    cwu_temp: float = 35.0           # °C
    flue_gas_temp: float = 30.0      # °C
    hopper_temp: float = 25.0        # °C
    outside_temp: float = 5.0        # °C

    fuel_buffer: float = 0.0         # „kg” paliwa w palniku
    stb_triggered: bool = False
    door_open: bool = False          # na razie nieużywane, ale jest pole


class MockHardware(HardwareInterface):
    """
    Symulator warstwy sprzętowej.

    Zasada działania:
    - Ślimak (feeder_on=True) dosypuje paliwo do „bufora paliwa”.
    - Dmuchawa (fan_power 0–100%) spala to paliwo; im większy nadmuch,
      tym szybciej spalanie i więcej energii na jednostkę czasu.
    - Wygenerowana energia grzeje:
        * kocioł (boiler_temp),
        * spaliny (flue_gas_temp).
    - Pompki CO/CWU/cyrkulacji wyciągają ciepło z kotła:
        * przy włączonych pompkach kocioł chłodzi się szybciej,
        * przy wyłączonych – „gotuje się”, bo energia zostaje w nim.
    - Środowisko (outside_temp) powoli chłodzi całość.
    """

    # --- Stałe symulacji (na czuja, ale zachowują logikę z opisu) ---
    AMBIENT_TEMP = 20.0          # temperatura otoczenia kotłowni
    MAX_DT = 5.0                 # max krok czasowy w sekundach (dla stabilności)

    FUEL_FEED_RATE = 0.2         # kg/s przy włączonym ślimaku
    ENERGY_PER_KG = 5.0          # „energia” na kg paliwa (jednostki umowne)

    # Wpływ spalania na temperatury
    K_BOILER_HEAT_FROM_BURN = 0.15   # jak szybko energia ze spalania podnosi temp. kotła
    K_FLUE_HEAT_FROM_BURN = 0.10     # jak szybko energia ze spalania podnosi temp. spalin

    # Straty ciepła / chłodzenie
    K_BOILER_LOSS = 0.01             # naturalne straty ciepła kotła do otoczenia
    K_RADIATORS_LOSS = 0.02          # radiatory → otoczenie
    K_CWU_LOSS = 0.01                # zasobnik → otoczenie
    K_FLUE_LOSS = 0.05               # spaliny → schładzanie do ambient

    # Transfer ciepła przy pracy pompek
    K_PUMP_CO_TRANSFER = 0.06        # jak mocno pompa CO wyciąga ciepło z kotła
    K_PUMP_CWU_TRANSFER = 0.04       # jak mocno pompa CWU wyciąga ciepło z kotła
    K_PUMP_CIRC_TRANSFER = 0.03      # wpływ pompy cyrkulacyjnej na ciepło z CWU
    K_RETURN_MIX = 0.04              # jak mocno temperatura powrotu zbliża się do zasilania

    # Bezpieczeństwo
    STB_LIMIT = 95.0                 # powyżej tej temperatury uznajemy, że STB zadziałał

    def __init__(self) -> None:
        self._state = _ThermalState()
        self._outputs = Outputs()  # ostatnio zaaplikowane wyjścia
        self._last_update = time.monotonic()

    # ------------------------------------------------------------------
    #  Implementacja HardwareInterface
    # ------------------------------------------------------------------
    def read_sensors(self) -> Sensors:
        """
        Zwraca snapshot czujników. Przy okazji symuluje ewolucję w czasie
        od ostatniego odczytu, na podstawie aktualnych Outputs.
        """
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
        """
        Zapamiętuje aktualne wyjścia sterujące.
        Nie robi długich obliczeń – logikę fizyki liczymy przy read_sensors().
        """
        # Kopia, żeby kernel nie mógł potem zmodyfikować poza naszym widokiem
        self._outputs = Outputs(**outputs.__dict__)

    # ------------------------------------------------------------------
    #  Wewnętrzna fizyka kotła
    # ------------------------------------------------------------------
    def _step_physics(self, dt: float) -> None:
        s = self._state
        o = self._outputs

        # 1) ŚLIMAK – dosypywanie paliwa
        if o.feeder_on:
            s.fuel_buffer += self.FUEL_FEED_RATE * dt

        # 2) DMUCHAWA – spalanie paliwa
        fan_factor = max(0.0, min(1.0, o.fan_power / 100.0))
        # Jeżeli nie ma nadmuchu → praktycznie brak spalania
        if fan_factor < 0.01 or s.fuel_buffer <= 0.0:
            burn_mass = 0.0
        else:
            # Maksymalna ilość spalana w jednostce czasu
            burn_rate = fan_factor * 0.5  # kg/s przy pełnym nadmuchu
            burn_mass = min(s.fuel_buffer, burn_rate * dt)

        s.fuel_buffer -= burn_mass
        if s.fuel_buffer < 0:
            s.fuel_buffer = 0.0

        energy_released = burn_mass * self.ENERGY_PER_KG  # jednostki umowne

        # 3) Jak rozdziela się energia ze spalania?
        # Większość w kotle, reszta w spaliny.
        boiler_heat_gain = energy_released * self.K_BOILER_HEAT_FROM_BURN
        flue_heat_gain = energy_released * self.K_FLUE_HEAT_FROM_BURN

        # Dampening przy bardzo wysokich temperaturach – im cieplej,
        # tym trudniej jeszcze bardziej grzać.
        boiler_temp_factor = max(0.2, 1.0 - (s.boiler_temp - 60.0) / 200.0)
        flue_temp_factor = max(0.2, 1.0 - (s.flue_gas_temp - 80.0) / 300.0)

        s.boiler_temp += boiler_heat_gain * boiler_temp_factor
        s.flue_gas_temp += flue_heat_gain * flue_temp_factor

        # 4) POMPKI – wyciąganie ciepła z kotła na obiegi

        # CO: jeżeli pompa CO chodzi, radiatory zbliżają się do temp kotła,
        # a sam kocioł jest dodatkowo chłodzony.
        if o.pump_co_on:
            delta = s.boiler_temp - s.radiators_temp
            s.radiators_temp += delta * self.K_PUMP_CO_TRANSFER * dt
            s.boiler_temp -= abs(delta) * self.K_PUMP_CO_TRANSFER * 0.5 * dt
        else:
            # brak obiegu – radiatory wracają do ambient/outside
            ambient = (self.AMBIENT_TEMP + s.outside_temp) / 2.0
            s.radiators_temp += (ambient - s.radiators_temp) * self.K_RADIATORS_LOSS * dt

        # CWU: podobnie, ale trochę mniejsze transfery
        if o.pump_cwu_on:
            delta_cwu = s.boiler_temp - s.cwu_temp
            s.cwu_temp += delta_cwu * self.K_PUMP_CWU_TRANSFER * dt
            s.boiler_temp -= abs(delta_cwu) * self.K_PUMP_CWU_TRANSFER * 0.4 * dt
        else:
            s.cwu_temp += (self.AMBIENT_TEMP - s.cwu_temp) * self.K_CWU_LOSS * dt

        # Cyrkulacja CWU – lekko spłaszcza różnice CWU / ambient
        if o.pump_circ_on:
            s.cwu_temp += (self.AMBIENT_TEMP - s.cwu_temp) * self.K_PUMP_CIRC_TRANSFER * dt

        # Temperatura powrotu – dążenie w stronę mieszanki zasilania i instalacji
        mix_target = (s.radiators_temp + s.boiler_temp) / 2.0
        s.return_temp += (mix_target - s.return_temp) * self.K_RETURN_MIX * dt

        # 5) Naturalne straty ciepła do otoczenia (gdy pomp brak – kocioł szybciej się gotuje,
        #    bo jedyny „wentyl” to powolne straty do otoczenia).
        ambient = (self.AMBIENT_TEMP + s.outside_temp) / 2.0
        s.boiler_temp += (ambient - s.boiler_temp) * self.K_BOILER_LOSS * dt
        s.flue_gas_temp += (ambient - s.flue_gas_temp) * self.K_FLUE_LOSS * dt

        # Zasobnik (hopper) lekko dogrzewa się od kotła
        s.hopper_temp += (s.boiler_temp - s.hopper_temp) * 0.005 * dt

        # 6) Bezpieczeństwo – STB
        if s.boiler_temp >= self.STB_LIMIT:
            s.stb_triggered = True

        # 7) Ograniczenia / sanity check
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
