"""History: the hopper temperature is written; a file of an older version keeps its format."""
import csv
import time

from backend.core.state import BoilerMode, Sensors, SystemState
from backend.modules.history import HistoryModule


def _run(m, tmp_path, t0, n=40):
    st = SystemState(mode=BoilerMode.WORK)
    for i in range(n):
        st.ts_mono = i * 2.0
        m.tick(now=t0 + i * 2.0, sensors=Sensors(boiler_temp=56.0, hopper_temp=38.9), system_state=st)
    return sorted(tmp_path.rglob("boiler_*.csv"))


def test_the_hopper_temperature_is_in_the_history(tmp_path):
    files = _run(HistoryModule(data_root=tmp_path), tmp_path, time.time())
    rows = list(csv.DictReader(files[0].open(encoding="utf-8"), delimiter=";"))
    assert rows and all(r["temp_podajnika"] == "38.9" for r in rows)


def test_a_file_started_by_an_older_version_keeps_its_columns(tmp_path):
    import datetime as dt
    t0 = time.time()
    m = HistoryModule(data_root=tmp_path)
    d = m._log_dir
    d.mkdir(parents=True, exist_ok=True)
    name = f"{m._config.file_prefix}_{dt.datetime.fromtimestamp(t0).strftime('%Y%m%d_%H')}.csv"
    old_header = "data_czas;temp_pieca;power;temp_grzejnikow;temp_spalin;tryb_pracy;temp_zewn;dmuchawa;slimak;zadana"
    (d / name).write_text(old_header + "\n", encoding="utf-8")
    _run(m, tmp_path, t0, n=5)
    lines = (d / name).read_text(encoding="utf-8").splitlines()
    assert all(len(line.split(";")) == 10 for line in lines)          # no column the header doesn't name
