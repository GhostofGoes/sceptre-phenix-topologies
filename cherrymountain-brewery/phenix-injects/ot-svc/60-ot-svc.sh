#!/bin/bash
# phenix startup hook on ot-svc (DESIGN.md 11). The ntp app wrote
# /etc/chrony/chrony.conf before boot (server mode: local clock at stratum 10,
# `allow all`). Limit the clients to the plant's prefixes and keep the control
# port on localhost, then start the services whose settings were injected.
# --no-block: this script runs inside phenix.service during boot.
# The marker line makes a reboot without a fresh inject a no-op instead of
# appending a second copy of the block.
conf=/etc/chrony/chrony.conf
marker="restricted by ot-svc startup hook"
if [ -f "$conf" ] && ! grep -qF "$marker" "$conf"; then
  sed -i '/^allow all$/d' "$conf"
  {
    echo "# Clients: DMZ, the routed OT zones, BAS, and SOC (${marker})."
    for net in 10.10.20.0/24 10.20.0.0/16 10.30.10.0/24 10.40.10.0/24; do
      echo "allow $net"
    done
    echo "ratelimit interval 1 burst 16"
    echo "bindcmdaddress 127.0.0.1"
    echo "cmdallow 127.0.0.1"
  } >> "$conf"
  systemctl restart --no-block chrony.service
fi
systemctl enable --now --no-block dnsmasq.service rsyslog.service
