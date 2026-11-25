#!/bin/bash
# phenix startup hook on every MGMT member (cherrymountain app, DESIGN.md 10.1).
# Loads the MGMT nftables policy after 3_interfaces-start.sh has configured the
# NICs and before 5-wait-broker.sh pings sim-ctl. The ruleset only matches the
# MGMT interface; the plant NICs are untouched.
#
# phenix does not rename a guest's NICs: the topology's interface name (mgmt) is
# only a label, and the guest calls the device ens2, eth1, or the like. The
# policy therefore takes the device name as the nft variable mgmt_if, and this
# script finds the device by the MGMT address the topology gave it.
# Env (from /etc/default/cm-mgmt): CM_MGMT_ADDRESS. CM_MGMT_RULES,
# CM_MGMT_SETTINGS, and CM_MGMT_WAIT (seconds to wait for the address, 30)
# override the paths and the wait (the tests use them).
set -euo pipefail

rules=${CM_MGMT_RULES:-/etc/cherrymountain/nftables-mgmt.nft}
settings=${CM_MGMT_SETTINGS:-/etc/default/cm-mgmt}
[ -f "${rules}" ] || { echo "cm-mgmt-nftables: ${rules} is missing" >&2; exit 1; }
[ -f "${settings}" ] || { echo "cm-mgmt-nftables: ${settings} is missing" >&2; exit 1; }
# shellcheck source=/dev/null
. "${settings}"
: "${CM_MGMT_ADDRESS:?set CM_MGMT_ADDRESS}"

# The device that holds the MGMT address (the first field after the index).
mgmt_device() {
  ip -oneline -4 addr show | awk -v want="${CM_MGMT_ADDRESS}" '
    { split($4, cidr, "/"); if (cidr[1] == want) { print $2; exit } }'
}

dev=$(mgmt_device)
for _ in $(seq 1 "${CM_MGMT_WAIT:-30}"); do
  [ -n "${dev}" ] && break
  sleep 1
  dev=$(mgmt_device)
done
if [ -z "${dev}" ]; then
  echo "cm-mgmt-nftables: no interface has ${CM_MGMT_ADDRESS}; the policy was not loaded" >&2
  exit 1
fi

nft -c -D "mgmt_if=${dev}" -f "${rules}"
nft -D "mgmt_if=${dev}" -f "${rules}"
echo "cm-mgmt-nftables: loaded ${rules} on ${dev} (${CM_MGMT_ADDRESS})"
