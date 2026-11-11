#!/bin/vbash
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
# script template and the marker directory, and CM_VYOS_BOOT_STATUS,
# CM_VYOS_BOOT_FLAG, CM_VYOS_WAIT_TRIES, and CM_VYOS_WAIT_SLEEP the two boot
# markers and the wait for them (the tests use all of them).
#
# Where it lives. The topology injects it next to the vrouter app's vyos.script,
# in boot/vyos/rw/config/scripts/custom/ of the image's persistence partition.
# VyOS mounts that partition at /usr/lib/live/mount/persistence and bind-mounts
# /opt/vyatta/etc/config over /config (vyos-1x, src/init/vyos-router), so the
# file is NOT at /config/scripts/custom/ in the running router. The topology's
# `commands` entry (cc exec through sg vyattacfg) runs it from the persistence
# path, the one the image's own boot hook uses (sceptre-phenix-images,
# scripts/vyos/build-vyos.sh). That hook runs only the *.script files there, so
# this .sh file runs through cc alone.
#
# When it runs. miniccc starts with multi-user.target, before VyOS loads its
# boot config, and minimega repeats the `cc exec` at every miniccc connect. So
# the script first waits for two markers: /tmp/vyos-config-status (VyOS has
# loaded its boot config) and phenix-run-flag in this directory (the boot hook
# has committed and saved vyos.script; it stays from the first boot on). The
# router's host name alone is not enough: a commit applies `system host-name`
# first, before the firewall and IPsec nodes that this script reads exist.
# /tmp is cleared at boot, and the done marker is written after a successful
# commit, so the script runs once per boot. A lock keeps a second run (the next
# miniccc connect) from opening a parallel configure session while the first one
# is still waiting or committing. If any step fails, the rest is still
# committed, the marker lists the failed steps (so the next connect does not
# loop on them), and the script exits 1 so `cc` shows it.
#
# TODO(verify): unverified on a VyOS VM. Check that the syslog syntax of the
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
BOOT_STATUS=${CM_VYOS_BOOT_STATUS:-/tmp/vyos-config-status}
BOOT_FLAG=${CM_VYOS_BOOT_FLAG:-$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/phenix-run-flag}
WAIT_TRIES=${CM_VYOS_WAIT_TRIES:-120}
WAIT_SLEEP=${CM_VYOS_WAIT_SLEEP:-5}

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

# Wait until VyOS has loaded its boot config and the image's boot hook has
# committed the vrouter app's vyos.script, which sets the host name.
host=
for _ in $(seq 1 "$WAIT_TRIES"); do
  if [ -e "$BOOT_STATUS" ] && [ -e "$BOOT_FLAG" ]; then
    case "$(hostname)" in
      isp-rtr | util-rtr | fw-edge | fw-ot)
        host=$(hostname)
        break
        ;;
    esac
  fi
  sleep "$WAIT_SLEEP"
done
if [ -z "$host" ]; then
  [ -e "$BOOT_STATUS" ] || echo "cm-vyos-config: VyOS did not finish loading its boot config ($BOOT_STATUS is missing)" >&2
  [ -e "$BOOT_FLAG" ] || echo "cm-vyos-config: the boot hook did not finish the vrouter config ($BOOT_FLAG is missing)" >&2
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
