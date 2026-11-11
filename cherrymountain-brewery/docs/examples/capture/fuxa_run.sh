#!/usr/bin/env bash
# Start and stop what the FUXA captures need: the stand-in plant and a real FUXA
# server running the range's generated project.
#
#   fuxa_run.sh up scada|bas SNAPSHOT.json [live]   start the plant and FUXA, provision
#   fuxa_run.sh snapshot SNAPSHOT.json              serve another snapshot
#   fuxa_run.sh down                                stop both
#
# Environment:
#   FUXA_SERVER  FUXA 1.3.6's server directory, with its node_modules (required)
#   FUXA_PKG     a directory with node_modules holding node-bacnet and node-snap7
#                (required; `npm install node-bacnet@0.2.4 node-snap7@1.0.9` in it)
#   CM_ROOT      the cherrymountain-brewery directory (default: this file's place)
#   WORK         scratch directory (default /tmp/cm-examples)
#   CM_BACNET_IFACE  a non-loopback IPv4 address of this host (default: found)
#   FUXA_PORT    default 11881 for scada, 11882 for bas
# "live" runs the brewhouse batch handshake (see fuxa_plant.mjs).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CM_ROOT=${CM_ROOT:-$(cd "${HERE}/../../.." && pwd)}
WORK=${WORK:-/tmp/cm-examples}
: "${FUXA_SERVER:?set FUXA_SERVER}" "${FUXA_PKG:?set FUXA_PKG}"
PY=${PYTHON:-python3}
CONTROL=18181
# Range-only passwords of this throwaway FUXA.
ADMIN_PW='Adm1n-example-pw'
OPERATOR_PW='Oper8tor-example-pw'
mkdir -p "${WORK}"

pid_of() { cat "${WORK}/$1.pid" 2>/dev/null || true; }

down() {
  local n p
  for n in fuxa plant; do
    p=$(pid_of "${n}")
    if [ -n "${p}" ] && kill -0 "${p}" 2>/dev/null; then
      kill "${p}" 2>/dev/null || true
      # Wait for it: a FUXA that is still shutting down would keep the port, and
      # the next one would not start (and the page would be the old one).
      for _ in $(seq 1 30); do kill -0 "${p}" 2>/dev/null || break; sleep 0.5; done
      kill -9 "${p}" 2>/dev/null || true
    fi
    rm -f "${WORK}/${n}.pid"
  done
  pkill -f "${HERE}/fuxa_s7.cjs" 2>/dev/null || true
  sleep 1
}

up() {
  local mode=$1 snap=$2 live=${3:-}
  local port=${FUXA_PORT:-$([ "${mode}" = bas ] && echo 11882 || echo 11881)}
  local ud="${WORK}/user-${mode}"
  down
  rm -rf "${ud:?}"
  mkdir -p "${ud}/_appdata" "${ud}/_images/Brand" "${ud}/_pkg/runtime"
  local iface=${CM_BACNET_IFACE:-$("${PY}" -c '
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("192.0.2.1", 9))
print(s.getsockname()[0])')}
  "${PY}" "${HERE}/fuxa_prep.py" "${CM_ROOT}/fuxa/full/${mode}-fuxa-project.json" "${WORK}/${mode}-project.json" "${iface}"

  # The stand-in plant.
  local flags=(); [ "${live}" = live ] && flags=(--live-batch)
  node "${HERE}/fuxa_plant.mjs" --root "${CM_ROOT}" --fuxa "${FUXA_SERVER}" --pkg "${FUXA_PKG}" \
    --project "${WORK}/${mode}-project.json" --snapshot "${snap}" --control "${CONTROL}" "${flags[@]}" \
    > "${WORK}/plant.log" 2>&1 &
  echo $! > "${WORK}/plant.pid"
  for _ in $(seq 1 60); do grep -q 'control up' "${WORK}/plant.log" && break; sleep 1; done
  grep -q 'control up' "${WORK}/plant.log" || { cat "${WORK}/plant.log"; echo "plant did not start" >&2; exit 1; }

  # FUXA: the repo's settings (secure mode on) with this port, the brand's mark where
  # the menu logo is read, and the two driver packages where FUXA looks for them.
  "${PY}" - "${CM_ROOT}/phenix-injects/fuxa/mysettings.json" "${ud}/_appdata/mysettings.json" "${port}" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
s["uiPort"] = int(sys.argv[3])
s["secretCode"] = "example-jwt-secret-range-only"
json.dump(s, open(sys.argv[2], "w"))
PY
  cp "${CM_ROOT}/brand/icon.svg" "${ud}/_images/Brand/icon.svg"
  cp "${FUXA_PKG}/package.json" "${ud}/_pkg/runtime/package.json"
  ln -s "${FUXA_PKG}/node_modules" "${ud}/_pkg/runtime/node_modules"
  if curl -sS -m 2 -o /dev/null "http://127.0.0.1:${port}/api/version" 2>/dev/null; then
    echo "port ${port} is in use by another FUXA" >&2; exit 1
  fi
  (cd "${FUXA_SERVER}" && exec node main.js --userDir "${ud}" --port "${port}") > "${WORK}/fuxa-${mode}.log" 2>&1 &
  echo $! > "${WORK}/fuxa.pid"
  for _ in $(seq 1 60); do curl -sS -m 2 -o /dev/null "http://127.0.0.1:${port}/api/version" 2>/dev/null && break; sleep 1; done

  # The range's own provisioning script: admin password, operator account, project import.
  FUXA_URL="http://127.0.0.1:${port}" FUXA_ADMIN_PASSWORD="${ADMIN_PW}" FUXA_OPERATOR_PASSWORD="${OPERATOR_PW}" \
    FUXA_PROJECT="${WORK}/${mode}-project.json" \
    bash "${CM_ROOT}/phenix-injects/fuxa/cm-fuxa-provision.sh" | tail -3
  echo "FUXA ${mode}: http://127.0.0.1:${port} (operator password in this script)"
}

case "${1:-}" in
  up) up "$2" "$3" "${4:-}" ;;
  snapshot) curl -sS -m 10 "http://127.0.0.1:${CONTROL}/snapshot?file=$2" | head -c 200; echo ;;
  down) down ;;
  *) sed -n 2,18p "${BASH_SOURCE[0]}"; exit 2 ;;
esac
