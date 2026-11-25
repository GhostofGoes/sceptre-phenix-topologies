#!/bin/bash
# phenix startup hook on dmz-vpn (DESIGN.md 13 and 14). Pins the vendor tunnel to
# dmz-jump on the gateway (the nftables forward policy) and starts the peer watcher
# that writes WireGuard events for Wazuh. It sorts before wireguard-enable.sh, which
# the phenix wireguard app injects to bring up wg-quick@wg0, so the policy is in
# place before the tunnel comes up. --no-block: this script runs inside
# phenix.service during boot.
#
# Forwarding is off in the image and comes on here, only once the policy has loaded:
# a policy that fails to load leaves a gateway that forwards nothing, not one that
# lets the vendor peer reach the whole DMZ. The same holds on a reboot, when
# systemd starts wg-quick@wg0 by itself, before this hook has run.
set -u
if nft -f /etc/cherrymountain/nftables-wireguard.nft; then
  sysctl -qw net.ipv4.ip_forward=1
else
  sysctl -qw net.ipv4.ip_forward=0
  echo "70-wireguard: the forward policy did not load; forwarding stays off and the vendor tunnel reaches nothing" >&2
fi
systemctl daemon-reload
systemctl enable --now --no-block cm-wg-watch.service
