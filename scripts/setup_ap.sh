#!/bin/bash

#	how to run this script
#	chmod +x setup_ap.sh
#	sudo ./setup_ap.sh

#!/bin/bash

# Ensure the script is run with root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script using sudo: sudo $0"
  exit 1
fi

echo "=== 1. Configuring NetworkManager DNS Spoofing ==="
# Ensure NetworkManager's internal dnsmasq folder exists
mkdir -p /etc/NetworkManager/dnsmasq-shared.d

# Force all DNS queries on the hotspot to resolve to your local IP
echo "address=/#/10.5.6.7" > /etc/NetworkManager/dnsmasq-shared.d/captive-portal.conf
echo "DNS redirection configured."

echo "=== 2. Reconfiguring Network Connections ==="
# Clean up any existing hotspot profile
nmcli connection delete "Hotspot" 2>/dev/null

# Create the Wi-Fi profile directly in Access Point (AP) shared mode
nmcli connection add type wifi ifname wlan0 con-name "Hotspot" sside "$(hostname)" mode ap

# Apply your custom configurations, static IP, and credentials while it's down
nmcli connection modify "Hotspot" 802-11-wireless.ssid "$(hostname)"
nmcli connection modify "Hotspot" 802-11-wireless.band bg
nmcli connection modify "Hotspot" 802-11-wireless-security.key-mgmt wpa-psk
nmcli connection modify "Hotspot" 802-11-wireless-security.psk "NMhotspot"
nmcli connection modify "Hotspot" 802-11-wireless-security.pmf 1
nmcli connection modify "Hotspot" ipv4.method shared
nmcli connection modify "Hotspot" ipv4.addresses 10.5.6.7/24
nmcli connection modify "Hotspot" connection.autoconnect yes
nmcli connection modify "Hotspot" connection.autoconnect-priority 5

# Configure your client/fallback network ("TACOCAT")
# (Note: This will still throw an error if "TACOCAT" hasn't been created on this Pi yet)
nmcli connection modify "TACOCAT" connection.autoconnect yes 2>/dev/null || echo "Note: TACOCAT profile not found yet, skipping priority."
nmcli connection modify "TACOCAT" connection.autoconnect-priority 10 2>/dev/null

echo "=== 3. Setting Up Firewall Port Redirection (80 -> 5000) ==="
# Add nftables rules to catch standard HTTP requests and redirect them to port 5000
nft add table ip nat 2>/dev/null || true
nft add chain ip nat prerouting '{ type nat hook prerouting priority dstnat; }' 2>/dev/null || true
nft add rule ip nat prerouting iifname "wlan0" tcp dport 80 redirect to :5000

# Make the firewall rules persistent across system reboots
nft list ruleset > /etc/nftables.conf
systemctl enable nftables --now >/dev/null 2>&1
echo "Firewall rules persistent."

echo "=== 4. Starting the Hotspot ==="
# Bring the newly customized hotspot up cleanly
nmcli connection up "Hotspot"

echo "=== Setup Complete! ==="
echo "Hotspot '$(hostname)' is running at 10.5.6.7."
echo "Traffic on port 80 is automatically routing to port 5000."
echo "You can connect to the hotspot using the password 'NMhotspot'."
