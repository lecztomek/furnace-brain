#!/usr/bin/env bash
# Updates furnace-brain on the Raspberry Pi straight from GitHub.
#
# Normally run every few minutes by furnace-update.timer - the version running
# on the furnace is whatever the prod branch points to (moved by the
# "Deploy to prod" workflow on GitHub).
# Manually (on the Pi, or: ssh pi@<ip> furnace-brain/deploy/update.sh):
#
#   deploy/update.sh             -> update to origin/prod
#   deploy/update.sh <ref>       -> update to a branch/tag/commit (e.g. origin/main, v1.0.0)
#   deploy/update.sh --dry-run   -> show what would change, change nothing
#   deploy/update.sh --rollback  -> emergency: go back to the previous version (until prod changes)
#   deploy/update.sh --force     -> retry a version that previously failed to start
#
# What the script does NOT touch:
#   - backend/modules/*/values.yaml (settings from the GUI) - they are in .gitignore,
#   - .venv, data under FURNACE_BRAIN_DATA_ROOT (history, logs, stats, PID state),
#   - any other untracked files.
# On top of that, values.yaml files are backed up to $DATA_ROOT/backups/ before every change.
#
# After the restart it checks that the backend responds and the control loop is alive.
# If not, it rolls back to the previous version and remembers the bad one,
# so the timer does not keep redeploying it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="${FB_APP_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# git reset replaces this file while it runs - run from a copy
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
    -h|--help)  sed -n '2,24p' "$APP_ROOT/deploy/update.sh"; exit 0 ;;
    -*)         echo "Unknown option: $arg"; exit 2 ;;
    *)          TARGET_REF="$arg" ;;
  esac
done

log()  { echo -e "\033[1;34m==>\033[0m $*"; }
warn() { echo -e "\033[1;33m!!\033[0m $*"; }
die()  { echo -e "\033[1;31mERROR:\033[0m $*" >&2; exit 1; }

cd "$APP_ROOT"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "$APP_ROOT is not a git repository - see README ('Switching the Pi to git')."

# --- 1. local changes to tracked files = someone edited code on the Pi ---
LOCAL_CHANGES="$(git status --porcelain --untracked-files=no)"
if [[ -n "$LOCAL_CHANGES" ]]; then
  echo "$LOCAL_CHANGES"
  die "Local changes to tracked files on the Pi (listed above). Move them to the repo or revert: git checkout -- <file>"
fi

OLD="$(git rev-parse HEAD)"

# --- 2. resolve the target version ---
if (( ROLLBACK )); then
  [[ -f "$PREV_FILE" ]] || die "No $PREV_FILE - don't know what to roll back to."
  NEW="$(cat "$PREV_FILE")"
  git cat-file -e "$NEW^{commit}" 2>/dev/null || die "Commit $NEW does not exist locally."
else
  log "Fetching from GitHub..."
  git fetch --quiet --tags origin
  NEW="$(git rev-parse --verify "$TARGET_REF^{commit}" 2>/dev/null)" \
    || die "Unknown version '$TARGET_REF'."
fi

if [[ "$OLD" == "$NEW" ]]; then
  log "Already at $(git log -1 --format='%h %s' "$NEW") - nothing to do."
  exit 0
fi

if (( ! FORCE )) && [[ -f "$FAILED_FILE" && "$(cat "$FAILED_FILE")" == "$NEW" ]]; then
  log "Skipping $(git log -1 --format='%h %s' "$NEW") - this version already failed to start (use --force to retry)."
  exit 0
fi

log "Current version: $(git log -1 --format='%h %s (%cr)' "$OLD")"
log "New version:     $(git log -1 --format='%h %s (%cr)' "$NEW")"
if git merge-base --is-ancestor "$OLD" "$NEW"; then
  echo "New commits:"
  git log --oneline "$OLD..$NEW" | sed 's/^/   /'
else
  warn "This is NOT a plain forward update (rollback or a different branch)."
fi
echo "Changed files:"
git diff --stat "$OLD" "$NEW" | sed 's/^/   /'

if (( DRY_RUN )); then
  log "--dry-run: not changing anything."
  exit 0
fi

# --- 3. back up settings ---
BACKUP_DIR="$DATA_ROOT/backups/$(date +%Y%m%d_%H%M%S)_$(git rev-parse --short "$OLD")"
mkdir -p "$BACKUP_DIR"
( cd "$APP_ROOT" && find backend -name values.yaml -print0 | xargs -0 -r cp --parents -t "$BACKUP_DIR" )
echo "$OLD" > "$BACKUP_DIR/commit.txt"
log "values.yaml backup: $BACKUP_DIR"
# keep the 20 most recent backups
ls -1dt "$DATA_ROOT"/backups/*/ 2>/dev/null | tail -n +21 | xargs -r rm -rf

# --- deployment helpers ---
switch_to() {
  local from="$1" to="$2"
  git reset --quiet --hard "$to"   # leaves untracked / .gitignore'd files alone

  if [[ -f requirements.txt ]] && ! git diff --quiet "$from" "$to" -- requirements.txt; then
    log "requirements.txt changed - installing packages..."
    "$APP_ROOT/.venv/bin/pip" install -q -r requirements.txt
  fi

  local reload=0 svc
  for svc in "${UNIT_FILES[@]}"; do
    if [[ -f "deploy/$svc" ]] && ! cmp -s "deploy/$svc" "$SYSTEMD_DIR/$svc"; then
      log "Updating $SYSTEMD_DIR/$svc"
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

  log "Restarting services..."
  sudo systemctl restart "${SERVICES[@]}"
}

# Healthy = /api/state/current responds and "ts" advances (control loop is running).
health_check() {
  python3 - "$BACKEND_PORT" "$HEALTH_TIMEOUT_S" <<'PY'
import json, sys, time, urllib.request
port, timeout = int(sys.argv[1]), float(sys.argv[2])
url = f"http://127.0.0.1:{port}/api/state/current"
deadline = time.time() + timeout
first_ts, last_err = None, "no response"
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
                print(f"  warning: module {m} is not OK: {e}")
            sys.exit(0)
        else:
            last_err = "control loop is stuck (ts not advancing)"
    except Exception as e:
        last_err = str(e)
    time.sleep(2)
print(f"backend is NOT running: {last_err}")
sys.exit(1)
PY
}

# --- 4. deploy + verify ---
switch_to "$OLD" "$NEW"
log "Checking that the backend came up (up to ${HEALTH_TIMEOUT_S}s)..."
if health_check; then
  echo "$OLD" > "$PREV_FILE"
  rm -f "$FAILED_FILE"
  # manual rollback: the timer won't return to the abandoned version until prod changes
  if (( ROLLBACK )); then echo "$OLD" > "$FAILED_FILE"; fi
  log "Done: $(git log -1 --format='%h %s' "$NEW")"
  exit 0
fi

warn "The new version does not work. Last backend logs:"
sudo journalctl -u furnace-backend.service -n 40 --no-pager || true
echo "$NEW" > "$FAILED_FILE"
warn "Rolling back to $(git log -1 --format='%h %s' "$OLD")..."
switch_to "$NEW" "$OLD"
if health_check; then
  die "Update rolled back - the previous version is running."
fi
die "Rollback did not help either - check: journalctl -u furnace-backend -e"
