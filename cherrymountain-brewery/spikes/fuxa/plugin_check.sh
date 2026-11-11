#!/usr/bin/env bash
# Run ON the FUXA host/container: confirm the optional driver packages are
# present in FUXA's plugin runtime and load with no network (design §12.2).
# Usage: FUXA_DIR=/usr/src/app/FUXA ./plugin_check.sh
# Verified at FUXA 1.3.5: the stock image bakes in NONE of the four drivers.
# FUXA installs them on demand (POST /api/plugins -> npm install) into
# server/_pkg/runtime/node_modules (server/runtime/plugins/npm-runtime-service.js,
# packageDir = server/_pkg set in server/main.js). At boot, every package found
# there (or in server/node_modules) is registered and loaded. Offline recipe:
# install once while online, then ship server/_pkg/ in the image.
set -euo pipefail

FUXA_DIR="${FUXA_DIR:-/usr/src/app/FUXA}"
cd "${FUXA_DIR}/server"
RT="_pkg/runtime/node_modules"
fail=0
for mod in modbus-serial node-opcua node-snap7 node-bacnet; do
  if node -e "
    const p = require('path'), fs = require('fs');
    const dir = [p.resolve('${RT}', '${mod}'), p.resolve('node_modules', '${mod}')].find(fs.existsSync);
    if (!dir) { console.error('not installed'); process.exit(1); }
    const m = require(dir);
    console.log('${mod}', require(p.join(dir, 'package.json')).version, 'loads from', p.relative('.', dir));
  " 2>/tmp/plug.err; then :; else
    echo "${mod}: MISSING or broken ($(head -1 /tmp/plug.err))"; fail=1
  fi
done
if (( fail )); then echo "FAIL"; exit 1; fi
echo "PASS: all four plugins load (run again with the network disabled for the offline case)"
