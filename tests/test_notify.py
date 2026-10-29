"""Powiadomienia: the ticked events to ntfy, in the background, never from the simulator."""
import time

import yaml

from backend.core.state import Event, EventLevel, Sensors, SystemState
from backend.modules import notify as N


def _module(tmp_path, sent, fail=0, **values):
    (tmp_path / "values.yaml").write_text(yaml.safe_dump(values))
    calls = {"n": 0}

    def sender(server, payload):
        calls["n"] += 1
        if calls["n"] <= fail:
            raise OSError("no internet")
        sent.append((server, payload))

    return N.NotifyModule(base_path=tmp_path, sender=sender)


def _tick(m, *events, now=None):
    st = SystemState()
    st.recent_events = list(events)
    return m.tick(now=now if now is not None else time.time(), sensors=Sensors(), system_state=st)


def _ev(etype, msg="x"):
    return Event(ts=time.time(), source="t", level=EventLevel.ALARM, type=etype, message=msg)


def _wait(sent, n, timeout=2.0):
    end = time.time() + timeout
    while len(sent) < n and time.time() < end:
        time.sleep(0.01)


def test_a_topic_is_generated_once_and_kept(tmp_path):
    m = _module(tmp_path, [])
    topic = m.get_config_values()["topic"]
    assert topic.startswith("piec-") and len(topic) == 21
    assert yaml.safe_load((tmp_path / "values.yaml").read_text())["topic"] == topic
    assert N.NotifyModule(base_path=tmp_path, sender=lambda s, p: None).get_config_values()["topic"] == topic


def test_only_the_flameout_by_default(tmp_path):
    sent = []
    m = _module(tmp_path, sent)
    _tick(m, _ev("FLAMEOUT", "Piec wygasł: kocioł 39,9°C"), _ev("FLAMEOUT_WARNING"), _ev("BOILER_OVERHEAT_ON"),
          _ev("SENSOR_HOPPER_TEMP_MISSING_ON"), _ev("MODULE_ERROR"), _ev("MIXER_MOVE"))
    _wait(sent, 1)
    time.sleep(0.1)
    assert len(sent) == 1
    server, payload = sent[0]
    assert server == "https://ntfy.sh" and payload["title"] == "Piec wygasł" and payload["priority"] == 5
    assert "39,9" in payload["message"] and payload["topic"] == m.get_config_values()["topic"]


def test_what_is_ticked_goes(tmp_path):
    sent = []
    m = _module(tmp_path, sent, send_fading=True, send_sensor=True)
    _tick(m, _ev("FLAMEOUT_WARNING"), _ev("SENSOR_FLUE_GAS_TEMP_MISSING_ON"), _ev("BOILER_OVERHEAT_ON"))
    _wait(sent, 2)
    assert sorted(p["title"] for _, p in sent) == ["Brak odczytu czujnika", "Ogień słabnie"]


def test_switched_off_sends_nothing(tmp_path):
    sent = []
    m = _module(tmp_path, sent, enabled=False)
    _tick(m, _ev("FLAMEOUT"))
    time.sleep(0.1)
    assert sent == []


def test_the_same_kind_at_most_every_5_min(tmp_path):
    sent = []
    m = _module(tmp_path, sent, send_module_error=True)
    t = time.time()
    _tick(m, _ev("MODULE_ERROR"), now=t)
    _tick(m, _ev("MODULE_ERROR"), now=t + 2)
    _tick(m, _ev("MODULE_ERROR"), now=t + 400)
    _wait(sent, 2)
    time.sleep(0.1)
    assert len(sent) == 2


def test_retried_when_the_internet_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "RETRY_S", (0.01, 0.01, 0.01))
    sent = []
    m = _module(tmp_path, sent, fail=2)
    _tick(m, _ev("FLAMEOUT"))
    _wait(sent, 1)
    assert len(sent) == 1
    res = _tick(m)
    assert any(e.type == "NOTIFY_SENT" for e in res.events)


def test_the_test_message_once(tmp_path):
    sent = []
    m = _module(tmp_path, sent, send_test=True)
    _tick(m)
    _tick(m)
    _wait(sent, 1)
    time.sleep(0.1)
    assert [p["title"] for _, p in sent] == ["Test powiadomień"]
    assert yaml.safe_load((tmp_path / "values.yaml").read_text())["send_test"] is False


def test_the_simulator_never_sends(monkeypatch):
    monkeypatch.delenv("FURNACE_BRAIN_HW_RPI", raising=False)
    assert not N.on_the_real_furnace()
    monkeypatch.setenv("FURNACE_BRAIN_HW_RPI", "1")
    assert N.on_the_real_furnace()
