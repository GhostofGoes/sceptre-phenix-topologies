#!/bin/bash
# phenix startup hook on every MGMT member (cherrymountain app, DESIGN.md 10.1).
# Loads the MGMT nftables policy before 5-wait-broker.sh pings sim-ctl. The
# ruleset only matches the MGMT interface; the plant NICs are untouched.
set -euo pipefail

rules=/etc/cherrymountain/nftables-mgmt.nft
[ -f "${rules}" ] || { echo "cm-mgmt-nftables: ${rules} is missing" >&2; exit 1; }
nft -c -f "${rules}"
nft -f "${rules}"
echo "cm-mgmt-nftables: loaded ${rules}"
