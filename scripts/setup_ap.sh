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
nmcli connection add type wifi ifname wlan0 con-name "Hotspot" ssid "$(hostname)" mode ap

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

echo "=== 3. Configuring Client Network (netplan-wlan0-TACOCAT) ==="
# If the Netplan profile doesn't exist in NetworkManager yet, create it
if ! nmcli connection show "netplan-wlan0-TACOCAT" >/dev/null 2>&1; then
    echo "Creating missing netplan-wlan0-TACOCAT connection profile..."
    nmcli connection add type wifi ifname wlan0 con-name "netplan-wlan0-TACOCAT" ssid "TACOCAT"
fi

# Apply autoconnect settings and set its priority HIGHER than the Hotspot (10 vs 5)
nmcli connection modify "netplan-wlan0-TACOCAT" connection.autoconnect yes
nmcli connection modify "netplan-wlan0-TACOCAT" connection.autoconnect-priority 10

echo "=== 4. Setting Up Firewall Port Redirection (80 -> 5000) ==="
# Add nftables rules to catch standard HTTP requests and redirect them to port 5000
nft add table ip nat 2>/dev/null || true
nft add chain ip nat prerouting '{ type nat hook prerouting priority dstnat; }' 2>/dev/null || true
nft add rule ip nat prerouting iifname "wlan0" tcp dport 80 redirect to :5000

# Make the firewall rules persistent across system reboots
nft list ruleset > /etc/nftables.conf
systemctl enable nftables --now >/dev/null 2>&1
echo "Firewall rules persistent."

# Bring the newly customized hotspot up cleanly
echo "=== Setup Complete! ==="
echo "Hotspot '$(hostname)' is running at 10.5.6.7."
echo "Traffic on port 80 is automatically routing to port 5000."
echo "=== 5. Starting the Hotspot ==="

nmcli connection up "Hotspot"


