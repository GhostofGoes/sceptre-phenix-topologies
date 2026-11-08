#!/usr/bin/env bash
# shellcheck shell=bash
# Run ON a VyOS 1.5 router with vbash to test whether IPsec commands commit:
#   vbash ./commit_check.sh generated.sh
#       applies the `set`/`delete` lines of the script phenix generated
#   vbash ./commit_check.sh --manual LOCAL PEER LOCAL_NET REMOTE_NET PSK
#       applies a hand-written known-good 1.5 set (the patched-template shape)
# Reports the failing command on a commit error and the SA state afterwards.
# TODO(verify): where phenix leaves the generated vbash script on the image
# (look under /opt/vyatta/etc/config/scripts/ or the phenix startup directory).
# TODO(verify): whether 1.5 needs `ike-group ... key-exchange ikev2` and
# `esp-group ... pfs` spelled as in manual_cmds below.
set -euo pipefail

# shellcheck disable=SC1091
source /opt/vyatta/etc/functions/script-template

manual_cmds() {
  local l="$1" p="$2" ln="$3" rn="$4" psk="$5"
  cat <<CMDS
set vpn ipsec esp-group ESP-1W lifetime 1800
set vpn ipsec esp-group ESP-1W mode tunnel
set vpn ipsec esp-group ESP-1W pfs enable
set vpn ipsec esp-group ESP-1W proposal 1 encryption aes256
set vpn ipsec esp-group ESP-1W proposal 1 hash sha1
set vpn ipsec ike-group IKE-1W key-exchange ikev2
set vpn ipsec ike-group IKE-1W lifetime 3600
set vpn ipsec ike-group IKE-1W proposal 1 encryption aes256
set vpn ipsec ike-group IKE-1W proposal 1 hash sha1
set vpn ipsec interface eth0
set vpn ipsec authentication psk PSK-KEY id '${l}'
set vpn ipsec authentication psk PSK-KEY id '${p}'
set vpn ipsec authentication psk PSK-KEY secret '${psk}'
set vpn ipsec site-to-site peer ${p} authentication mode pre-shared-secret
set vpn ipsec site-to-site peer ${p} authentication local-id '${l}'
set vpn ipsec site-to-site peer ${p} authentication remote-id '${p}'
set vpn ipsec site-to-site peer ${p} connection-type initiate
set vpn ipsec site-to-site peer ${p} default-esp-group ESP-1W
set vpn ipsec site-to-site peer ${p} ike-group IKE-1W
set vpn ipsec site-to-site peer ${p} local-address ${l}
set vpn ipsec site-to-site peer ${p} remote-address ${p}
set vpn ipsec site-to-site peer ${p} tunnel 0 local prefix ${ln}
set vpn ipsec site-to-site peer ${p} tunnel 0 remote prefix ${rn}
CMDS
}

if [ "${1:-}" = "--manual" ]; then
  [ $# -eq 6 ] || { echo "usage: --manual LOCAL PEER LOCAL_NET REMOTE_NET PSK"; exit 2; }
  cmds=$(manual_cmds "$2" "$3" "$4" "$5" "$6")
else
  [ -r "${1:?generated script or --manual}" ] || { echo "cannot read $1"; exit 2; }
  cmds=$(grep -E '^(set|delete) ' "$1")
fi

configure
n=0
while IFS= read -r line; do
  n=$((n + 1))
  if ! eval "${line}" 2>/tmp/vyos-set.err; then
    echo "FAIL at command ${n}: ${line}"; cat /tmp/vyos-set.err; discard; exit 1
  fi
done <<<"${cmds}"
echo "applied ${n} commands, committing"
if ! commit 2>&1 | tee /tmp/vyos-commit.out; then
  echo "FAIL: commit rejected"; discard; exit 1
fi
if grep -qiE 'error|invalid|not valid' /tmp/vyos-commit.out; then
  echo "FAIL: commit printed errors (see above)"; discard; exit 1
fi
save
echo "PASS: commit succeeded"
run show vpn ipsec sa || true
