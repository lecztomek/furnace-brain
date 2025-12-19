import pytest

from backend.core.state import SystemState, Sensors, Outputs, BoilerMode, PartialOutputs

# POPRAW, jeśli masz inną ścieżkę:
from backend.modules.mixer import MixerModule


DEFAULT_CFG = dict(
    target_temp=40.0,
    ok_band_degC=2.0,
    min_pulse_s=0.5,
    max_pulse_s=3.0,
    adjust_interval_s=5.0,
    ramp_error_factor=2.0,
    boiler_min_temp_for_open=55.0,
    boiler_max_drop_degC=5.0,
    boiler_recover_factor=0.5,
    preclose_on_ignition_enabled=True,
    preclose_full_close_time_s=10.0,  # krócej = szybciej
)


def cfg_mixer(m: MixerModule, **overrides):
    cfg = dict(DEFAULT_CFG)
    cfg.update(overrides)
    m.set_config_values(cfg, persist=False)
    return cfg


def apply_partial_outputs(st: SystemState, po: PartialOutputs | None):
    if po is None:
        return
    for name in ("mixer_open_on", "mixer_close_on"):
        v = getattr(po, name, None)
        if v is not None:
            setattr(st.outputs, name, v)


def tick(m: MixerModule, st: SystemState, now: float):
    st.ts = now
    res = m.tick(now=now, sensors=st.sensors, system_state=st)
    apply_partial_outputs(st, getattr(res, "partial_outputs", None))
    open_on = bool(st.outputs.mixer_open_on)
    close_on = bool(st.outputs.mixer_close_on)
    events = list(res.events or [])
    types = [e.type for e in events]
    return open_on, close_on, events, types


def first_event(events, type_: str):
    for e in events:
        if e.type == type_:
            return e
    return None


def plant_step(rad: float, boiler: float, open_on: bool, close_on: bool, dt: float) -> float:
    if open_on and close_on:
        return rad

    heat_gain = 0.10
    cool_loss = 0.04
    idle_loss = 0.002

    if open_on:
        err = max(0.0, boiler - rad)
        rad += heat_gain * err * dt
    elif close_on:
        cold = 20.0
        err = max(0.0, rad - cold)
        rad -= cool_loss * err * dt
    else:
        rad -= idle_loss * dt

    rad = max(0.0, min(rad, boiler))
    return rad


def simulate(m: MixerModule, st: SystemState, *, duration_s: float, dt: float):
    """
    Zwraca listę kroków *włącznie z tickiem t=0.0*,
    żeby wykrywanie START nie zgubiło eventu z pierwszego ticka.
    """
    hist = []

    now = 0.0
    open_on, close_on, ev, types = tick(m, st, now)
    hist.append(dict(t=now, rad=float(st.sensors.radiators_temp or 0.0),
                     open=open_on, close=close_on, events=ev, types=types))

    steps = int(duration_s / dt)
    for _ in range(steps):
        now += dt

        st.sensors.radiators_temp = plant_step(
            float(st.sensors.radiators_temp),
            float(st.sensors.boiler_temp),
            open_on,
            close_on,
            dt,
        )

        open_on, close_on, ev, types = tick(m, st, now)
        hist.append(dict(t=now, rad=float(st.sensors.radiators_temp),
                         open=open_on, close=close_on, events=ev, types=types))

    return hist


def assert_never_open_and_close(hist):
    bad = [h for h in hist if h["open"] and h["close"]]
    assert not bad, f"Znaleziono jednoczesne open+close, np: {bad[:3]}"


@pytest.fixture
def mixer_module():
    return MixerModule()


@pytest.fixture
def state():
    return SystemState(ts=0.0, sensors=Sensors(), outputs=Outputs(), runtime={}, modules={})


# =============================================================================
# Telemetria: START
# =============================================================================

def test_mixer_emits_move_start_on_relay_start(mixer_module, state):
    cfg = cfg_mixer(mixer_module)

    state.mode = BoilerMode.WORK
    state.sensors.boiler_temp = 60.0
    state.sensors.radiators_temp = 10.0  # far -> ramp -> będzie OPEN

    hist = simulate(mixer_module, state, duration_s=5 * 60.0, dt=1.0)
    assert_never_open_and_close(hist)

    prev = hist[0]
    starts = 0

    for h in hist[1:]:
        started_open = (not prev["open"]) and h["open"]
        started_close = (not prev["close"]) and h["close"]

        if started_open or started_close:
            starts += 1
            e = first_event(h["events"], "MIXER_MOVE_START")
            assert e is not None, f"[t={h['t']}] START przekaźnika bez MIXER_MOVE_START, events={h['types']}"

            if started_open:
                assert e.data.get("direction") == "open"
            if started_close:
                assert e.data.get("direction") == "close"

        prev = h

    assert starts > 0, "Nie wykryto żadnego startu przekaźnika"


# =============================================================================
# Telemetria: STOP + czas
# =============================================================================

def test_mixer_emits_move_stop_on_relay_stop_and_reports_duration(mixer_module, state):
    cfg_mixer(mixer_module, min_pulse_s=1.0, max_pulse_s=2.0, adjust_interval_s=3.0)

    state.mode = BoilerMode.WORK
    state.sensors.boiler_temp = 60.0
    state.sensors.radiators_temp = 10.0

    dt = 0.5
    hist = simulate(mixer_module, state, duration_s=4 * 60.0, dt=dt)
    assert_never_open_and_close(hist)

    starts = [(h["t"], first_event(h["events"], "MIXER_MOVE_START")) for h in hist if "MIXER_MOVE_START" in h["types"]]
    stops = [(h["t"], first_event(h["events"], "MIXER_MOVE_STOP")) for h in hist if "MIXER_MOVE_STOP" in h["types"]]

    assert starts, "Brak MIXER_MOVE_START"
    assert stops, "Brak MIXER_MOVE_STOP"

    t_start, e_start = starts[0]
    after = [(t, e) for (t, e) in stops if t > t_start]
    assert after, "Nie znaleziono STOP po pierwszym START"
    t_stop, e_stop = after[0]

    assert e_stop.data.get("direction") == e_start.data.get("direction")

    planned = e_start.data.get("planned_pulse_s")
    actual = e_stop.data.get("actual_run_s")

    if planned is not None and actual is not None:
        assert actual == pytest.approx(planned, abs=dt + 0.2)


# =============================================================================
# Preclose na IGNITION
# =============================================================================

def test_mixer_emits_preclose_on_ignition_event_when_enabled(mixer_module, state):
    cfg_mixer(mixer_module, preclose_on_ignition_enabled=True, preclose_full_close_time_s=10.0)

    state.sensors.boiler_temp = 60.0
    state.sensors.radiators_temp = 10.0  # far

    state.mode = BoilerMode.WORK
    tick(mixer_module, state, now=0.0)

    state.mode = BoilerMode.IGNITION
    open_on, close_on, ev, types = tick(mixer_module, state, now=1.0)

    assert "MIXER_PRECLOSE_ON_IGNITION" in types, f"events={types}"
    assert close_on is True


# =============================================================================
# Mode changed (w dłuższej symulacji powinno wystąpić)
# =============================================================================

def test_mixer_emits_mode_changed_event_at_least_once(mixer_module, state):
    cfg_mixer(mixer_module)

    state.mode = BoilerMode.WORK
    state.sensors.boiler_temp = 60.0
    state.sensors.radiators_temp = 10.0

    hist = simulate(mixer_module, state, duration_s=20 * 60.0, dt=1.0)
    assert_never_open_and_close(hist)

    assert any("MIXER_MODE_CHANGED" in h["types"] for h in hist), "Brak MIXER_MODE_CHANGED"


# =============================================================================
# Opcjonalnie: jeśli chcesz wymagać, że na START jest też MIXER_MOVE (decyzja)
# =============================================================================

def test_move_start_tick_contains_decision_event_mixer_move(mixer_module, state):
    cfg_mixer(mixer_module)

    state.mode = BoilerMode.WORK
    state.sensors.boiler_temp = 60.0
    state.sensors.radiators_temp = 10.0

    hist = simulate(mixer_module, state, duration_s=5 * 60.0, dt=1.0)

    prev = hist[0]
    for h in hist[1:]:
        started = ((not prev["open"]) and h["open"]) or ((not prev["close"]) and h["close"])
        if started:
            assert "MIXER_MOVE_START" in h["types"]
            assert "MIXER_MOVE" in h["types"], f"[t={h['t']}] brak MIXER_MOVE na starcie, events={h['types']}"
            return
        prev = h

    pytest.fail("Nie znaleziono żadnego startu ruchu w symulacji")


# =============================================================================
# Bezpieczeństwo: nigdy open i close jednocześnie
# =============================================================================

def test_mixer_never_sets_open_and_close_together(mixer_module, state):
    cfg_mixer(mixer_module)

    state.mode = BoilerMode.WORK
    state.sensors.boiler_temp = 60.0
    state.sensors.radiators_temp = 30.0

    hist = simulate(mixer_module, state, duration_s=10 * 60.0, dt=1.0)
    assert_never_open_and_close(hist)

def test_preclose_emits_start_and_stop_with_duration(mixer_module, state):
    """
    Preclose powinno mieć telemetrię:
    - START CLOSE (MIXER_MOVE_START)
    - STOP  CLOSE (MIXER_MOVE_STOP)
    oraz czas zbliżony do preclose_full_close_time_s.
    """
    preclose_s = 6.0
    cfg_mixer(
        mixer_module,
        preclose_on_ignition_enabled=True,
        preclose_full_close_time_s=preclose_s,
        adjust_interval_s=5.0,
        target_temp=40.0,
        ok_band_degC=2.0,
        ramp_error_factor=2.0,
    )

    state.sensors.boiler_temp = 60.0
    state.sensors.radiators_temp = 10.0  # far -> preclose warunek spełniony

    # 1) tick w WORK, żeby wejście w IGNITION było wykryte
    state.mode = BoilerMode.WORK
    tick(mixer_module, state, now=0.0)

    # 2) wejście w IGNITION -> start preclose
    state.mode = BoilerMode.IGNITION
    open_on, close_on, ev, types = tick(mixer_module, state, now=1.0)

    assert "MIXER_PRECLOSE_ON_IGNITION" in types, f"events={types}"
    assert close_on is True

    # tu powinien być też START przekaźnika CLOSE
    e_start = first_event(ev, "MIXER_MOVE_START")
    assert e_start is not None, f"Brak MIXER_MOVE_START na starcie preclose, events={types}"
    assert e_start.data.get("direction") == "close"

    planned = e_start.data.get("planned_pulse_s")
    if planned is not None:
        assert planned == pytest.approx(preclose_s, abs=0.5)  # z grubsza

    # 3) dobijamy czas do końca impulsu + 1 tick, żeby zobaczyć STOP (zbocze True->False)
    dt = 0.5
    now = 1.0
    found_stop = None
    for _ in range(int((preclose_s + 2.0) / dt)):  # +2s zapasu
        now += dt
        open_on, close_on, ev, types = tick(mixer_module, state, now=now)
        e_stop = first_event(ev, "MIXER_MOVE_STOP")
        if e_stop is not None:
            found_stop = (now, e_stop)
            break

    assert found_stop is not None, "Nie znaleziono MIXER_MOVE_STOP dla preclose"
    _, e_stop = found_stop
    assert e_stop.data.get("direction") == "close"

    actual = e_stop.data.get("actual_run_s")
    if actual is not None:
        assert actual == pytest.approx(preclose_s, abs=dt + 0.3)
