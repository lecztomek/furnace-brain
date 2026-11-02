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


def test_the_topic_comes_from_the_remote_view_key(monkeypatch):
    import hashlib
    monkeypatch.setenv("FURNACE_BRAIN_VIEWER_KEY", "k" * 32)
    assert N.new_topic() == "piec-" + hashlib.sha256(("ntfy:" + "k" * 32).encode()).hexdigest()[:16]
    assert N.new_topic() == N.new_topic()                                  # the same every time
    monkeypatch.delenv("FURNACE_BRAIN_VIEWER_KEY")
    assert N.new_topic() != N.new_topic()                                  # random without the key


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
    m = _module(tmp_path, sent, fading="głośno", overheat="cicho")
    _tick(m, _ev("FLAMEOUT_WARNING"), _ev("BOILER_OVERHEAT_ON"))
    _wait(sent, 2)
    assert sorted(p["title"] for _, p in sent) == ["Ogień słabnie", "Przegrzanie kotła"]


def test_switched_off_sends_nothing(tmp_path):
    sent = []
    m = _module(tmp_path, sent, enabled=False)
    _tick(m, _ev("FLAMEOUT"))
    time.sleep(0.1)
    assert sent == []


def test_the_same_kind_at_most_every_5_min(tmp_path):
    sent = []
    m = _module(tmp_path, sent, module_error="zwykle")
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


def test_shown_in_the_diagnostics():
    from backend.api.service_api import _notify_item
    now = time.time()
    base = {"enabled": True, "topic": "piec-abc", "sends": ["wygaśnięcie"], "simulated": False}
    assert _notify_item({**base}, now)["value"] == "włączone, jeszcze nic nie wysłano"
    ok = _notify_item({**base, "last_sent": now - 7200, "last_title": "Piec wygasł"}, now)
    assert ok["status"] == "ok" and "Piec wygasł" in ok["value"] and "piec-abc" in ok["note"]
    bad = _notify_item({**base, "failed": {"at": now - 60, "title": "Piec wygasł", "error": "URLError"}}, now)
    assert bad["status"] == "warn" and "nie wysłano" in bad["value"]
    assert _notify_item({**base, "enabled": False}, now)["value"] == "wyłączone"


def test_the_diagnostics_data_after_a_failure_and_a_success(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "RETRY_S", (0.01,))
    sent = []
    m = _module(tmp_path, sent, fail=2)                       # the first message fails twice: given up
    _tick(m, _ev("FLAMEOUT"))
    end = time.time() + 2
    while not _tick(m).runtime.get("failed") and time.time() < end:
        time.sleep(0.01)
    rt = _tick(m).runtime
    assert rt["failed"]["title"] == "Piec wygasł" and rt["sends"] == ["wygaśnięcie (alarm)"]
    m._config.send_test = True
    _tick(m)
    _wait(sent, 1)
    time.sleep(0.05)
    rt = _tick(m).runtime
    assert rt["failed"] is None and rt["last_title"] == "Test powiadomień" and rt["last_sent"]


def test_how_loud_is_chosen_per_event(tmp_path):
    sent = []
    m = _module(tmp_path, sent, overheat="cicho", flameout="głośno")
    _tick(m, _ev("FLAMEOUT"), _ev("BOILER_OVERHEAT_ON"))
    _wait(sent, 2)
    prio = {p["title"]: p["priority"] for _, p in sent}
    assert prio == {"Piec wygasł": 4, "Przegrzanie kotła": 2}
    m._apply({"flameout": "bzdura"})                                    # not on the list: kept
    assert m.get_config_values()["flameout"] == "głośno"


def test_the_old_on_off_settings_are_taken_over_once(tmp_path):
    # values.yaml from 1.53/1.54 (new keys already added with the defaults at the start)
    sent = []
    m = _module(tmp_path, sent, send_flameout=True, send_overheat=True, send_fading=False,
                flameout="alarm", overheat="nie")
    v = m.get_config_values()
    assert v["flameout"] == "alarm" and v["overheat"] == "alarm" and v["fading"] == "nie"
    saved = yaml.safe_load((tmp_path / "values.yaml").read_text())
    assert "send_overheat" not in saved and saved["overheat"] == "alarm"   # the old keys dropped


def _sensor_ev(key, on):
    return Event(ts=time.time(), source="safety", level=EventLevel.WARNING,
                 type=f"SENSOR_{key.upper()}_MISSING_{'ON' if on else 'OFF'}", message="x", data={"sensor": key})


def test_a_sensor_lost_for_seconds_is_not_sent(tmp_path):
    sent = []
    m = _module(tmp_path, sent, sensor="głośno")
    t = time.time()
    _tick(m, _sensor_ev("hopper_temp", True), now=t)
    _tick(m, _sensor_ev("hopper_temp", False), now=t + 4)            # back after 4 s (like 02.11 08:36)
    _tick(m, now=t + 600)
    time.sleep(0.1)
    assert sent == []


def test_a_silent_sensor_is_sent_after_2_min_and_its_return_quietly(tmp_path):
    sent = []
    m = _module(tmp_path, sent, sensor="głośno")
    t = time.time()
    _tick(m, _sensor_ev("boiler_temp", True), now=t)
    _tick(m, now=t + 60)
    time.sleep(0.05)
    assert sent == []                                                 # 1 min: not yet
    _tick(m, now=t + 125)
    _tick(m, now=t + 200)                                             # once, not every tick
    _wait(sent, 1)
    time.sleep(0.05)
    assert len(sent) == 1 and sent[0][1]["priority"] == 4
    assert "kotła" in sent[0][1]["message"] and "2 min" in sent[0][1]["message"]
    _tick(m, _sensor_ev("boiler_temp", False), now=t + 600)
    _wait(sent, 2)
    assert sent[1][1]["title"] == "Czujnik kotła znowu działa" and sent[1][1]["priority"] == 2


def test_sensors_off_sends_nothing_even_when_silent_long(tmp_path):
    sent = []
    m = _module(tmp_path, sent)                                       # sensor: nie (the default)
    t = time.time()
    _tick(m, _sensor_ev("radiators_temp", True), now=t)
    _tick(m, now=t + 3600)
    _tick(m, _sensor_ev("radiators_temp", False), now=t + 3700)
    time.sleep(0.1)
    assert sent == []
