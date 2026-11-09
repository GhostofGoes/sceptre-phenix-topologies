#!/bin/bash
# shellcheck shell=bash
# VyOS settings that the phenix vrouter app cannot render (DESIGN.md 10.3).
#
# The vyatta.tmpl rulesets cannot log, and the template has no syslog target,
# no IKE key exchange version, and one NTP server for every router. This script
# adds what the plant needs:
#   fw-edge, fw-ot: log every default-deny drop, and send the logs to Wazuh
#   fw-ot:          take time from ot-svc (the app gives every router the same
#                   NTP server, the one of the first router that has one)
#   fw-edge, util-rtr: IKEv2 for the utility IPsec conduit (the IKE group
#                   otherwise accepts the deprecated IKEv1)
#   fw-edge:        an IPsec policy match on the utility DNP3 rule (any rule whose
#                   source is utility-derms), so a plaintext packet that spoofs
#                   192.0.2.10 from INET is dropped (DESIGN.md 10.1)
#
# --dry-run makes the same changes, prints `compare`, and discards them: no
# commit, no marker. CM_VYOS_TEMPLATE and CM_VYOS_RUN_DIR override the VyOS
# script template and the marker directory (the tests use them).
#
# The topology injects it into the router's persistent config tree and runs it
# with a `commands` entry (cc exec through sg vyattacfg), which minimega
# repeats at every miniccc connect, so it runs once per boot: /tmp is cleared
# at boot, and the marker is written after a successful commit. A lock keeps
# a second run (the next miniccc connect) from opening a parallel configure
# session while the first one is still waiting or committing. If any step fails,
# the rest is still committed, the marker lists the failed steps (so the next
# connect does not loop on them), and the script exits 1 so `cc` shows it.
#
# TODO(verify): unverified on a VyOS VM. Check that the file shows up as
# /config/scripts/custom/cm-vyos-config.sh, that the syslog syntax of the
# build is `remote` or `host` (both are tried), and that `default-log` takes.
#
# VyOS's script-template is not safe under -e or -u.

WAZUH=10.40.10.11
OT_SVC=10.20.30.13
UTILITY_DERMS=192.0.2.10
RUN_DIR=${CM_VYOS_RUN_DIR:-/tmp}
TEMPLATE=${CM_VYOS_TEMPLATE:-/opt/vyatta/etc/functions/script-template}
DONE=$RUN_DIR/cm-vyos-config.done
LOCK=$RUN_DIR/cm-vyos-config.lock

dry_run=0
case "${1:-}" in
  "") ;;
  --dry-run) dry_run=1 ;;
  *)
    echo "usage: $0 [--dry-run]" >&2
    exit 2
    ;;
esac

[ "$dry_run" = 0 ] && [ -e "$DONE" ] && exit 0
exec 9> "$LOCK"
if ! flock -n 9; then
  echo "cm-vyos-config: another run holds $LOCK; leaving it to that one" >&2
  exit 0
fi

# Wait for the vrouter app's boot config, which sets the host name.
host=
for _ in $(seq 1 120); do
  case "$(hostname)" in
    isp-rtr | util-rtr | fw-edge | fw-ot)
      host=$(hostname)
      break
      ;;
  esac
  sleep 5
done
if [ -z "$host" ]; then
  echo "cm-vyos-config: the router never got its host name" >&2
  exit 1
fi

# shellcheck disable=SC1090,SC1091
source "$TEMPLATE"
configure

failed=()
try() {
  if ! "$@"; then
    echo "cm-vyos-config: failed: $*" >&2
    failed+=("$*")
  fi
}

quiet() {
  "$@" 2> /dev/null
}

case "$host" in
  fw-edge | fw-ot)
    for ruleset in $(cli-shell-api listEffectiveNodes firewall ipv4 name | tr -d "'"); do
      try set firewall ipv4 name "$ruleset" default-log
    done
    if ! quiet set system syslog remote "$WAZUH" facility all level warning; then
      try set system syslog host "$WAZUH" facility all level warning
    fi
    ;;
esac

if [ "$host" = fw-ot ]; then
  for server in $(cli-shell-api listEffectiveNodes service ntp server | tr -d "'"); do
    try delete service ntp server "$server"
  done
  try set service ntp server "$OT_SVC" prefer
fi

case "$host" in
  fw-edge | util-rtr)
    try set vpn ipsec ike-group IKE-1W key-exchange ikev2
    ;;
esac

# The vrouter template has no IPsec match, so the utility DNP3 rule (source
# utility-derms) would also pass a plaintext packet that spoofs that address on
# INET. Require that it arrived through the IPsec tunnel. The keyword is
# match-ipsec-in on current builds and match-ipsec on older ones.
if [ "$host" = fw-edge ]; then
  for ruleset in $(cli-shell-api listEffectiveNodes firewall ipv4 name | tr -d "'"); do
    for rule in $(cli-shell-api listEffectiveNodes firewall ipv4 name "$ruleset" rule | tr -d "'"); do
      src=$(cli-shell-api returnEffectiveValue firewall ipv4 name "$ruleset" rule "$rule" source address)
      [ "$src" = "$UTILITY_DERMS" ] || continue
      if ! quiet set firewall ipv4 name "$ruleset" rule "$rule" ipsec match-ipsec-in; then
        try set firewall ipv4 name "$ruleset" rule "$rule" ipsec match-ipsec
      fi
    done
  done
fi

if [ "$dry_run" = 1 ]; then
  compare
  discard
  if [ "${#failed[@]}" -gt 0 ]; then
    printf 'cm-vyos-config: dry run: failed: %s\n' "${failed[@]}" >&2
    exit 1
  fi
  exit 0
fi

if commit; then
  save
  if [ "${#failed[@]}" -eq 0 ]; then
    touch "$DONE"
  else
    printf 'failed: %s\n' "${failed[@]}" > "$DONE"
    echo "cm-vyos-config: committed on $host with ${#failed[@]} failed step(s); see $DONE" >&2
    exit 1
  fi
else
  echo "cm-vyos-config: commit failed on $host" >&2
  exit 1
fi
exit
