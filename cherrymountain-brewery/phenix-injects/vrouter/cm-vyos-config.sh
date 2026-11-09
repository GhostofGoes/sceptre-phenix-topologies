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
#
# The topology injects it into the router's persistent config tree and runs it
# with a `commands` entry (cc exec through sg vyattacfg), which minimega
# repeats at every miniccc connect, so it runs once per boot: /tmp is cleared
# at boot, and the marker is written after a successful commit.
#
# TODO(verify): unverified on a VyOS VM. Check that the file shows up as
# /config/scripts/custom/cm-vyos-config.sh, that the syslog syntax of the
# build is `remote` or `host` (both are tried), and that `default-log` takes.
#
# VyOS's script-template is not safe under -e or -u.

WAZUH=10.40.10.11
OT_SVC=10.20.30.13
DONE=/tmp/cm-vyos-config.done

[ -e "$DONE" ] && exit 0

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

# shellcheck disable=SC1091
source /opt/vyatta/etc/functions/script-template
configure

try() {
  "$@" || echo "cm-vyos-config: failed: $*" >&2
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

if commit; then
  save
  touch "$DONE"
else
  echo "cm-vyos-config: commit failed on $host" >&2
  exit 1
fi
exit
