#!/usr/bin/env bash
# Run upstream's end-to-end test on noble: HELICS broker + OpenDSS federate,
# device-2 (Modbus outstation + HELICS I/O), device-1 (Modbus master + DNP3
# outstation), then the C++ DNP3 master (class 0 scan, CROB trip, scan, CROB
# close, scan). Usage: SRC=/usr/local/src/ot-sim ./e2e_check.sh
# Needs the venv from build_check.sh on PATH and opendssdirect.py~=0.8.4 (the
# repo's CI pin; 0.9.x fails with "Invalid terminal index (0)").
set -euo pipefail

SRC="${SRC:-/usr/local/src/ot-sim}"
export PATH="/opt/otsim-venv/bin:${PATH}"
work=$(mktemp -d)
cp -r "${SRC}/testing/e2e/." "${work}/"
cd "${work}"

pip install -q "opendssdirect.py~=0.8.4"
# data/IEEE13/*.csv are Git LFS objects upstream. A clone without LFS access
# holds 130-byte pointers, the load shape is empty, and every kW reads 0.
csv=helics/data/IEEE13/LoadShape1.csv
if head -c 100 "${csv}" | grep -q 'git-lfs'; then
  echo "LoadShape1.csv is an LFS pointer; substituting a synthetic 1440-point shape"
  python3 -c "
import math
print('\n'.join(f'{0.8 + 0.2 * math.sin(2 * math.pi * i / 1440):.6f}' for i in range(1440)))" > "${csv}"
fi

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT
python3 helics/broker.py >/tmp/e2e-broker.log 2>&1 & pids+=($!)
sleep 2
python3 helics/opendss-federate.py >/tmp/e2e-dss.log 2>&1 & pids+=($!)
ot-sim-cpu-module configs/device-2.xml >/tmp/e2e-dev2.log 2>&1 & pids+=($!)
ot-sim-cpu-module configs/device-1.xml >/tmp/e2e-dev1.log 2>&1 & pids+=($!)
sleep 30   # let devices propagate data (upstream waits 15 s)
rc=0
timeout 120 ot-sim-e2e-dnp3-master >/tmp/e2e-master.log 2>&1 || rc=$?
grep -v '^ms(' /tmp/e2e-master.log || true
# The master exits 0 only if the scans saw closed=1/kW 1-5, closed=0/kW 0, closed=1/kW 1-5.
if [ "${rc}" = 0 ] && ! grep -q segmentation /tmp/e2e-dev2.log; then
  echo "PASS: DNP3 scan, CROB trip, scan, CROB close, scan"
else
  echo "FAIL: master exit ${rc}; see /tmp/e2e-*.log"; exit 1
fi
