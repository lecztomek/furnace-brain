#!/usr/bin/env bash
set -euo pipefail

# Repo root = katalog nadrzędny względem miejsca, gdzie leży ten skrypt
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Opcjonalny plik env obok skryptu (nieobowiązkowy)
# np. scripts/dev.env
if [[ -f "$SCRIPT_DIR/dev.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/dev.env"
  set +a
fi

# Defaults (możesz je nadpisać w scripts/dev.env albo przez zmienne środowiskowe)
FRONT_DIR="${FRONT_DIR:-$APP_ROOT/frontend}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
GATEWAY_BIND="${GATEWAY_BIND:-0.0.0.0}"   # LAN krytyczny -> domyślnie bind na wszystkie
GATEWAY_PORT="${GATEWAY_PORT:-8080}"

export PYTHONPATH="${PYTHONPATH:-$APP_ROOT}"
export FURNACE_BRAIN_DATA_ROOT="${FURNACE_BRAIN_DATA_ROOT:-$APP_ROOT/.data}" # testowo lokalnie
export FURNACE_BRAIN_HW_RPI="${FURNACE_BRAIN_HW_RPI:-0}"

UVICORN_BIN="${UVICORN_BIN:-$APP_ROOT/.venv/bin/uvicorn}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
GATEWAY_SCRIPT="${GATEWAY_SCRIPT:-$SCRIPT_DIR/gateway.py}"

# Proste walidacje (żeby błędy były czytelne)
[[ -x "$UVICORN_BIN" ]] || { echo "Brak uvicorn: $UVICORN_BIN (czy jest .venv?)"; exit 1; }
[[ -f "$GATEWAY_SCRIPT" ]] || { echo "Brak gateway.py: $GATEWAY_SCRIPT"; exit 1; }
[[ -d "$FRONT_DIR" ]] || { echo "Brak FRONT_DIR: $FRONT_DIR"; exit 1; }

mkdir -p "$FURNACE_BRAIN_DATA_ROOT"

cleanup() {
  echo "Stopping dev processes..."
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$APP_ROOT"

echo "APP_ROOT=$APP_ROOT"
echo "FRONT_DIR=$FRONT_DIR"
echo "BACKEND=$BACKEND_HOST:$BACKEND_PORT"
echo "GATEWAY=$GATEWAY_BIND:$GATEWAY_PORT"
echo "DATA_ROOT=$FURNACE_BRAIN_DATA_ROOT"

"$UVICORN_BIN" backend.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
"$PYTHON_BIN" "$GATEWAY_SCRIPT" "$FRONT_DIR" "$BACKEND_HOST" "$BACKEND_PORT" "$GATEWAY_PORT" "$GATEWAY_BIND" &

echo "OK:"
echo "  local: http://localhost:$GATEWAY_PORT/"
echo "  LAN:   http://<IP_maszyny>:$GATEWAY_PORT/"
wait
