#!/usr/bin/env bash
# Host-side checks for the autotunnel port plan (DESIGN §15.3).
#   ./check_ports.sh before   -> no planned host port is already in use
#   ./check_ports.sh after    -> every planned port answers, with the right banner
# Env: HOST=127.0.0.1, PLAN="11881:scada-fuxa 11882:bas-fuxa ..." (port:vm)
set -euo pipefail

mode="${1:?before|after}"
HOST="${HOST:-127.0.0.1}"
PLAN="${PLAN:-11881:scada-fuxa 11882:bas-fuxa 13000:historian 13001:sim-ctl 18080:sim-ctl 19200:sim-ctl 18443:malcolm 18444:wazuh}"

bad=0
for entry in ${PLAN}; do
  port="${entry%%:*}"; vm="${entry#*:}"
  case "${mode}" in
    before)
      if ss -ltnH "sport = :${port}" | grep -q .; then echo "CONFLICT  ${port} (${vm}) already listening"; bad=1; else echo "free      ${port} (${vm})"; fi ;;
    after)
      body=$(curl -sS -m 5 "http://${HOST}:${port}/" 2>&1 | head -c 80 || true)
      if [ -n "${body}" ]; then echo "reachable ${port} (${vm}): ${body}"; else echo "NO REPLY  ${port} (${vm})"; bad=1; fi ;;
    *) echo "unknown mode"; exit 2 ;;
  esac
done
# phenix's own UI port must not be in the plan.
case " ${PLAN} " in *" 3000:"*) echo "CONFLICT  3000 is the phenix UI"; bad=1 ;; esac
exit "${bad}"
