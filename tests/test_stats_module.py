import pytest
from backend.core.state import SystemState, Sensors, Outputs

# Dostosuj ścieżkę jeśli potrzebujesz
from backend.modules.stats import StatsModule


WINDOWS = {
    "5m": 300.0,
    "1h": 3600.0,
    "4h": 14400.0,
    "24h": 86400.0,
    "7d": 604800.0,
}


# =============================================================================
# Core helpers
# =============================================================================

def cfg_stats(module, feeder_kgph=12.0, calorific=29.0):
    """Ustaw config modułu w testach bez zapisu na dysk."""
    module.set_config_values(
        {"feeder_kg_per_hour": feeder_kgph, "calorific_mj_per_kg": calorific},
        persist=False,
    )
    return feeder_kgph, calorific

def assert_min_avg_max(mn: float, av: float, mx: float, eps: float = 1e-9, msg: str = ""):
    assert mn <= av + eps, msg or f"min>avg: {mn} > {av}"
    assert av <= mx + eps, msg or f"avg>max: {av} > {mx}"

	
def make_state():
    return SystemState(ts=0.0, sensors=Sensors(), outputs=Outputs(), runtime={})


def get_stats(state: SystemState, module_id: str = "stats") -> dict:
    assert isinstance(state.runtime, dict), "SystemState.runtime musi być dict"
    assert module_id in state.runtime, f"Brak state.runtime['{module_id}']"
    d = state.runtime[module_id]
    assert isinstance(d, dict), "state.runtime['stats'] musi być dict"
    return d


def expected_from_on_seconds(window_s: float, on_s: float, feeder_kgph: float, calorific_mj_per_kg: float):
    coal_kg = feeder_kgph * (on_s / 3600.0)
    burn_kgph = (coal_kg * 3600.0 / window_s) if window_s > 0 else 0.0
    power_kw = burn_kgph * calorific_mj_per_kg / 3.6
    energy_kwh = power_kw * (window_s / 3600.0)
    return coal_kg, burn_kgph, power_kw, energy_kwh


def implied_on_seconds_from_module(stats: dict, suffix: str, feeder_kgph: float) -> float:
    """Ile sekund ON implikuje wynik coal_kg."""
    coal = float(stats[f"coal_kg_{suffix}"])
    return coal * 3600.0 / feeder_kgph if feeder_kgph > 0 else 0.0


def on_seconds_in_last_window(segments: list[tuple[float, bool]], window_s: float) -> float:
    """Ile sekund ON w ostatnim window_s czasu (rolling window)."""
    remaining = window_s
    on_s = 0.0
    for dur, on in reversed(segments):
        if remaining <= 0:
            break
        take = dur if dur <= remaining else remaining
        if on:
            on_s += take
        remaining -= take
    return on_s


def run_segments_constant_dt(
    module,
    state: SystemState,
    sensors: Sensors,
    start: float,
    dt: float,
    segments: list[tuple[float, bool]],
):
    """
    Symuluje pracę w segmentach (duration_s, feeder_on) przy stałym dt.
    Ustawiamy feeder_on, zwiększamy czas o dt i dopiero wtedy tick().
    """
    now = start
    state.ts = now
    module.tick(now=now, sensors=sensors, system_state=state)  # init

    for duration_s, on in segments:
        steps = int(round(duration_s / dt))
        if abs(steps * dt - duration_s) > 1e-9:
            raise ValueError(f"Segment {duration_s}s nie dzieli się przez dt={dt}s")

        for _ in range(steps):
            state.outputs.feeder_on = bool(on)
            now += dt
            state.ts = now
            module.tick(now=now, sensors=sensors, system_state=state)


def run_segments_jittered_dt(
    module,
    state: SystemState,
    sensors: Sensors,
    start: float,
    dt_seq: list[float],
    segments: list[tuple[float, bool]],
):
    """Symulacja z nieregularnym dt (jitter)."""
    now = start
    state.ts = now
    module.tick(now=now, sensors=sensors, system_state=state)  # init

    i = 0
    for duration_s, on in segments:
        remaining = duration_s
        while remaining > 1e-12:
            dt = dt_seq[i % len(dt_seq)]
            i += 1
            if dt > remaining:
                dt = remaining
            state.outputs.feeder_on = bool(on)
            now += dt
            state.ts = now
            module.tick(now=now, sensors=sensors, system_state=state)
            remaining -= dt


def assert_window_values(stats: dict, suffix: str, expected_on_s: float, feeder_kgph: float, calorific: float):
    """Twarde sprawdzenie coal/burn/power/energy/seconds dla danego okna."""
    window_s = WINDOWS[suffix]
    exp_coal, exp_burn, exp_power, exp_energy = expected_from_on_seconds(window_s, expected_on_s, feeder_kgph, calorific)

    got_coal = float(stats[f"coal_kg_{suffix}"])
    got_on_s = implied_on_seconds_from_module(stats, suffix, feeder_kgph)
    msg = (
        f"[{suffix}] expected_on_s={expected_on_s:.1f}s, got_on_s≈{got_on_s:.1f}s | "
        f"expected_coal={exp_coal:.3f}kg, got_coal={got_coal:.3f}kg"
    )

    assert float(stats[f"seconds_{suffix}"]) == pytest.approx(window_s, abs=1e-9), msg
    assert float(stats[f"coal_kg_{suffix}"]) == pytest.approx(exp_coal, rel=1e-4), msg
    assert float(stats[f"burn_kgph_{suffix}"]) == pytest.approx(exp_burn, rel=1e-4), msg
    assert float(stats[f"power_kw_{suffix}"]) == pytest.approx(exp_power, rel=1e-4), msg
    assert float(stats[f"energy_kwh_{suffix}"]) == pytest.approx(exp_energy, rel=1e-4), msg

    # min/avg/max: nie zgadujemy definicji, ale wymagamy spójności i sensownych granic
    mn = float(stats[f"burn_kgph_min_{suffix}"])
    av = float(stats[f"burn_kgph_{suffix}"])
    mx = float(stats[f"burn_kgph_max_{suffix}"])
    assert_min_avg_max(mn, av, mx, msg=f"{suffix}: burn min/avg/max")
    assert 0.0 <= mn <= feeder_kgph, f"{suffix}: burn_kgph_min poza zakresem"
    assert 0.0 <= mx <= feeder_kgph, f"{suffix}: burn_kgph_max poza zakresem"


def assert_internal_consistency(stats: dict, suffix: str, calorific: float):
    """Sprawdza tożsamości pomiędzy polami (niezależnie od wzorca ON/OFF)."""
    seconds = float(stats[f"seconds_{suffix}"])
    coal = float(stats[f"coal_kg_{suffix}"])
    burn = float(stats[f"burn_kgph_{suffix}"])
    power = float(stats[f"power_kw_{suffix}"])
    energy = float(stats[f"energy_kwh_{suffix}"])

    if seconds > 0:
        assert burn == pytest.approx(coal * 3600.0 / seconds, rel=1e-6)
        assert coal == pytest.approx(burn * seconds / 3600.0, rel=1e-6)

    assert power == pytest.approx(burn * calorific / 3.6, rel=1e-6)
    assert energy == pytest.approx(power * seconds / 3600.0, rel=1e-6)
    assert energy == pytest.approx(coal * calorific / 3.6, rel=1e-6)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sensors_ok():
    return Sensors(boiler_temp=50.0)

@pytest.fixture
def stats_module():
    return StatsModule()

@pytest.fixture
def state():
    return make_state()


# =============================================================================
# Basic sanity tests (quick)
# =============================================================================

def test_5m_all_off(stats_module, state, sensors_ok):
    cfg_stats(stats_module, 12.0, 29.0)
    run_segments_constant_dt(stats_module, state, sensors_ok, 0.0, dt=1.0, segments=[(300.0, False)])
    s = get_stats(state)
    assert float(s["seconds_5m"]) == pytest.approx(300.0)
    assert float(s["coal_kg_5m"]) == pytest.approx(0.0, abs=1e-9)
    assert float(s["burn_kgph_5m"]) == pytest.approx(0.0, abs=1e-9)
    assert float(s["power_kw_5m"]) == pytest.approx(0.0, abs=1e-9)
    assert float(s["energy_kwh_5m"]) == pytest.approx(0.0, abs=1e-9)

def test_5m_all_on(stats_module, state, sensors_ok):
    feeder_kgph, calorific = cfg_stats(stats_module, 12.0, 29.0)
    run_segments_constant_dt(stats_module, state, sensors_ok, 0.0, dt=1.0, segments=[(300.0, True)])
    s = get_stats(state)
    assert_window_values(s, "5m", 300.0, feeder_kgph, calorific)
    # w stałym ON zwykle min=max=avg (to jest stabilne, ale jeśli chcesz: możesz usunąć)
    assert float(s["burn_kgph_min_5m"]) == pytest.approx(feeder_kgph, rel=1e-4)
    assert float(s["burn_kgph_max_5m"]) == pytest.approx(feeder_kgph, rel=1e-4)

def test_warmup_seconds_5m(stats_module, state, sensors_ok):
    feeder_kgph, _ = cfg_stats(stats_module, 12.0, 29.0)
    run_segments_constant_dt(stats_module, state, sensors_ok, 0.0, dt=1.0, segments=[(10.0, True)])
    s = get_stats(state)
    assert float(s["seconds_5m"]) == pytest.approx(10.0, abs=1e-6)
    assert float(s["burn_kgph_5m"]) == pytest.approx(feeder_kgph, rel=1e-3)


# =============================================================================
# Multiwindow correctness (rolling), different dt (this should FAIL if module is wrong)
# =============================================================================

@pytest.mark.parametrize("dt", [1.0, 10.0, 60.0, 300.0])
def test_dt_invariance_tail_pattern_all_windows(dt, sensors_ok):
    module = StatsModule()
    state = make_state()
    feeder_kgph, calorific = cfg_stats(module, 12.0, 29.0)

    segments = [
        (6 * 86400.0, False),
        (23 * 3600.0, True),
        (55 * 60.0, False),
        (5 * 60.0, True),
    ]
    run_segments_constant_dt(module, state, sensors_ok, 0.0, dt=dt, segments=segments)
    s = get_stats(state)

    for suffix in ["5m", "1h", "4h", "24h", "7d"]:
        expected_on_s = on_seconds_in_last_window(segments, WINDOWS[suffix])
        assert_window_values(s, suffix, expected_on_s, feeder_kgph, calorific)
        assert_internal_consistency(s, suffix, calorific)


# =============================================================================
# Minimal reproductions: show dt dependence on 4h
# =============================================================================

def test_4h_dt_1_vs_60_same(sensors_ok):
    def run(dt: float) -> dict:
        m = StatsModule()
        st = make_state()
        feeder_kgph, calorific = cfg_stats(m, 12.0, 29.0)
        segments = [
            (2 * 3600.0, True),
            (30 * 60.0, False),
            (1 * 3600.0, True),
            (30 * 60.0, False),
        ]
        run_segments_constant_dt(m, st, sensors_ok, 0.0, dt=dt, segments=segments)
        s = get_stats(st)
        expected_on_4h = on_seconds_in_last_window(segments, WINDOWS["4h"])  # 3h
        assert_window_values(s, "4h", expected_on_4h, feeder_kgph, calorific)
        return s

    s1 = run(1.0)
    s60 = run(60.0)

    for k in ["coal_kg_4h", "burn_kgph_4h", "power_kw_4h", "energy_kwh_4h", "seconds_4h"]:
        assert float(s1[k]) == pytest.approx(float(s60[k]), rel=1e-6), f"{k}: dt=1 -> {s1[k]}, dt=60 -> {s60[k]}"


# =============================================================================
# Jitter test: dt changes during run
# =============================================================================

def test_jittered_dt_1h_correctness(stats_module, state, sensors_ok):
    feeder_kgph, calorific = cfg_stats(stats_module, 12.0, 29.0)

    segments = [
        (10 * 60.0, True),
        (20 * 60.0, False),
        (10 * 60.0, True),
        (20 * 60.0, False),
    ]
    dt_seq = [1.0, 2.0, 5.0, 1.0, 3.0, 7.0, 1.0]
    run_segments_jittered_dt(stats_module, state, sensors_ok, 0.0, dt_seq=dt_seq, segments=segments)
    s = get_stats(state)

    expected_on_1h = on_seconds_in_last_window(segments, WINDOWS["1h"])  # 20m ON
    assert_window_values(s, "1h", expected_on_1h, feeder_kgph, calorific)
    assert_internal_consistency(s, "1h", calorific)


# =============================================================================
# Strong correctness under constant ON: should converge for all windows
# =============================================================================

@pytest.mark.parametrize("dt", [1.0, 10.0, 60.0, 300.0])
def test_constant_on_7d_all_windows(dt, sensors_ok):
    module = StatsModule()
    state = make_state()
    feeder_kgph, calorific = cfg_stats(module, 12.0, 29.0)

    segments = [(WINDOWS["7d"], True)]
    run_segments_constant_dt(module, state, sensors_ok, 0.0, dt=dt, segments=segments)
    s = get_stats(state)

    for suffix in ["5m", "1h", "4h", "24h", "7d"]:
        expected_on = WINDOWS[suffix]
        assert_window_values(s, suffix, expected_on, feeder_kgph, calorific)
        assert float(s[f"burn_kgph_{suffix}"]) == pytest.approx(feeder_kgph, rel=1e-4)


# =============================================================================
# Config scaling tests
# =============================================================================

def test_calorific_scales_only_power_and_energy(sensors_ok):
    dt = 10.0
    segments = [(30 * 60.0, True), (30 * 60.0, False)]  # 1h with 30m ON

    mA = StatsModule()
    stA = make_state()
    feeder_kgph, calorificA = cfg_stats(mA, 12.0, 29.0)
    run_segments_constant_dt(mA, stA, sensors_ok, 0.0, dt=dt, segments=segments)
    sA = get_stats(stA)

    mB = StatsModule()
    stB = make_state()
    cfg_stats(mB, 12.0, 10.0)  # different calorific
    run_segments_constant_dt(mB, stB, sensors_ok, 0.0, dt=dt, segments=segments)
    sB = get_stats(stB)

    assert float(sB["coal_kg_1h"]) == pytest.approx(float(sA["coal_kg_1h"]), rel=1e-6)
    assert float(sB["burn_kgph_1h"]) == pytest.approx(float(sA["burn_kgph_1h"]), rel=1e-6)

    ratio = 10.0 / calorificA
    assert float(sB["power_kw_1h"]) == pytest.approx(float(sA["power_kw_1h"]) * ratio, rel=1e-6)
    assert float(sB["energy_kwh_1h"]) == pytest.approx(float(sA["energy_kwh_1h"]) * ratio, rel=1e-6)

def test_feeder_kgph_scales_linearly(sensors_ok):
    dt = 10.0
    segments = [(15 * 60.0, True), (45 * 60.0, False)]  # 1h with 15m ON

    mA = StatsModule()
    stA = make_state()
    feederA, calorific = cfg_stats(mA, 12.0, 29.0)
    run_segments_constant_dt(mA, stA, sensors_ok, 0.0, dt=dt, segments=segments)
    sA = get_stats(stA)

    mB = StatsModule()
    stB = make_state()
    feederB = 6.0
    cfg_stats(mB, feederB, 29.0)
    run_segments_constant_dt(mB, stB, sensors_ok, 0.0, dt=dt, segments=segments)
    sB = get_stats(stB)

    ratio = feederB / feederA
    assert float(sB["coal_kg_1h"]) == pytest.approx(float(sA["coal_kg_1h"]) * ratio, rel=1e-6)
    assert float(sB["burn_kgph_1h"]) == pytest.approx(float(sA["burn_kgph_1h"]) * ratio, rel=1e-6)
    assert float(sB["power_kw_1h"]) == pytest.approx(float(sA["power_kw_1h"]) * ratio, rel=1e-6)
    assert float(sB["energy_kwh_1h"]) == pytest.approx(float(sA["energy_kwh_1h"]) * ratio, rel=1e-6)

@pytest.mark.parametrize("dt", [60.0, 300.0])
def test_7d_tail_pattern_only(dt, sensors_ok):
    module = StatsModule()
    state = make_state()
    feeder_kgph, calorific = cfg_stats(module, 12.0, 29.0)

    segments = [
        (6 * 86400.0, False),
        (23 * 3600.0, True),
        (55 * 60.0, False),
        (5 * 60.0, True),
    ]
    run_segments_constant_dt(module, state, sensors_ok, 0.0, dt=dt, segments=segments)
    s = get_stats(state)

    expected_on_7d = on_seconds_in_last_window(segments, WINDOWS["7d"])
    assert_window_values(s, "7d", expected_on_7d, feeder_kgph, calorific)
    assert_internal_consistency(s, "7d", calorific)


@pytest.mark.parametrize("suffix", ["5m", "1h", "4h", "24h", "7d"])
def test_min_avg_max_consistency_in_constant_on(suffix, sensors_ok):
    """
    Jeśli karmimy moduł stałym ON wystarczająco długo (7d),
    to dla każdego okna średnia powinna być równa feeder_kgph,
    a min <= avg <= max musi być spełnione.
    """
    module = StatsModule()
    state = make_state()
    feeder_kgph, calorific = cfg_stats(module, 12.0, 29.0)

    # dt=60 dla szybkości, ale jeśli moduł gubi przy dt, to i tak test ma to obnażyć
    run_segments_constant_dt(module, state, sensors_ok, 0.0, dt=60.0, segments=[(WINDOWS["7d"], True)])
    s = get_stats(state)

    mn = float(s[f"burn_kgph_min_{suffix}"])
    av = float(s[f"burn_kgph_{suffix}"])
    mx = float(s[f"burn_kgph_max_{suffix}"])

    assert_min_avg_max(mn, av, mx, msg=f"{suffix}: burn min/avg/max")

    # w stałym ON średnia powinna dążyć do feeder_kgph:
    assert av == pytest.approx(feeder_kgph, rel=1e-4), f"{suffix}: avg={av} != {feeder_kgph}"

    # i analogicznie dla power_kw:
    pmn = float(s[f"power_kw_min_{suffix}"])
    pav = float(s[f"power_kw_{suffix}"])
    pmx = float(s[f"power_kw_max_{suffix}"])

    assert_min_avg_max(pmn, pav, pmx, msg=f"{suffix}: power min/avg/max")

@pytest.mark.parametrize("dt", [60.0, 300.0])
def test_7d_all_off_is_zero(dt, sensors_ok):
    m = StatsModule()
    st = make_state()
    feeder_kgph, calorific = cfg_stats(m, 12.0, 29.0)

    run_segments_constant_dt(m, st, sensors_ok, 0.0, dt=dt, segments=[(WINDOWS["7d"], False)])
    s = get_stats(st)

    assert float(s["coal_kg_7d"]) == pytest.approx(0.0, abs=1e-9)
    assert float(s["burn_kgph_7d"]) == pytest.approx(0.0, abs=1e-9)
    assert float(s["energy_kwh_7d"]) == pytest.approx(0.0, abs=1e-9)

	
@pytest.mark.parametrize("dt", [1.0, 10.0, 60.0, 300.0])
def test_4h_after_5h_on_should_be_full_4h(dt, sensors_ok):
    m = StatsModule()
    st = make_state()
    feeder_kgph, calorific = cfg_stats(m, 12.0, 29.0)

    run_segments_constant_dt(m, st, sensors_ok, 0.0, dt=dt, segments=[(5 * 3600.0, True)])
    s = get_stats(st)

    # oczekiwane coal_kg_4h = 12 kg/h * 4h = 48 kg
    assert float(s["coal_kg_4h"]) == pytest.approx(48.0, rel=1e-4)
    assert float(s["burn_kgph_4h"]) == pytest.approx(12.0, rel=1e-4)

@pytest.mark.parametrize("dt", [1.0, 10.0, 60.0, 300.0])
def test_4h_after_10h_on_should_equal_4h_on(dt, sensors_ok):
    m = StatsModule()
    st = make_state()
    feeder_kgph, calorific = cfg_stats(m, 12.0, 29.0)

    # 10h ON -> ostatnie 4h powinno być 4h ON
    run_segments_constant_dt(m, st, sensors_ok, 0.0, dt=dt, segments=[(10 * 3600.0, True)])
    s = get_stats(st)

    expected_on_s = WINDOWS["4h"]
    assert_window_values(s, "4h", expected_on_s, feeder_kgph, calorific)


@pytest.mark.parametrize("dt", [60.0, 300.0])
def test_7d_after_20d_on_should_equal_7d_on(dt, sensors_ok):
    m = StatsModule()
    st = make_state()
    feeder_kgph, calorific = cfg_stats(m, 12.0, 29.0)

    # 20 dni ON -> ostatnie 7 dni powinno być 7d ON
    run_segments_constant_dt(m, st, sensors_ok, 0.0, dt=dt, segments=[(20 * 86400.0, True)])
    s = get_stats(st)

    expected_on_s = WINDOWS["7d"]
    assert_window_values(s, "7d", expected_on_s, feeder_kgph, calorific)

@pytest.mark.parametrize("dt", [1.0, 10.0, 60.0, 300.0])
def test_7d_all_off_is_zero(dt, sensors_ok):
    m = StatsModule()
    st = make_state()
    cfg_stats(m, 12.0, 29.0)

    run_segments_constant_dt(m, st, sensors_ok, 0.0, dt=dt, segments=[(WINDOWS["7d"], False)])
    s = get_stats(st)

    assert float(s["coal_kg_7d"]) == pytest.approx(0.0, abs=1e-9)
    assert float(s["burn_kgph_7d"]) == pytest.approx(0.0, abs=1e-9)
    assert float(s["energy_kwh_7d"]) == pytest.approx(0.0, abs=1e-9)

