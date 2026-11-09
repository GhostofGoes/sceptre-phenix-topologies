#!/bin/bash
# phenix startup hook on a sim-* node (cherrymountain app). Startup scripts run
# in name order, so 5-wait-broker.sh (added by the helics app) has already
# waited for sim-ctl. Starts one cm-federate@<name> unit per line of
# /etc/cherrymountain/federates. --no-block: this runs inside phenix.service.
set -euo pipefail

list=/etc/cherrymountain/federates
[ -f "${list}" ] || { echo "cm-federates: ${list} is missing" >&2; exit 1; }

systemctl daemon-reload
while read -r fed; do
  [ -n "${fed}" ] || continue
  # A topology that still injects the older per-federate unit (cm-cellar.service)
  # runs that one; starting both would register the federate name twice.
  if systemctl cat "cm-${fed}.service" >/dev/null 2>&1; then
    echo "cm-federates: cm-${fed}.service exists; not starting cm-federate@${fed}"
    continue
  fi
  systemctl enable --now --no-block "cm-federate@${fed}.service"
done < "${list}"
