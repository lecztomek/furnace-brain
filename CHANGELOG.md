# Changelog

Every version, newest first. Version in `VERSION`: Z = fix, Y = feature, X = big change.

## 1.40.1 · 28.10.2026 11:18
- Hot water: the per-hour sums kept in the day cache are rounded (~0.7 KB a day instead of ~2 KB)

## 1.40.0 · 28.10.2026 11:16
- Statistics, new tab Woda: how much of the coal goes to hot water and how much to the radiators (estimate - the night 0-5 is heating alone, power above that line at the same outside temperature is hot water), per day and by hour of day, with water icons
- A day that starts in WORK at midnight counts as settled from its first row (it has been working since yesterday)

## 1.39.5 · 28.10.2026 10:45
- STOP breaks back as grey bands over the full chart height (behind the lines, lighter), the table of the last stops stays

## 1.39.4 · 28.10.2026 10:44
- Remote view: scripts and styles carry the version in their URL - after a deploy the page no longer mixes a new page with an old cached script ("Cannot set properties of null")

## 1.39.3 · 28.10.2026 10:31
- STOP breaks no longer drawn over the charts: a strip under each 5 min chart (below the hours) and a table of the last stops (from, to, how long)

## 1.39.2 · 28.10.2026 10:27
- STOP bands: a break is the STOP plus the ignition right after it (a 2 min STOP was a sliver), at least 10 px wide with a marker on top

## 1.39.1 · 28.10.2026 10:05
- Tuning: the 2 h after a setpoint change (≥1 °C) are left out - a hot boiler overshooting a lower setpoint no longer reads as a PID swing
- Tuning: a swing is reported from ±1.5 °C (was ±1.0), "bad" from ±2.5 °C - a coal bed reacts slowly, ±1-1.5 °C is normal

## 1.39.0 · 28.10.2026 09:59
- Statistics, Praca: every STOP (coal added, ash taken out) as a grey band on the 5 min power and temperature charts, with a list of stops (time and minutes) under the temperature chart
- Tuning: the blower row explains how to read the flame and ash (good / too little air / too much air)

## 1.38.3 · 28.10.2026 10:30
- Changelog: each version shows its tag or commit - what to enter in "Deploy to prod" to go back to it

## 1.38.2 · 28.10.2026 10:25
- Changelog as a timeline: versions grouped by day with their time, a strip of days on top (how many versions each day)

## 1.38.1 · 28.10.2026 10:23
- Changelog lists every version (each fix too), with its date

## 1.38.0 · 28.10.2026 10:19
- Service: 'Zmiany' tab with the changelog

## 1.37.9 · 28.10.2026 10:13
- Pi diagnostics: all warnings under 'Do sprawdzenia'; no false logs warning
- Disks / RAM low on space were only coloured bars - now listed too, so the 'są uwagi' badge always has its reason under it
- The event log is compacted and written only when something happens: it is flagged after 30 min without a write, not after 3

## 1.37.8 · 28.10.2026 10:09
- Main page: outside average inside the temperature badge, like the radiators setpoint

## 1.37.7 · 28.10.2026 10:08
- Pi diagnostics: details always shown under a heading

## 1.37.6 · 28.10.2026 10:03
- Main page: outside average and its window under the outside temperature

## 1.37.5 · 28.10.2026 10:02
- Statistics no longer stuck rebuilding; service tables; 5 min charts
- Days re-read for the new power fields were never saved (the cache size didn't change), so every refresh read 30 days of files again and the page stayed on 'liczę aktualne'; now each day read from files counts as unsaved. A failed build is logged instead of swallowed.
- Service: 'Co zmienić' and the problems in Kontrola / Diagnostyka as tables under a 'Do sprawdzenia' heading instead of cards
- Regulation and power charts of the last day in 5 min steps

## 1.37.4 · 28.10.2026 09:38
- Pi diagnostics: one bar each for CPU, RAM, SD card and the USB stick

## 1.37.3 · 28.10.2026 09:30
- Service page: rings and zone bars instead of plain tiles
- Tuning: a ring with the share of time within 2 C of the setpoint and bars with good / so-so / bad zones and a marker for the deviation, the swing and the average power
- Pi diagnostics: rings for CPU temperature, RAM and disk; status tiles with icons for power supply, internet, sensors (a dot each) and uptime

## 1.37.2 · 28.10.2026 09:26
- Tuning advice rebuilt every 10 min instead of 30

## 1.37.1 · 28.10.2026 09:20
- History refill after a restart: only the wanted hour files, in the background

## 1.37.0 · 28.10.2026 09:07
- Statistics: 'Moc pieca' card - kWh, power %, stability
- Tiles: average and peak kW, average controller power with its spread, share of time at full power (>=90%) and at the minimum (<=25%)
- Charts: heat per hour (last day) or per day in kWh; controller power in % every 10 min (last day) or the daily average in WORK
- Per day: power sums in settled WORK; the last 30 cached days are read again once in the background to get them

## 1.36.1 · 28.10.2026 08:57
- Heating curve: 12 h average by default; a weather outage keeps the average

## 1.36.0 · 28.10.2026 08:56
- Restart-proof: outputs parked while down, windows refilled from history
- On shutdown the feeder, blower and mixing valve stop (the pins keep their state until the new process starts); with a boiler above 45 C the pumps run
- Flame-out: the 30 min window is refilled from the history CSV, so the protection works right after a restart (only the unbroken WORK stretch)
- Heating curve: the outside average (6 h) is refilled from the history instead of starting from the current reading

## 1.35.6 · 28.10.2026 08:39
- Kontrola over the last 3 days; every card names its period
- Checks use a new 3-day period (today included); tiles counted over another period (efficiency trend, consumption vs weather) say so
- Statistics cards show 'ostatnie 7 dni (od 22.10)' etc. next to the title, the season card its start; service cards say what they are counted from

## 1.35.5 · 28.10.2026 08:34
- Regulation chart for the last day in 10 min steps

## 1.35.4 · 28.10.2026 08:30
- Regulation chart shows the lowest temperature of each hour; periods named on the tiles

## 1.35.3 · 27.10.2026 21:14
- Kontrola as small tiles with one number each, problems explained below

## 1.35.2 · 27.10.2026 21:00
- Remote view: service page and forecast; README rewritten, CHANGELOG added

## 1.35.1 · 27.10.2026 20:47
- Kontrola moved from the statistics to the service page

## 1.35.0 · 27.10.2026 20:40
- Statistics in the service page style; modern line icons instead of emoji
- Statistics: big tiles with icons, short texts, checks as cards (details on tap, problems open), weather model as three tiles, bigger chart labels, nothing below 15 px
- Js/icons.js: Lucide-style line icons in coloured chips on both pages, weather icons coloured by the sky

## 1.34.0 · 27.10.2026 20:26
- Service page redone: big icon tiles and cards, short texts, no small print
- Tuning: three big tiles, the PID now as four boxes (power bar split into P and I), one card per area with the change in big digits; tap a card for its description; other areas only when something is wrong
- Setting changes as before / after bars, hidden when there are none
- Diagnostics: six big tiles, problems as cards, all details folded
- Nothing smaller than 15 px

## 1.33.4 · 27.10.2026 19:41
- Service page: burger menu works (missing app wrapper)

## 1.33.3 · 27.10.2026 19:38
- Diagnostics: when each background analysis ran and how long it took

## 1.33.2 · 27.10.2026 19:26
- Service page and forecast refreshed less often
- Tuning advice rebuilt at most every 30 min, nothing read before the page is opened
- Service page refreshes every 30 s, system commands every 5 min
- Weather forecast fetched every 3 h

## 1.33.1 · 27.10.2026 19:23
- Service page never waits for the analysis; past days read once
- Tuning advice built in a background thread (first one with pauses, warm-up 2 min after start); a request returns at once, the page shows progress
- Past days of history / events kept in memory, a rebuild reads only today
- Data directories listed once per build instead of a glob per day
- Diagnostics: system commands cached for a minute, newest data file found without a stat of every file

## 1.33.0 · 27.10.2026 17:50
- Service page instead of History: PID tuning advice and Raspberry Pi diagnostics; forecast tab in statistics
- Serwis / Strojenie: integral (Ki), Kp, feeder dose and blower judged on the last 24 h of settled work (from 2 h after ignition), with concrete changes; the PID chain live (error, P, I, power, feeder rhythm, blower); setting changes compared 12 h before / after
- Every settings change is logged (SETTINGS_CHANGED, old -> new)
- Serwis / Diagnostyka Pi: CPU temperature, power supply (throttling), load, memory, disks, data recording, loop timing, internet, sensors, modules
- Statystyki / Prognoza: Open-Meteo forecast for 3 days (fetched hourly) with the coal, cost and heat expected from the days the furnace ran

## 1.32.2 · 27.10.2026 17:13
- Statistics: kg on the left axis, zl on the right one, on the same grid lines

## 1.32.1 · 27.10.2026 17:10
- Simulator: CWU tank 140 l like the real one

## 1.32.0 · 27.10.2026 17:04
- Statistics: simpler and bigger; ranges per card, only where they apply
- Each card/tab has its own range (Dziś = last 24 h / 7 dni / 30 dni / Sezon) and everything under it follows it; the season chart, weather and checks have no range. 'Wczoraj' dropped.
- Big headline numbers (coal, cost, per day; 'w normie' % of time).
- Charts: one thing each, titled in words, thick lines without dots, bigger labels, one axis. Coal: bars only (kg/zl axis; tooltip kg, zl, kWh, outside temp). Regulation: boiler line over a green band (setpoint +-2 C) with room around it. Efficiency: hidden with a note until there are days with outside temperature. Drops: the last 5.

## 1.31.1 · 27.10.2026 16:56
- Main page: the warning sign is just '!', a tap opens the list of problems

## 1.31.0 · 27.10.2026 16:56
- Flame-out module; warning sign on the main page for every problem

## 1.30.4 · 27.10.2026 16:46
- Statistics: cost in zl on the coal charts

## 1.30.3 · 27.10.2026 16:43
- Statistics: coal per hour only for today and yesterday, read on the fly

## 1.30.2 · 27.10.2026 16:39
- Statistics: charts follow the chosen period; energy in kWh instead of power %

## 1.30.1 · 27.10.2026 16:25
- Statistics: requests never wait for a report build; build progress shown; coal price 1200 zl/t

## 1.30.0 · 27.10.2026 16:17
- Statistics: tabs, costs, season forecast and last season comparison; fewer misleading numbers
- Tabs at the top: Zużycie / Praca / Sprawność / Kontrola (remembered).
- Coal price setting (Statistics, zł/t): cost of the period, per day, of the season so far and forecast.
- Season: used so far vs the same part of last season, forecast for the whole season (coal per degree-day x typical Nowy Sącz weather until the end of May), and a monthly chart of this season next to the previous one. The report now also reads last season's days.
- Removed: peak power (5 min feeder bursts), energy in the coal, flue loss and flue temperature (the flue probe reads far too low), fan / feeder %. 'Jak trzyma temperaturę' cut to 3 tiles.
- Checks: ignitions count only IGNITION lasting 5+ min (not clicks), the boiler sensor is bad only above 5% missing readings, no flue check.

## 1.29.1 · 27.10.2026 16:01
- PID: changing ki doesn't make the power jump

## 1.29.0 · 27.10.2026 15:53
- Simulator fitted to the real boiler; staging settings = prod; all tests pass
- Coal reaches the fire 6 min after the feeder (retort) and burns over ~15 min; boiler inertia 240 kg water equivalent (real: temperature keeps going ~30 min after a power drop, ignition 33->55 C in ~1 h);
- House load ~4.3 kW at 0 C / half at +10 C, ~75% efficiency, small boiler/CWU losses, feeder 12 kg/h, unburnt coal only ~5% with the fan off (was ~70% at a 28% fan, so the boiler needed 90% power);
- With last season's settings it reproduces the real boiler: 1.5 C below the setpoint, swing 0.57 C, 56% power at 0 C (real 1.1-1.8 / 0.6 / 53%).

## 1.28.3 · 27.10.2026 15:08
- Curve change logged with the new target; statistics: last 24 h always, power in kW; mixer tests fixed
- Mixer: saving new heating curve settings logs MIXER_CURVE_CHANGED with the target it gives now.
- Statistics: 'Regulacja w ciągu doby' always shows the last 24 hours (the page opens on 7 days, which showed the 0-23 average); the period buttons change the drops table and the worst-hour note. 'Praca pieca' shows the average power in kW (controller % in the unit line).
- Test_mixer_module: advance the monotonic clock the module times its pulses on, and use its own settings directory.

## 1.28.2 · 27.10.2026 14:40
- Less SD card wear: no log line per HTTP request

## 1.28.1 · 27.10.2026 14:20
- Statistics: regulation chart shows the last 24 hours for 'today'

## 1.28.0 · 27.10.2026 14:09
- PID: classic integral doesn't wind up while the boiler heads for the setpoint

## 1.27.4 · 27.10.2026 13:19
- Classic PID integral back on by default

## 1.27.3 · 27.10.2026 13:13
- Remote view: log entries with the same timestamp no longer overwrite each other

## 1.27.2 · 27.10.2026 12:55
- Classic PID integral off by default

## 1.27.1 · 27.10.2026 12:53
- Remote view: 15 min summary in the log of what was sent

## 1.27.0 · 27.10.2026 12:02
- PID holds the setpoint: classic integral with anti-windup

## 1.24.0 · 27.10.2026 10:44
- Remove the fuzzy and predictive power regulators

## 1.23.6 · 27.10.2026 09:25
- Main page: pump status dots were swapped

## 1.23.5 · 27.10.2026 09:22
- CO pump: settings changed in the GUI apply at once; its mode works

## 1.23.4 · 27.10.2026 09:14
- Main page: radiators temperature on the radiator, with its setpoint

## 1.23.3 · 27.10.2026 08:55
- Phone: menu button in one place on every page; history scrolls
- The menu button sits in the bottom right corner on every page on a phone (main, manual, statistics too - before it was on the left there)
- History: the page scrolls, so all series buttons under the chart can be reached; compact series buttons, fixed chart height
- Manual mode: long names wrap instead of pushing the switches off the screen

## 1.23.2 · 27.10.2026 08:53
- Statistics show at once after a restart
- The last report is saved to analytics_report.json; after a restart the page shows it right away ("Stan na HH:MM • liczę aktualne…") while a fresh one is built in the background, and reloads itself when it's ready
- The per-day cache is saved every 10 newly computed days, so a restart in the middle of a long first build (months of CSV on the Pi) no longer throws the work away - with many deploys in a row it restarted from zero every time
- The background build starts 5 s after start instead of 20 s

## 1.23.1 · 26.10.2026 19:46
- Steady kW / kg/h on the main page: measured over whole feeder cycles

## 1.23.0 · 26.10.2026 19:44
- Main page: current coal use in kg/h under the heat output

## 1.22.4 · 26.10.2026 19:04
- Settings on the 800x480 panel: modules in one scrolled row

## 1.22.3 · 26.10.2026 19:01
- Settings usable on a phone

## 1.22.2 · 26.10.2026 18:59
- Logs and history readable on a phone
- Logs (screens up to 640 px wide): each entry as a card - time, source and level on the first line, the message (2 lines) under it; filters in two columns; no column header row
- History: compact range bar (one row of buttons, compare and refresh under it), taller chart
- Both: the menu button moves to the bottom right corner, so it doesn't cover the list / chart
- Log times in Polish short format ("26.10, 18:00:04") everywhere

## 1.22.1 · 26.10.2026 18:56
- Revert phone alerts (ntfy) - postponed

## 1.22.0 · 26.10.2026 18:55
- Phone alerts through ntfy - cold flue gas at high power

## 1.21.3 · 26.10.2026 18:53
- Page icon: flame in the browser tab and on phone/tablet home screens

## 1.21.2 · 26.10.2026 17:12
- No-data warning sign: main page only, bottom left corner

## 1.21.1 · 26.10.2026 17:10
- No-data warning as a big road-sign triangle over the tank

## 1.21.0 · 26.10.2026 17:08
- Statistics: regulation over the day; warning when data stops
- Statistics page, card "Regulacja w ciągu doby": for each hour of the day the average boiler temperature vs the setpoint, the share of time more than 4 C below it and the average power, plus the list of drops (when, how long, lowest temperature, power before and at most) - the evening bath dips show up at once
- Every page (also the remote view): "⚠ Brak nowych danych od HH:MM (N min)" at the top when the control loop timestamp stops changing for 30 s (90 s in the remote view) or the backend doesn't answer; data older than 5 min is flagged right at page load

## 1.20.0 · 26.10.2026 16:52
- Errors on the logs page say what failed and why
- Every unexpected API error (HTTP 500) is logged with the request, the error and the last lines of the traceback (source "api"), at most every 10 min for the same error; the answer carries the error text
- Remote view: each part (state, history and logs, statistics, settings, 5 min history, cleanup) is sent and reported separately - a failing endpoint is named with the HTTP status and the server's answer, and the rest still goes out
- Remote view and weather: a problem is logged when it starts or changes, repeated every 30 min while it lasts (with the number of attempts) and logged again when it is fixed; DNS, timeouts and refused connections are described in plain words

## 1.19.3 · 26.10.2026 16:41
- Main page: pump dots back beside the pumps, only lower

## 1.19.2 · 26.10.2026 16:39
- Main page: pump status dots under the pumps

## 1.19.1 · 26.10.2026 16:35
- Main page: heat output next to the power percent

## 1.19.0 · 26.10.2026 16:28
- Main page: current heat output in kW

## 1.18.1 · 26.10.2026 15:33
- GUI reloads itself after an update

## 1.18.0 · 26.10.2026 15:21
- Main page: "DMUCHAWA RĘCZNIE" switch next to the blower

## 1.17.0 · 26.10.2026 15:13
- Boiler mode survives restarts - updates and power cuts

## 1.16.4 · 26.10.2026 14:52
- Logs: filtered search reads only the last two hour files

## 1.16.3 · 26.10.2026 14:51
- Logs: fast search by source on the Pi
- For a rarely logging module the page waited for minutes. Files that

## 1.16.2 · 26.10.2026 14:38
- Statistics: read damaged CSV files; menu on narrow screens

## 1.16.1 · 26.10.2026 14:30
- Fix: backend stopped answering on the Raspberry Pi

## 1.16.0 · 26.10.2026 13:22
- Remote view on GitHub Pages + Firebase instead of Render

## 1.15.0 · 26.10.2026 13:09
- Statistics: efficiency - coal per degree-day and estimated flue loss
- Kg of coal per heating degree-day (base 18 °C) per period and per day: the same house at the same weather should need the same coal, so a rise points at a dirty boiler
- Estimated flue gas loss (Siegert, coal coefficients, assumed 10 % CO2, 20 °C boiler room) from the flue temperature in WORK
- Trend: last 7 full days vs the 3 weeks before; check warns at +15 %
- "Efektywność" card with tiles, a 30-day trend chart and an explanation; per-day values only for (nearly) full days

## 1.14.0 · 26.10.2026 13:04
- History: 7 days with averaging, compare with yesterday; faster statistics
- History API: max_points averages rows in time buckets (numbers averaged, mode = most frequent), only files inside the range are read; the page asks for at most 900 points - 7 days load as ~840 points instead of ~19k
- History page: "7 dni" range, "porównaj z wczoraj" (same series 24 h earlier, dashed), real time axis, gaps in the data stay empty, label for the setpoint series
- Statistics: report precomputed in the background after start-up and reused for a minute (refresh button recomputes)
- Viewer: same max_points support

## 1.13.0 · 26.10.2026 12:51
- Statistics: energy in kWh, average and peak heat output
- Energy from coal (kWh, per day), average heat output (kW) and the highest 5 min output per period; kWh per day in the weather model
- Note on the page: energy in the fuel (calorific value), without boiler efficiency
- Demo data energy computed like the stats module (no efficiency factor)

## 1.12.0 · 26.10.2026 12:49
- Remote read-only view of the furnace
- New aux module "Podgląd zdalny" (remote_view, off by default): a background thread reads the local API (state, manual, history and log increments, statistics report, settings) and pushes it gzipped to the viewer; server address and token from /etc/furnace-brain.env; only sends, never receives
- Backend/viewer.py: separate app serving the unchanged GUI on the pushed data, same read API shapes as the Pi; every write is accepted and ignored (replies carry the real values); push requires the token; optional viewing password; health endpoint
- Render.yaml: furnace-brain-view service on the prod branch (token generated by Render)
- Tests for the viewer (push, token, ignored writes, gzip)

## 1.11.0 · 26.10.2026 12:33
- New statistics page: coal, averages, regulation quality, checks, weather model
- Backend/core/analytics.py: per-day aggregates from history (30 s rows), coal buckets and event log; past days cached in analytics_days.json; periods today / yesterday / 7 d / 30 d / season
- GET /api/analytics/report: coal (total, per day, per hour), averages (power, boiler, radiators, flue, outside, blower, feeder), time in modes, ignitions, regulation quality in settled WORK (within ±1/±2 °C, mean error, σ, max over/under, grade), health checks with ok/warn/bad, and a linear model of coal and power vs outside temperature
- History records the WORK setpoint ("zadana") for the regulation stats
- Stats page rebuilt around the report (period buttons, daily coal vs outside temperature chart, scatter with trend line)
- Test environment: FURNACE_BRAIN_DEMO_DAYS seeds past days of demo data (simulator only), set to 30 on Render

## 1.10.0 · 26.10.2026 12:25
- Compact logs; blower and feeder series in history
- Mixer: one entry per movement (direction, real run time, CO vs target, mode) instead of decision + start + stop; boiler protection: first close plus a summary when it ends
- Blower: no entry per power change; mode changes + a 15 min summary (average power, share of time running)
- Feeder: no ON/OFF entries; a 15 min summary (feeds, total time); manual feeding in MANUAL mode logged right away
- History: "dmuchawa" (average %) and "slimak" (% of time feeding) per row, off by default on the chart
- Fix: changing the event log settings moved the log directory into the code folder, so new entries disappeared from the logs page

## 1.9.3 · 26.10.2026 12:09
- Logs: full source/level lists from /logs/meta
- New GET /api/logs/meta: every event level and every source (enabled modules + kernel/aux_runner); the logs page already requested it and got a 404 - it now fills both filters from that single call instead of deriving sources from the latest entries
- Level filter: ALARM added (with its own colour), unused DEBUG removed

## 1.9.2 · 26.10.2026 12:05
- CWU tank closer to the hopper; logs source filter searches all logs
- The redrawn CWU tank moves right, next to the fuel hopper, so the weather window has more room around it
- Logs: the source filter is sent to the backend (which searches all log files) instead of filtering only the latest N entries, and the source list contains every module - rare events like the weather module's were invisible behind frequent feeder/mixer entries

## 1.9.1 · 26.10.2026 12:00
- Weather window further left of the pipe, temperature overlapping it

## 1.9.0 · 26.10.2026 11:56
- Outside temperature on the main page: window with sun/moon

## 1.8.0 · 26.10.2026 11:18
- Outside temperature from Open-Meteo, saved in history
- New aux module "weather" (Pogoda): location in the settings (default Nowy Sącz), fetches the current temperature from Open-Meteo every 15 min in a background thread with a 10 s timeout - never blocks any loop; retries after 5 min on errors, one warning event per outage
- Aux modules can publish data to SystemState.runtime; the kernel uses runtime["weather"] as sensors.outside_temp when there is no outside sensor and the data is fresh (max 60 min old)
- History records temp_zewn; /history/fields returns the union of headers so the new column shows up next to older files; chart label/colour added
- The simulator reports no outside sensor, like the real furnace

## 1.7.0 · 26.10.2026 11:11
- Boiler popup: one setpoint, switch and ignition set automatically

## 1.6.1 · 26.10.2026 11:05
- Show the version box reliably on Safari

## 1.6.0 · 26.10.2026 11:03
- Log details in a popup; no stale cached files after deploys
- Logs: tapping an entry opens a popup (full message, source, type, event data as key/value rows) instead of expanding the row; compact toolbar on short screens so the list is usable on an 800x480 panel
- Static files are served with "Cache-Control: no-cache" (Render and the Pi gateway), so a browser revalidates and never mixes a new page with an old cached stylesheet (Safari showed the feeder switch as a black box)
- The feeder switch carries its colours as svg attributes as well

## 1.5.0 · 26.10.2026 10:56
- Ignition->work switch temperature in the boiler popup; plain-svg feeder switch
- Boiler popup: work / switch / ignition setpoints kept in order (work <= switch <= ignition), so ignition can't get stuck
- "ŚLIMAK RĘCZNIE" switch drawn as plain svg instead of a foreignObject, which Safari on iPad placed in the wrong spot
- Slightly smaller popups on very short screens

## 1.4.0 · 26.10.2026 10:46
- Manual feeder switch on the fuel hopper

## 1.3.0 · 26.10.2026 10:36
- Boiler setpoint popup on the main page
- The WORK setpoint, written to PID, fuzzy and predictive at once (shown when they currently differ, saving aligns them)
- The IGNITION setpoint, which can't go below the WORK one (raising WORK above it raises IGNITION too)

## 1.2.0 · 26.10.2026 10:31
- Pumps and blower settable from the main page; popups fit short screens
- The setpoint popup is generic: one or more fields per diagram element, ranges/steps/units/labels from the module schema, only changed fields saved
- Pump CO and CWU: switch-on temperature; blower: cycle time and speed
- All clickable elements get the pencil badge
- Popups (incl. the feeder one) shrink on screens below 900px height instead of overflowing; feeder presets in one row there

## 1.1.2 · 26.10.2026 10:25
- Fit pages to the visible screen height on tablets and phones

## 1.1.1 · 26.10.2026 10:21
- Smaller tap badge in the page's badge colours

## 1.1.0 · 26.10.2026 10:18
- Mark tappable parts of the main page with a pencil badge
