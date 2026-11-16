#!/usr/bin/env bash
# Network watchdog for the Pi (run as root every minute by furnace-netwatch.timer).
#
# The Pi 3's WiFi can drop off the router and not come back by itself (the furnace keeps
# working, but there is no weather, remote view or updates). Every run:
#   - WiFi power saving off (it is what usually makes the Pi 3 drop off),
#   - is the router reachable? (ping the default gateway)
#   - unreachable for 3 min  -> WiFi off and on again,
#     6 min                  -> restart the network service (dhcpcd / NetworkManager),
#     10 min                 -> reboot the Pi - at most 3 times a day and not within
#                               30 min of the last boot, so a router that is off for a long
#                               time does not make the controller reboot all day.
# Only the home network is checked: when the router works but the internet does not,
# rebooting the Pi would not help - nothing is done (the main screen says so).
# Everything goes to the journal: journalctl -t furnace-netwatch
set -u

STATE_DIR=/var/lib/furnace-netwatch
mkdir -p "$STATE_DIR"
COUNT_FILE="$STATE_DIR/fails"
REBOOT_FILE="$STATE_DIR/reboots"     # "<date> <count>"
log() { logger -t furnace-netwatch "$*"; }

WIFI=""
if [[ -d /sys/class/net/wlan0 ]]; then
  WIFI=wlan0
  iw dev wlan0 set power_save off 2>/dev/null || iwconfig wlan0 power off 2>/dev/null || true
fi

GW="$(ip -4 route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
if [[ -n "$GW" ]] && ping -c 1 -W 3 "$GW" >/dev/null 2>&1; then
  if [[ -s "$COUNT_FILE" && "$(cat "$COUNT_FILE")" != "0" ]]; then
    log "router $GW reachable again (after $(cat "$COUNT_FILE") min)"
  fi
  echo 0 > "$COUNT_FILE"
  exit 0
fi

FAILS=$(( $(cat "$COUNT_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$FAILS" > "$COUNT_FILE"
log "router ${GW:-(no route)} unreachable for $FAILS min"

if (( FAILS == 3 )) && [[ -n "$WIFI" ]]; then
  log "WiFi off and on again"
  ip link set "$WIFI" down; sleep 3; ip link set "$WIFI" up
  wpa_cli -i "$WIFI" reconfigure >/dev/null 2>&1 || true
elif (( FAILS == 6 )); then
  for svc in dhcpcd NetworkManager networking; do
    if systemctl is-active --quiet "$svc"; then
      log "restarting $svc"
      systemctl restart "$svc"
      break
    fi
  done
elif (( FAILS >= 10 )); then
  TODAY="$(date +%F)"
  read -r DAY N < "$REBOOT_FILE" 2>/dev/null || { DAY=""; N=0; }
  [[ "$DAY" == "$TODAY" ]] || N=0
  UP_S="$(cut -d. -f1 /proc/uptime)"
  if (( N < 3 && UP_S > 1800 )); then
    echo "$TODAY $(( N + 1 ))" > "$REBOOT_FILE"
    echo 0 > "$COUNT_FILE"
    log "no router for $FAILS min - rebooting the Pi ($(( N + 1 ))/3 today)"
    systemctl reboot
  fi
fi
exit 0
