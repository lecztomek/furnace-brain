from backend.core.downsample import downsample


def rows(n, step_s=30):
    return [{"data_czas": f"2026-10-26T{(i * step_s) // 3600:02d}:{(i * step_s) % 3600 // 60:02d}:{(i * step_s) % 60:02d}",
             "temp_pieca": str(50 + i % 2), "tryb_pracy": "WORK" if i % 10 else "OFF", "temp_zewn": ""}
            for i in range(n)]


def test_small_input_untouched():
    items = rows(100)
    assert downsample(items, 200) is items


def test_averages_numbers_and_keeps_most_common_text():
    out = downsample(rows(2880), 100)
    assert len(out) <= 100
    assert all(abs(o["temp_pieca"] - 50.5) < 0.05 for o in out)
    assert all(o["tryb_pracy"] == "WORK" for o in out)
    assert out[0]["temp_zewn"] == ""


def test_gap_stays_a_gap():
    items = rows(200) + [{"data_czas": "2026-10-26T20:00:00", "temp_pieca": "40", "tryb_pracy": "OFF", "temp_zewn": ""}]
    out = downsample(items, 50)
    assert out[-1]["data_czas"] == "2026-10-26T20:00:00" and out[-1]["temp_pieca"] == 40.0
