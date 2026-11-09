#!/usr/bin/env bash
# Install FUXA's four optional drivers through the admin API (needs network
# for npm). They land in server/_pkg/runtime/node_modules; ship that _pkg/
# directory for the offline image (see plugin_check.sh).
# Usage: FUXA=http://127.0.0.1:1881 TOKEN=<x-access-token> [ONLY="node-bacnet"] ./install_plugins.sh
# ONLY: space-separated driver names to install (default: all four).
# Versions are the registry defaults of FUXA 1.3.5 (GET /api/plugins).
set -euo pipefail

: "${FUXA:?set FUXA to the base URL}"
: "${TOKEN:?set TOKEN to an admin x-access-token (signin data.token)}"

install() {  # name module type version
  local code body
  if [ -n "${ONLY:-}" ] && [[ " ${ONLY} " != *" $1 "* ]]; then
    return 0
  fi
  # The runtime-install flag is spelled as FUXA spells it.
  body=$(printf '{"params":{"name":"%s","module":"%s","type":"%s","version":"%s","group":"connection-device","dinamic":true}}' "$1" "$2" "$3" "$4") # codespell:ignore dinamic
  code=$(curl -sS -m 900 -o /tmp/fuxa-plugin.out -w '%{http_code}' \
    -H 'Content-Type: application/json' -H "x-access-token: ${TOKEN}" \
    -d "${body}" "${FUXA}/api/plugins")
  echo "$1@$4 -> HTTP ${code} $(cat /tmp/fuxa-plugin.out)"
  [ "${code}" = 200 ]
}

install modbus-serial ./modbus Modbus 8.0.19
install node-opcua ./opcua OPCUA 2.149.0
install node-bacnet ./bacnet BACnet 0.2.4
install node-snap7 ./s7 SiemensS7 1.0.9
echo "PASS: drivers installed"
