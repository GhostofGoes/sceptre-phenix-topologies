#!/usr/bin/env bash
# Run ON the FUXA VM: confirm optional driver plugins are baked in and load
# with no network (design §12.2). Usage: FUXA_DIR=/opt/fuxa ./plugin_check.sh
# TODO(verify): where FUXA v1.3.x installs optional drivers (npm packages under
# server/node_modules, or a dedicated plugin dir). The loop checks both.
set -euo pipefail

FUXA_DIR="${FUXA_DIR:-/opt/fuxa}"
cd "${FUXA_DIR}/server"
fail=0
for mod in modbus-serial node-opcua node-snap7 node-bacnet; do
  if node -e "require('${mod}'); console.log('${mod}: loads')" 2>/tmp/plug.err; then :; else
    echo "${mod}: MISSING or broken ($(head -1 /tmp/plug.err))"; fail=1
  fi
done
echo "offline check: blocking registry access, then loading again"
NPM_CONFIG_OFFLINE=true node -e "require('node-snap7'); console.log('node-snap7 native binding ok')" || fail=1
if (( fail )); then echo "FAIL"; exit 1; fi
echo "PASS: all four plugins load"
