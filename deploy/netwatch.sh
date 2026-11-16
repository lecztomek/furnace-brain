#!/usr/bin/env bash
# Network watchdog for the Pi (run as root every minute by furnace-netwatch.timer).
#
# The Pi can drop off the router and not come back by itself (the furnace keeps working,
# but there is no weather, remote view or updates). Works for a cable (eth0) and WiFi. Every run:
#   - WiFi power saving off, if there is WiFi (it makes the Pi 3 drop off),
#   - is the router reachable? (ping the default gateway)
#   - unreachable for 3 min  -> the connection (cable or WiFi) off and on again,
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

# the connection to the router: the one with the default route, else the first cable / WiFi one
IFACE="$(ip -4 route show default 2>/dev/null | awk '/default/ {for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')"
if [[ -z "$IFACE" ]]; then
  IFACE="$(ls /sys/class/net 2>/dev/null | grep -E '^(eth|en|wl)' | head -n 1)"
fi
for w in /sys/class/net/wl*; do
  [[ -e "$w" ]] || continue
  iw dev "${w##*/}" set power_save off 2>/dev/null || iwconfig "${w##*/}" power off 2>/dev/null || true
done

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
# carrier 1 = the cable / WiFi has a link to the router (0 = unplugged, the router port off...)
CARRIER="$(cat "/sys/class/net/${IFACE:-none}/carrier" 2>/dev/null || echo '?')"
ADDR="$(ip -4 -br addr show dev "${IFACE:-none}" 2>/dev/null | awk '{print $3}')"
log "router ${GW:-(no route)} unreachable for $FAILS min (${IFACE:-no interface}: link=$CARRIER, address=${ADDR:-none})"

if (( FAILS == 3 )) && [[ -n "$IFACE" ]]; then
  log "$IFACE off and on again"
  ip link set "$IFACE" down; sleep 3; ip link set "$IFACE" up
  [[ "$IFACE" == wl* ]] && { wpa_cli -i "$IFACE" reconfigure >/dev/null 2>&1 || true; }
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
