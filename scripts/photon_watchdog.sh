#!/bin/bash
# photon_watchdog.sh — restart the Hermes gateway when Photon is stuck disconnected.
# Runs via launchd every 10 minutes (see com.hermes.photon-watchdog.plist).
# The Photon upstream stream degrades periodically and the adapter's own
# reconnect sometimes wedges in "retrying" for hours; a gateway restart
# (drain-aware) reliably brings the sidecar back.

STATE="$HOME/.hermes/gateway_state.json"
LOG="$HOME/.hermes/logs/photon-watchdog.log"
HERMES="$(command -v hermes || echo "$HOME/.hermes/bin/hermes")"

[ -f "$STATE" ] || exit 0

photon_state=$(python3 - "$STATE" <<'EOF'
import json, sys
try:
    s = json.load(open(sys.argv[1]))
    print(s.get("platforms", {}).get("photon", {}).get("state", "unknown"))
    print(s.get("gateway_state", "unknown"))
except Exception:
    print("unknown"); print("unknown")
EOF
)
p_state=$(echo "$photon_state" | sed -n 1p)
g_state=$(echo "$photon_state" | sed -n 2p)

if [ "$g_state" = "running" ] && [ "$p_state" != "connected" ]; then
    # Debounce: only restart if it's been unhealthy on two consecutive checks.
    FLAG="$HOME/.hermes/.photon_unhealthy"
    if [ -f "$FLAG" ]; then
        rm -f "$FLAG"
        echo "$(date '+%F %T') photon=$p_state — restarting gateway" >> "$LOG"
        "$HERMES" gateway restart >> "$LOG" 2>&1
    else
        touch "$FLAG"
        echo "$(date '+%F %T') photon=$p_state — flagged, will restart if still down next check" >> "$LOG"
    fi
else
    rm -f "$HOME/.hermes/.photon_unhealthy" 2>/dev/null
fi
exit 0
