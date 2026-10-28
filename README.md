# Furnace Brain

Controller for a coal boiler with a feeder (retort burner) on a Raspberry Pi 3
(Python 3.7, Raspbian Buster): FastAPI backend, HTML/JS GUI (Polish) for the
800×480 panel and phones. Changes: [CHANGELOG.md](CHANGELOG.md).

## Features
- PID power control (classic integral, anti-windup), feeder and blower driven by power.
- Ignition → work switch, mixing valve with an optional heating curve, CO / CWU pumps.
- Safety: overheating (boiler, hopper), missing sensors, flame-out detection.
- Pages: main diagram, manual mode, settings, statistics (usage, forecast, work,
  efficiency), service (tuning advice, checks, Pi diagnostics), logs.
- Remote read-only view (GitHub Pages + Firebase).

## Layout
- `backend/core` – kernel (critical loop 0.5 s), aux loop (2 s), analytics, tuning
- `backend/modules/*` – one module per function; `schema.yaml` + `values.yaml` (settings, not in git)
- `backend/api` – REST API (`/api/...`)
- `backend/hw` – Raspberry Pi hardware and the simulator (`mock.py`)
- `frontend` – GUI; `viewer` – remote view; `deploy` – services and the update script

## Run and test
```
pip install -r requirements.txt
./furnace-run-dev.sh                 # simulated furnace
python -m pytest -q tests            # ~5 min
```
Simulator env: `FURNACE_BRAIN_TIME_SCALE` (speed), `FURNACE_BRAIN_DEMO_DAYS` (past demo data),
`FURNACE_BRAIN_SERVE_FRONTEND` (serve the GUI from the backend).

## Versions
- `VERSION` holds X.Y.Z (shown on the main page); bump it with every change.
- Settings: on start-up new fields from `schema.yaml` are appended to `values.yaml`; deploys don't touch values.

## Deploy
- `main` → cloud test environment (Render, simulator), on every push that passes the tests.
- `prod` → the furnace. Every 5 min the Pi checks the `prod` branch (`furnace-update.timer`) and runs
  `deploy/update.sh`: back up settings, restart, check the backend – roll back if it didn't start.
- Release: GitHub → Actions → "Deploy to prod" → version = `main`, a tag (`v1.0.0`) or a commit.
  The released commit is tagged `v<VERSION>`; changed code under a released version is refused.

On the Pi:
```
deploy/update.sh --dry-run      # what would change
deploy/update.sh --rollback     # previous version (until the next prod change)
deploy/update.sh --force        # retry a version that failed to start
journalctl -u furnace-update -e
```

## After a restart (updates)
- Kept: settings, mode (`mode_state.json`; RĘCZNY becomes STOP), PID state (if < 15 min old),
  today's coal, the last statistics report, history and logs (a 10–20 s gap).
- Refilled from the history CSV: the flame-out window (protection works at once) and the
  heating curve's outside average.
- While the backend is down the feeder, blower and mixing valve are stopped; with a boiler
  above 45 °C the pumps run.
- Start from zero: kW / kg/h on the main page (10 min window), valve seconds, tuning cache, diagnostics counters.

## Remote view
GUI of the prod version on GitHub Pages, data from Firebase Realtime Database; read-only.
The Pi's `remote_view` module sends the state every 15 s, history and logs every 60 s,
statistics and settings every 10 min; data older than 14 days is deleted.
Link: `https://lecztomek.github.io/furnace-brain/#k=<FURNACE_BRAIN_VIEWER_KEY>` (the key is the password).

Setup, once:
1. Firebase console → project → Realtime Database (europe-west1, locked); rules from `viewer/firebase-rules.json`.
2. Project settings → Service accounts → Database secrets.
3. `/etc/furnace-brain.env` on the Pi: `FURNACE_BRAIN_VIEWER_FIREBASE_URL`, `FURNACE_BRAIN_VIEWER_FIREBASE_SECRET`,
   `FURNACE_BRAIN_VIEWER_KEY` (`openssl rand -hex 16`); restart the backend, enable "Podgląd zdalny".
4. GitHub: Pages source = GitHub Actions; variable `VIEWER_FIREBASE_URL`.
5. Run "Deploy to prod" (or "Remote view (GitHub Pages)").

## New Pi
```
sudo apt install -y git
git clone https://github.com/lecztomek/furnace-brain.git && cd furnace-brain && git checkout prod
cp deploy/furnace-brain.env.sample /etc/furnace-brain.env    # then fill it in
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo cp deploy/furnace-*.service deploy/furnace-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now furnace-backend furnace-gateway furnace-update.timer
```

## Open ideas
- Simple vs advanced settings; review value ranges in all schemas.
- PIN for changes from the LAN.
- History clean-up module; controlled burn-out (shutdown) mode.
- Relay module on an Arduino.
