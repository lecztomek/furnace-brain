"""After an update restart: windows refilled from the history, outputs parked while the backend is down."""
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from backend.core.shutdown import parking_outputs
from backend.core.state import BoilerMode, Outputs, Sensors, SystemState
from backend.modules.flameout import FlameoutModule
from backend.modules.mixer import MixerModule

ROOT = Path(__file__).resolve().parents[1]
HEADER = "data_czas;temp_pieca;power;temp_grzejnikow;temp_spalin;tryb_pracy;temp_zewn;dmuchawa;slimak;zadana\n"


def write_history(data_root, minutes, row):
    """History CSV with a row every 30 s for the last `minutes`; row(i_minutes_ago) -> dict of values."""
    d = data_root / "modules" / "history" / "data"
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now().replace(microsecond=0)
    files = {}
    for k in range(minutes * 2, 0, -1):
        ts = now - timedelta(seconds=30 * k)
        v = row(k / 2)
        f = files.setdefault(ts.date(), [HEADER])
        f.append(f"{ts.isoformat()};{v['boiler']};{v.get('power', 93)};30;{v.get('flue', 30)};{v.get('mode', 'WORK')};"
                 f"{v.get('outside', 0)};28;0;56\n")
    for day, lines in files.items():
        (d / f"boiler_{day:%Y%m%d}_00.csv").write_text("".join(lines), encoding="utf-8")


def test_flameout_works_right_after_a_restart(tmp_path):
    # the fire died 40 min ago; the backend restarts now
    write_history(tmp_path, 40, lambda ago: {"boiler": 38.0, "flue": 30.0})
    m = FlameoutModule(base_path=Path("/nonexistent"), data_root=tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    st.ts_mono = 1000.0
    st.outputs.power_percent = 93.0
    res = m.tick(now=time.time(), sensors=Sensors(boiler_temp=38.0, flue_gas_temp=30.0), system_state=st)
    assert [e.type for e in res.events] == ["FLAMEOUT"]       # not 30 min later
    assert st.mode == BoilerMode.OFF


def test_flameout_refill_stops_at_the_last_ignition(tmp_path):
    # in WORK only for the last 10 min (before: ignition) - no whole window yet
    write_history(tmp_path, 40, lambda ago: {"boiler": 38.0, "mode": "WORK" if ago <= 10 else "IGNITION"})
    m = FlameoutModule(base_path=Path("/nonexistent"), data_root=tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    st.ts_mono = 1000.0
    st.outputs.power_percent = 93.0
    res = m.tick(now=time.time(), sensors=Sensors(boiler_temp=38.0, flue_gas_temp=30.0), system_state=st)
    assert res.events == [] and st.mode == BoilerMode.WORK
    assert 15 <= len(m._samples) <= 23


def test_heating_curve_average_comes_from_the_history(tmp_path):
    # 6 h: -8 C at night, 0 C now - the average must not start from the current 0 C
    write_history(tmp_path, 360, lambda ago: {"boiler": 56.0, "outside": -8.0 if ago > 60 else 0.0})
    shutil.copy(ROOT / "backend/modules/mixer/schema.yaml", tmp_path / "schema.yaml")
    (tmp_path / "values.yaml").write_text("curve_enabled: true\ncurve_avg_h: 6\n", encoding="utf-8")
    m = MixerModule(base_path=tmp_path, data_root=tmp_path)
    st = SystemState(mode=BoilerMode.WORK)
    st.ts_mono = 5000.0
    m.tick(now=time.time(), sensors=Sensors(boiler_temp=56.0, radiators_temp=30.0, outside_temp=0.0), system_state=st)
    avg = st.runtime["mixer"]["outside_avg"]
    assert -7.5 < avg < -6.0                                   # (5 h at -8 + 1 h at 0) / 6


def test_outputs_parked_while_the_backend_is_down():
    last = Outputs(feeder_on=True, fan_power=40, mixer_open_on=True, pump_co_on=False, pump_cwu_on=True)
    p = parking_outputs(last, boiler_temp=56.0)
    assert (p.feeder_on, p.fan_power, p.mixer_open_on) == (False, 0, False)
    assert p.pump_co_on and p.pump_cwu_on                      # warm boiler: the heat has somewhere to go
    cold = parking_outputs(Outputs(pump_co_on=False, pump_cwu_on=False), boiler_temp=20.0)
    assert not cold.pump_co_on and not cold.pump_cwu_on        # cold boiler: pumps stay as they were
