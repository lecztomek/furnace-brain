from __future__ import annotations

import time
from dataclasses import dataclass
import logging

from backend.hw.interface import HardwareInterface
from backend.core.state import Sensors, Outputs


__all__ = ["MockHardware"]

# Podzielone loggery – możesz je osobno włączać/wyłączać
logger_core = logging.getLogger(__name__ + ".core")
logger_mixer = logging.getLogger(__name__ + ".mixer")
logger_feeder = logging.getLogger(__name__ + ".feeder")
logger_fan = logging.getLogger(__name__ + ".fan")
logger_pumps = logging.getLogger(__name__ + ".pumps")
logger_energy = logging.getLogger(__name__ + ".energy")
logger_flue = logging.getLogger(__name__ + ".flue")


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
    flue_gas_temp: float = 30.0      # °C – temp. spalin (w dymnicy, przy kotle)
    hopper_temp: float = 25.0        # °C – zasobnik / podajnik
    outside_temp: float = 5.0        # °C – na zewnątrz

    # PALIWO:
    fuel_buffer: float = 0.0         # kg świeżego paliwa na palniku (nie-aktywne)
    active_fuel: float = 0.0         # kg „żaru” zdolnego do spalania

    stb_triggered: bool = False
    door_open: bool = False


class MockHardware(HardwareInterface):
    """
    Symulator warstwy sprzętowej z dwustanowym paliwem, zaworem mieszającym
    i wpływem dmuchawy na spaliny:

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
    - Zawór mieszający CO:
        mix_valve_pos ∈ [0..1], pełny przebieg w 120 s.
        Tylko mix_valve_pos * przepływu pompy CO przechodzi przez kocioł.
    - CWU:
        wężownica modelowana jako wymiennik U * ΔT (bez mieszania wód).
    - Energia:
        Q_chem = m_spalone * 29 MJ/kg → część do wody w kotle, część do spalin,
        straty do kotłowni / domu / komina.

    Temperaturę spalin interpretujemy jako czujnik przy WYLOCIE kotła (dymnica),
    nie na końcu komina.
    """

    # --- Stałe fizyczne / parametry kotła ---

    C_WATER = 4180.0  # [J/(kg*K)]

    M_BOILER_WATER = 50.0    # [kg] ~ 50 l w kotle
    M_CO_WATER = 150.0       # [kg] ~ 150 l w instalacji CO
    M_CWU_WATER = 120.0      # [kg] – przyjęty zasobnik 120 l

    # Pojemność cieplna „węzła spalin” przy czujniku (dymnica, kawałek rury, powietrze)
    C_FLUE = 2500.0          # [J/K] – mniejsza pojemność, szybsza reakcja

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
    FRACTION_TO_WATER = 0.75      # z użytecznej energii tyle w wodzie, reszta w spalinach

    # --- 2-etapowy model paliwa ---

    FUEL_DRYING_TIME_S = 60.0     # szybsze przejście świeżego paliwa w „żar”
    NATURAL_BURN_TIME_S = 600.0   # naturalne dogorywanie

    # --- Straty ciepła ---

    U_BOILER_LOSS_W_PER_K = 50.0   # kocioł -> kotłownia

    # CO – rozróżniamy sytuację z pompą i bez:
    U_CO_LOSS_ACTIVE_W_PER_K = 400.0  # grzejniki -> dom przy pracującej pompie
    U_CO_LOSS_IDLE_W_PER_K = 40.0     # minimalne straty przy wyłączonej pompie

    U_CWU_LOSS_W_PER_K = 20.0      # zasobnik -> kotłownia

    # Wymiennik CWU (wężownica): kocioł <-> zasobnik CWU
    U_CWU_EXCH_W_PER_K = 800.0

    # Spaliny – straty z okolic czujnika (dymnica, początek komina)
    U_FLUE_LOSS_W_PER_K = 25.0

    HOPPER_COUPLING_W_PER_K = 5.0  # kocioł -> zasobnik

    # --- Pompy ---

    # 1320 l/h ≈ 0.3667 kg/s
    PUMP_FLOW_KG_PER_S = 1320.0 / 3600.0

    # --- Zawór mieszający CO ---

    MIX_VALVE_FULL_TRAVEL_S = 120.0  # czas pełnego przebiegu 0→1 lub 1→0

    # --- STB ---

    STB_LIMIT = 95.0  # [°C]

    def __init__(self) -> None:
        self._state = _ThermalState()
        self._outputs = Outputs()
        self._last_update = time.monotonic()

        self._C_boiler = self.M_BOILER_WATER * self.C_WATER
        self._C_co = self.M_CO_WATER * self.C_WATER
        self._C_cwu = self.M_CWU_WATER * self.C_WATER

        # Zawór mieszający startuje zamknięty
        self._mix_valve_pos: float = 0.0  # 0.0 = zamknięty, 1.0 = otwarty

        # Czas symulacji (narastająco, w sekundach)
        self._sim_time: float = 0.0

        # Stany ON/OFF + czas od kiedy ON (dla logowania)
        self._feeder_prev_on: bool = False
        self._feeder_on_since: float | None = None

        self._fan_prev_on: bool = False
        self._fan_on_since: float | None = None

        self._mixer_prev_on: bool = False
        self._mixer_on_since: float | None = None

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

        # aktualizujemy czas symulacji
        self._sim_time += dt
        t = self._sim_time

        # --- ZMIANY STANÓW ON/OFF (feeder, fan, mixer) --------------------
        # Feeder
        if o.feeder_on and not self._feeder_prev_on:
            self._feeder_on_since = t
            logger_feeder.info("Feeder ON at t=%.2fs", t)
        elif not o.feeder_on and self._feeder_prev_on:
            duration = t - (self._feeder_on_since or t)
            logger_feeder.info("Feeder OFF at t=%.2fs (ON for %.2fs)", t, duration)
            self._feeder_on_since = None
        self._feeder_prev_on = o.feeder_on

        # Fan (blower) – traktujemy >0% jako ON
        fan_on = o.fan_power > 0
        if fan_on and not self._fan_prev_on:
            self._fan_on_since = t
            logger_fan.info("Fan ON at t=%.2fs (power=%d%%)", t, o.fan_power)
        elif not fan_on and self._fan_prev_on:
            duration = t - (self._fan_on_since or t)
            logger_fan.info("Fan OFF at t=%.2fs (ON for %.2fs)", t, duration)
            self._fan_on_since = None
        self._fan_prev_on = fan_on

        # Mixer – traktujemy jakikolwiek ruch jako ACTIVE
        mixer_on = o.mixer_open_on or o.mixer_close_on
        if mixer_on and not self._mixer_prev_on:
            self._mixer_on_since = t
            logger_mixer.info(
                "Mixer ACTIVE at t=%.2fs (open_on=%s, close_on=%s, pos=%.3f)",
                t,
                o.mixer_open_on,
                o.mixer_close_on,
                self._mix_valve_pos,
            )
        elif not mixer_on and self._mixer_prev_on:
            duration = t - (self._mixer_on_since or t)
            logger_mixer.info("Mixer STOP at t=%.2fs (ACTIVE for %.2fs)", t, duration)
            self._mixer_on_since = None
        self._mixer_prev_on = mixer_on

        logger_core.debug(
            "STEP t=%.2fs dt=%.3f | T_kotla=%.1f T_CO=%.1f T_CWU=%.1f T_spalin=%.1f "
            "fuel_buffer=%.4f active_fuel=%.4f mix_pos=%.3f",
            t,
            dt,
            s.boiler_temp,
            s.radiators_temp,
            s.cwu_temp,
            s.flue_gas_temp,
            s.fuel_buffer,
            s.active_fuel,
            self._mix_valve_pos,
        )

        # 0) Zawór mieszający – aktualizacja pozycji
        valve_speed = dt / self.MIX_VALVE_FULL_TRAVEL_S  # ile ułamka w tym kroku
        old_valve_pos = self._mix_valve_pos

        if o.mixer_open_on and not o.mixer_close_on:
            # otwieranie – do 1.0
            self._mix_valve_pos += valve_speed
        elif o.mixer_close_on and not o.mixer_open_on:
            # zamykanie – do 0.0
            self._mix_valve_pos -= valve_speed

        # clamp 0..1
        if self._mix_valve_pos < 0.0:
            self._mix_valve_pos = 0.0
        elif self._mix_valve_pos > 1.0:
            self._mix_valve_pos = 1.0

        if self._mix_valve_pos != old_valve_pos:
            logger_mixer.debug(
                "Mixer pos: %.3f -> %.3f (valve_speed=%.4f, open_on=%s, close_on=%s, active_for=%.2fs)",
                old_valve_pos,
                self._mix_valve_pos,
                valve_speed,
                o.mixer_open_on,
                o.mixer_close_on,
                0.0 if self._mixer_on_since is None else t - self._mixer_on_since,
            )

        # 1) ŚLIMAK – dosypywanie świeżego paliwa
        if o.feeder_on:
            added = self.FUEL_FEED_RATE * dt
            s.fuel_buffer += added
            logger_feeder.debug(
                "Feeder step: added=%.5f kg, fuel_buffer=%.5f (on_for=%.2fs)",
                added,
                s.fuel_buffer,
                0.0 if self._feeder_on_since is None else t - self._feeder_on_since,
            )

        # 2) fuel_buffer -> active_fuel (suszenie/nagrzewanie)
        if s.fuel_buffer > 0.0:
            frac = dt / self.FUEL_DRYING_TIME_S
            if frac > 1.0:
                frac = 1.0
            m_to_active = s.fuel_buffer * frac
            s.fuel_buffer -= m_to_active
            s.active_fuel += m_to_active
            logger_feeder.debug(
                "Drying: moved=%.5f kg -> active, fuel_buffer=%.5f, active_fuel=%.5f",
                m_to_active,
                s.fuel_buffer,
                s.active_fuel,
            )

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

            if burn_mass > 0.0:
                logger_fan.debug(
                    "Burning: burn_mass=%.5f kg, fan=%.1f%%, air_flow=%.5f m3/s, "
                    "burn_rate=%.6f kg/s, active_fuel=%.5f (fan_on_for=%.2fs)",
                    burn_mass,
                    fan_fraction * 100.0,
                    air_flow_m3_s,
                    burn_rate,
                    s.active_fuel,
                    0.0 if self._fan_on_since is None else t - self._fan_on_since,
                )

        # 3a) Niedopalony węgiel wypychany do popielnika przy małym nadmuchu
        if o.feeder_on and s.active_fuel > 0.0:
            loss_factor = max(0.0, 1.0 - fan_fraction)
            loss_rate = loss_factor * self.FUEL_FEED_RATE       # [kg/s]
            lost_mass = min(s.active_fuel, loss_rate * dt)
            s.active_fuel -= lost_mass
            if lost_mass > 0.0:
                logger_feeder.debug(
                    "Unburnt loss: lost_mass=%.5f kg, loss_factor=%.3f, loss_rate=%.6f kg/s, active_fuel=%.5f",
                    lost_mass,
                    loss_factor,
                    loss_rate,
                    s.active_fuel,
                )

        # 4) Energia ze spalania
        Q_chem = burn_mass * self.FUEL_LHV           # [J]
        Q_useful = Q_chem * self.BOILER_EFFICIENCY   # [J]

        if burn_mass > 0.0:
            logger_energy.debug(
                "Combustion energy: burn_mass=%.5f kg, Q_chem=%.0f J, Q_useful=%.0f J",
                burn_mass,
                Q_chem,
                Q_useful,
            )

        Q_to_water = Q_useful * self.FRACTION_TO_WATER
        Q_to_flue = Q_useful * (1.0 - self.FRACTION_TO_WATER)

        if Q_to_water > 0.0:
            dT_boiler = Q_to_water / self._C_boiler
            s.boiler_temp += dT_boiler
            logger_energy.debug(
                "Boiler heating from combustion: Q_to_water=%.0f J, dT_boiler=%.3f, T_kotla=%.2f",
                Q_to_water,
                dT_boiler,
                s.boiler_temp,
            )

        # 5) Pompa CO – transfer kocioł <-> obieg CO + straty z grzejników
        if o.pump_co_on:
            # Tylko część przepływu przechodzi przez kocioł (reszta bypass)
            valve_open = self._mix_valve_pos  # 0..1
            flow_boiler = self.PUMP_FLOW_KG_PER_S * valve_open

            if flow_boiler > 0.0:
                deltaT = s.boiler_temp - s.radiators_temp
                Q_co = flow_boiler * self.C_WATER * deltaT * dt
                s.boiler_temp -= Q_co / self._C_boiler
                s.radiators_temp += Q_co / self._C_co

                logger_pumps.debug(
                    "CO pump ON: valve_open=%.3f, flow_boiler=%.5f kg/s, "
                    "deltaT=%.3f, Q_co=%.0f J, T_kotla=%.2f, T_CO=%.2f",
                    valve_open,
                    flow_boiler,
                    deltaT,
                    Q_co,
                    s.boiler_temp,
                    s.radiators_temp,
                )

            # Straty z obiegu CO do domu (gorące grzejniki)
            sink_temp_co = (self.ROOM_TEMP + s.outside_temp) / 2.0
            Q_loss_co = self.U_CO_LOSS_ACTIVE_W_PER_K * (s.radiators_temp - sink_temp_co) * dt
            s.radiators_temp -= Q_loss_co / self._C_co

            logger_energy.debug(
                "CO losses (active): sink_temp_co=%.2f, Q_loss_co=%.0f J, T_CO=%.2f",
                sink_temp_co,
                Q_loss_co,
                s.radiators_temp,
            )
        else:
            # Pompa CO wyłączona: minimalne straty, ciepłe rury przy kotle
            sink_temp_co = self.ROOM_TEMP
            Q_loss_co = self.U_CO_LOSS_IDLE_W_PER_K * (s.radiators_temp - sink_temp_co) * dt
            s.radiators_temp -= Q_loss_co / self._C_co

            logger_energy.debug(
                "CO losses (idle): sink_temp_co=%.2f, Q_loss_co=%.0f J, T_CO=%.2f",
                sink_temp_co,
                Q_loss_co,
                s.radiators_temp,
            )

        # 6) Pompa CWU – wymiennik kocioł <-> zasobnik CWU (bez mieszania wód)
        if o.pump_cwu_on:
            deltaT_cwu = s.boiler_temp - s.cwu_temp
            Q_cwu = self.U_CWU_EXCH_W_PER_K * deltaT_cwu * dt
            s.boiler_temp -= Q_cwu / self._C_boiler
            s.cwu_temp += Q_cwu / self._C_cwu

            logger_pumps.debug(
                "CWU pump ON: deltaT_cwu=%.3f, Q_cwu=%.0f J, T_kotla=%.2f, T_CWU=%.2f",
                deltaT_cwu,
                Q_cwu,
                s.boiler_temp,
                s.cwu_temp,
            )

        # Straty CWU do kotłowni (zawsze)
        Q_loss_cwu = self.U_CWU_LOSS_W_PER_K * (s.cwu_temp - self.ROOM_TEMP) * dt
        s.cwu_temp -= Q_loss_cwu / self._C_cwu

        logger_energy.debug(
            "CWU losses: Q_loss_cwu=%.0f J, T_CWU=%.2f",
            Q_loss_cwu,
            s.cwu_temp,
        )

        # 7) Cyrkulacja CWU – lekkie dociąganie do ROOM_TEMP
        if o.pump_circ_on:
            Q_circ = self.U_CWU_LOSS_W_PER_K * (self.ROOM_TEMP - s.cwu_temp) * dt * 0.5
            s.cwu_temp += Q_circ / self._C_cwu

            logger_pumps.debug(
                "CWU circulation ON: Q_circ=%.0f J, T_CWU=%.2f",
                Q_circ,
                s.cwu_temp,
            )

        # 8) Straty kotła do kotłowni
        Q_loss_boiler = self.U_BOILER_LOSS_W_PER_K * (s.boiler_temp - self.ROOM_TEMP) * dt
        s.boiler_temp -= Q_loss_boiler / self._C_boiler

        logger_energy.debug(
            "Boiler losses: Q_loss_boiler=%.0f J, T_kotla=%.2f",
            Q_loss_boiler,
            s.boiler_temp,
        )

        # 9) Spaliny – podgrzewanie i chłodzenie (czujnik w dymnicy)
        if Q_to_flue > 0.0:
            # Czujnik w dymnicy „widzi” większość energii spalin.
            # Przy małym nadmuchu nadal ~70%, przy 100% → ~100%.
            flue_coupling = 0.7 + 0.3 * fan_fraction  # 70% przy 0%, 100% przy 100%
            dT_flue = (Q_to_flue * flue_coupling) / self.C_FLUE
            s.flue_gas_temp += dT_flue

            logger_flue.debug(
                "Flue heating: Q_to_flue=%.0f J, flue_coupling=%.3f, dT_flue=%.3f, T_spalin=%.2f",
                Q_to_flue,
                flue_coupling,
                dT_flue,
                s.flue_gas_temp,
            )

        # Chłodzenie spalin – odniesienie raczej do kotłowni z lekkim wpływem zewnątrz
        sink_temp_flue = self.ROOM_TEMP + 0.3 * (s.outside_temp - self.ROOM_TEMP)
        Q_loss_flue = self.U_FLUE_LOSS_W_PER_K * (s.flue_gas_temp - sink_temp_flue) * dt
        s.flue_gas_temp -= Q_loss_flue / self.C_FLUE

        logger_flue.debug(
            "Flue losses: sink_temp_flue=%.2f, Q_loss_flue=%.0f J, T_spalin=%.2f",
            sink_temp_flue,
            Q_loss_flue,
            s.flue_gas_temp,
        )

        # Przy trwającym spalaniu czujnik spalin nie powinien być zimniejszy niż woda w kotle
        # i zwykle jest wyżej o kilkanaście–kilkadziesiąt °C (zależnie od nadmuchu).
        if burn_mass > 0.0:
            min_flue = s.boiler_temp + 10.0 + 40.0 * fan_fraction
            if s.flue_gas_temp < min_flue:
                logger_flue.debug(
                    "Flue min clamp during burn: T_spalin=%.2f -> %.2f (fan=%.1f%%)",
                    s.flue_gas_temp,
                    min_flue,
                    fan_fraction * 100.0,
                )
                s.flue_gas_temp = min_flue

        # 10) Powrót – dociąganie do średniej (boiler + CO)
        mix_target = 0.5 * (s.boiler_temp + s.radiators_temp)
        alpha = 0.2 * dt * (0.3 + 0.7 * self._mix_valve_pos)
        if alpha > 1.0:
            alpha = 1.0
        old_return = s.return_temp
        s.return_temp = (1.0 - alpha) * s.return_temp + alpha * mix_target

        logger_core.debug(
            "Return mix: old=%.2f, new=%.2f, mix_target=%.2f, alpha=%.3f",
            old_return,
            s.return_temp,
            mix_target,
            alpha,
        )

        # 11) Hopper – lekko dogrzewa się od kotła
        Q_hopper = self.HOPPER_COUPLING_W_PER_K * (s.boiler_temp - s.hopper_temp) * dt
        C_hopper = 2000.0  # [J/K] – umowne
        dT_hopper = Q_hopper / C_hopper
        s.hopper_temp += dT_hopper

        logger_energy.debug(
            "Hopper heating: Q_hopper=%.0f J, dT_hopper=%.3f, T_hopper=%.2f",
            Q_hopper,
            dT_hopper,
            s.hopper_temp,
        )

        # 12) STB
        if s.boiler_temp >= self.STB_LIMIT and not s.stb_triggered:
            logger_core.warning(
                "STB TRIGGERED at t=%.2fs! T_kotla=%.1f °C (limit=%.1f °C)",
                t,
                s.boiler_temp,
                self.STB_LIMIT,
            )
            s.stb_triggered = True

        # 13) Ograniczenia
        self._clamp_temps()

        logger_core.debug(
            "STEP END t=%.2fs | T_kotla=%.1f T_powrotu=%.1f T_CO=%.1f T_CWU=%.1f "
            "T_spalin=%.1f T_hopper=%.1f fuel_buffer=%.4f active_fuel=%.4f mix_pos=%.3f stb=%s",
            t,
            s.boiler_temp,
            s.return_temp,
            s.radiators_temp,
            s.cwu_temp,
            s.flue_gas_temp,
            s.hopper_temp,
            s.fuel_buffer,
            s.active_fuel,
            self._mix_valve_pos,
            s.stb_triggered,
        )

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
