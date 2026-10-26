#!/usr/bin/env bash
# Aktualizacja furnace-brain na Raspberry Pi prosto z GitHuba.
#
# Normalnie uruchamia go co kilka minut furnace-update.timer - wersję na piecu
# wyznacza branch prod (zmieniany workflowem "Wdróż na prod" na GitHubie).
# Ręcznie (na Pi albo: ssh pi@<ip> furnace-brain/deploy/update.sh):
#
#   deploy/update.sh             -> aktualizacja do origin/prod
#   deploy/update.sh <ref>       -> aktualizacja do brancha/taga/commita (np. origin/main, prod-20261026-1200)
#   deploy/update.sh --dry-run   -> pokaż co się zmieni, nic nie zmieniaj
#   deploy/update.sh --rollback  -> awaryjnie wróć do poprzedniej wersji (do czasu zmiany prod)
#   deploy/update.sh --force     -> spróbuj ponownie wersji, która wcześniej nie wstała
#
# Czego skrypt NIE rusza:
#   - backend/modules/*/values.yaml (ustawienia z GUI) - są w .gitignore,
#   - .venv, dane na FURNACE_BRAIN_DATA_ROOT (historia, logi, statystyki, stany PID),
#   - inne nieśledzone pliki.
# Dodatkowo przed każdą zmianą robi kopię values.yaml do $DATA_ROOT/backups/.
#
# Po restarcie sprawdza, czy backend odpowiada i czy pętla sterowania żyje.
# Jeśli nie - automatycznie wraca do poprzedniej wersji i zapamiętuje złą wersję,
# żeby timer nie próbował jej wdrażać w kółko.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="${FB_APP_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# git reset podmienia ten plik w trakcie działania - uruchom się z kopii
if [[ -z "${FB_APP_ROOT:-}" ]]; then
  tmp="$(mktemp /tmp/furnace-update.XXXXXX)"
  cp "$0" "$tmp"
  FB_APP_ROOT="$APP_ROOT" exec bash "$tmp" "$@"
fi
trap 'rm -f "$0"' EXIT
ENV_FILE="${ENV_FILE:-/etc/furnace-brain.env}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
SERVICES=(furnace-backend.service furnace-gateway.service)
UNIT_FILES=("${SERVICES[@]}" furnace-update.service furnace-update.timer)
PREV_FILE="$APP_ROOT/.deploy_prev"
FAILED_FILE="$APP_ROOT/.deploy_failed"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-60}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
BACKEND_PORT="${BACKEND_PORT:-8000}"
DATA_ROOT="${FURNACE_BRAIN_DATA_ROOT:-$APP_ROOT/backend}"

DRY_RUN=0
ROLLBACK=0
FORCE=0
TARGET_REF="origin/prod"
for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=1 ;;
    --rollback) ROLLBACK=1 ;;
    --force)    FORCE=1 ;;
    -h|--help)  sed -n '2,23p' "$APP_ROOT/deploy/update.sh"; exit 0 ;;
    -*)         echo "Nieznana opcja: $arg"; exit 2 ;;
    *)          TARGET_REF="$arg" ;;
  esac
done

log()  { echo -e "\033[1;34m==>\033[0m $*"; }
warn() { echo -e "\033[1;33m!!\033[0m $*"; }
die()  { echo -e "\033[1;31mBŁĄD:\033[0m $*" >&2; exit 1; }

cd "$APP_ROOT"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "$APP_ROOT nie jest repozytorium gita - zobacz README (sekcja 'Pierwsze przejście na git')."

# --- 1. lokalne zmiany w śledzonych plikach = ktoś edytował kod na Pi ---
LOCAL_CHANGES="$(git status --porcelain --untracked-files=no)"
if [[ -n "$LOCAL_CHANGES" ]]; then
  echo "$LOCAL_CHANGES"
  die "Na Pi są lokalne zmiany w plikach z repo (lista wyżej). Przenieś je do repo albo cofnij: git checkout -- <plik>"
fi

OLD="$(git rev-parse HEAD)"

# --- 2. ustal wersję docelową ---
if (( ROLLBACK )); then
  [[ -f "$PREV_FILE" ]] || die "Brak $PREV_FILE - nie wiem, do czego wracać."
  NEW="$(cat "$PREV_FILE")"
  git cat-file -e "$NEW^{commit}" 2>/dev/null || die "Commit $NEW nie istnieje lokalnie."
else
  log "Pobieram zmiany z GitHuba..."
  git fetch --quiet --tags origin
  NEW="$(git rev-parse --verify "$TARGET_REF^{commit}" 2>/dev/null)" \
    || die "Nie znam wersji '$TARGET_REF'."
fi

if [[ "$OLD" == "$NEW" ]]; then
  log "Już jest wersja $(git log -1 --format='%h %s' "$NEW") - nic do zrobienia."
  exit 0
fi

if (( ! FORCE )) && [[ -f "$FAILED_FILE" && "$(cat "$FAILED_FILE")" == "$NEW" ]]; then
  log "Pomijam $(git log -1 --format='%h %s' "$NEW") - ta wersja już raz nie wstała (--force, żeby spróbować)."
  exit 0
fi

log "Obecna wersja: $(git log -1 --format='%h %s (%cr)' "$OLD")"
log "Nowa wersja:   $(git log -1 --format='%h %s (%cr)' "$NEW")"
if git merge-base --is-ancestor "$OLD" "$NEW"; then
  echo "Nowe commity:"
  git log --oneline "$OLD..$NEW" | sed 's/^/   /'
else
  warn "To NIE jest zwykła aktualizacja do przodu (cofnięcie albo inna gałąź)."
fi
echo "Zmienione pliki:"
git diff --stat "$OLD" "$NEW" | sed 's/^/   /'

if (( DRY_RUN )); then
  log "--dry-run: nic nie zmieniam."
  exit 0
fi

# --- 3. kopia ustawień ---
BACKUP_DIR="$DATA_ROOT/backups/$(date +%Y%m%d_%H%M%S)_$(git rev-parse --short "$OLD")"
mkdir -p "$BACKUP_DIR"
( cd "$APP_ROOT" && find backend -name values.yaml -print0 | xargs -0 -r cp --parents -t "$BACKUP_DIR" )
echo "$OLD" > "$BACKUP_DIR/commit.txt"
log "Kopia values.yaml: $BACKUP_DIR"
# zostaw 20 ostatnich kopii
ls -1dt "$DATA_ROOT"/backups/*/ 2>/dev/null | tail -n +21 | xargs -r rm -rf

# --- funkcje wdrożenia ---
switch_to() {
  local from="$1" to="$2"
  git reset --quiet --hard "$to"   # nie dotyka plików nieśledzonych / z .gitignore

  if [[ -f requirements.txt ]] && ! git diff --quiet "$from" "$to" -- requirements.txt; then
    log "requirements.txt się zmienił - instaluję pakiety..."
    "$APP_ROOT/.venv/bin/pip" install -q -r requirements.txt
  fi

  local reload=0 svc
  for svc in "${UNIT_FILES[@]}"; do
    if [[ -f "deploy/$svc" ]] && ! cmp -s "deploy/$svc" "$SYSTEMD_DIR/$svc"; then
      log "Aktualizuję $SYSTEMD_DIR/$svc"
      sudo install -m 644 "deploy/$svc" "$SYSTEMD_DIR/$svc"
      reload=1
    fi
  done
  if (( reload )); then
    sudo systemctl daemon-reload
    if systemctl is-enabled --quiet furnace-update.timer 2>/dev/null; then
      sudo systemctl restart furnace-update.timer
    fi
  fi

  log "Restart usług..."
  sudo systemctl restart "${SERVICES[@]}"
}

# Zdrowy = /api/state/current odpowiada i "ts" rośnie (pętla sterowania działa).
health_check() {
  python3 - "$BACKEND_PORT" "$HEALTH_TIMEOUT_S" <<'PY'
import json, sys, time, urllib.request
port, timeout = int(sys.argv[1]), float(sys.argv[2])
url = f"http://127.0.0.1:{port}/api/state/current"
deadline = time.time() + timeout
first_ts, last_err = None, "brak odpowiedzi"
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            st = json.load(r)
        ts = st.get("ts")
        if first_ts is None:
            first_ts = ts
        elif ts is not None and ts != first_ts:
            bad = {m: v.get("last_error") for m, v in (st.get("modules") or {}).items()
                   if v.get("health") not in (None, "OK", "DISABLED")}
            print(f"backend OK, mode={st.get('mode')}")
            for m, e in bad.items():
                print(f"  uwaga: moduł {m} nie jest OK: {e}")
            sys.exit(0)
        else:
            last_err = "pętla sterowania stoi (ts się nie zmienia)"
    except Exception as e:
        last_err = str(e)
    time.sleep(2)
print(f"backend NIE działa: {last_err}")
sys.exit(1)
PY
}

# --- 4. wdrożenie + kontrola ---
switch_to "$OLD" "$NEW"
log "Sprawdzam, czy backend wstał (do ${HEALTH_TIMEOUT_S}s)..."
if health_check; then
  echo "$OLD" > "$PREV_FILE"
  rm -f "$FAILED_FILE"
  # ręczne wycofanie: timer nie wróci do porzuconej wersji, dopóki prod się nie zmieni
  if (( ROLLBACK )); then echo "$OLD" > "$FAILED_FILE"; fi
  log "Gotowe: $(git log -1 --format='%h %s' "$NEW")"
  exit 0
fi

warn "Nowa wersja nie działa. Ostatnie logi backendu:"
sudo journalctl -u furnace-backend.service -n 40 --no-pager || true
echo "$NEW" > "$FAILED_FILE"
warn "Wracam do $(git log -1 --format='%h %s' "$OLD")..."
switch_to "$NEW" "$OLD"
if health_check; then
  die "Aktualizacja wycofana - działa poprzednia wersja."
fi
die "Wycofanie też nie pomogło - sprawdź: journalctl -u furnace-backend -e"
