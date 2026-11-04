#!/usr/bin/env bash
# Advertise a fixed mDNS name (default nodemonitor.local) for this Pi's current IPv4
# address, in addition to its normal <hostname>.local. Re-publishes when the IP changes.
# Run by nodemonitor-mdns.service (installed by setup_pi.sh). Requires avahi-utils.
set -u

NAME="${1:-nodemonitor}.local"

current_ip() { hostname -I 2>/dev/null | awk '{print $1}'; }

while true; do
    ip="$(current_ip)"
    if [[ -z "$ip" ]]; then
        sleep 5
        continue
    fi

    # -R: don't publish a reverse (PTR) record, which would clash with the real hostname
    avahi-publish -a -R "$NAME" "$ip" &
    pid=$!
    while kill -0 "$pid" 2>/dev/null && [[ "$(current_ip)" == "$ip" ]]; do
        sleep 15
    done

    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        wait "$pid" 2>/dev/null
    else
        # avahi-publish exited, e.g. another device on the network already owns the name
        wait "$pid" 2>/dev/null
        sleep 60
    fi
done
