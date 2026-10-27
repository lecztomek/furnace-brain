# Changelog

Version in `VERSION`: Z = fix, Y = feature, X = big change.

## 1.35
- Remote view: Service page and the forecast work too (sent every 10 min).
- README rewritten, this changelog added.
- Line icons (Lucide style) instead of emoji on the service and statistics pages.
- Statistics restyled like the service page: big icon tiles, short texts, nothing below 15 px.
- "Kontrola" (7-day checks) moved from Statistics to Service.

## 1.34
- Service page redone: big tiles, the PID now as four boxes, one card per area with the change in big digits.

## 1.33
- **Service page** (replaces History):
  - Strojenie: Ki, Kp, feeder dose and blower advice from the last 24 h of settled work; live PID; setting changes compared before / after.
  - Diagnostyka Pi: CPU temperature, power supply, disks, data recording, loop timing, internet, sensors, background analysis timings.
- Every settings change is logged (`SETTINGS_CHANGED`, old → new).
- **Forecast tab** in Statistics: 3-day Open-Meteo forecast with expected coal, cost and heat.
- Analyses run in the background, past days are read once; refreshed rarely (tuning 30 min, forecast 3 h).

## 1.32
- Statistics simpler and bigger; a range per card; kg on the left axis, zł on the right.
- Simulator: 140 l CWU tank.

## 1.31
- Flame-out module (WORK only): stops the furnace with an alarm.
- Main page: one "!" sign for every problem, tap for the list.

## 1.30
- Statistics: tabs, cost in zł (default 1200 zł/t), season forecast, comparison with last season.
- Requests never wait for a report build; progress shown.

## 1.29
- Simulator fitted to the real boiler; staging settings = prod.
- Changing Ki doesn't make the power jump.

## 1.28
- Classic integral frozen while the boiler already heads for the setpoint (no windup).
- Less SD card wear: no log line per HTTP request.

## 1.27
- PID holds the setpoint: classic integral with anti-windup.
- Remote view: 15-minute summary of what was sent; same-timestamp log entries kept.

## 1.25 – 1.26
- Heating curve for the radiators (6 h outside average, off by default).
- Boiler popup: work from setpoint −2 °C, ignition aims at the setpoint.
- Mixing valve net seconds (30 min) on the main page.

## 1.24
- Fuzzy and predictive power regulators removed.

## 1.23
- Main page: coal use in kg/h, radiators temperature with its setpoint.
- Phone layout fixes; statistics show at once after a restart.

## 1.17 – 1.22
- Boiler mode survives restarts.
- Manual blower switch, heat output in kW, pump status dots.
- Errors on the logs page say what failed and why.
- Settings, logs and history usable on phones and the 800×480 panel.

## 1.11 – 1.16
- Statistics page: coal, energy, regulation quality, efficiency, weather model.
- Remote read-only view (GitHub Pages + Firebase).
- Fixes for the Pi: backend hang, damaged CSV files, fast log search.

## 1.1 – 1.10
- Main page: tap badges, popups for pumps, blower and boiler setpoint, manual feeder switch.
- Outside temperature from Open-Meteo.
- Compact logs, blower and feeder in history.
