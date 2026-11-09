#!/bin/bash
# phenix startup hook on dmz-vpn (DESIGN.md 13 and 14). Pins the vendor tunnel to
# dmz-jump on the gateway (the nftables forward policy) and starts the peer watcher
# that writes WireGuard events for Wazuh. It sorts before wireguard-enable.sh, which
# the phenix wireguard app injects to bring up wg-quick@wg0, so the policy is in
# place before the tunnel comes up. --no-block: this script runs inside
# phenix.service during boot.
set -u
if ! nft -f /etc/cherrymountain/nftables-wireguard.nft; then
  echo "70-wireguard: the forward policy did not load; the vendor peer is not pinned" >&2
fi
systemctl daemon-reload
systemctl enable --now --no-block cm-wg-watch.service
