#!/usr/bin/env bash
# Build ot-sim from source on Ubuntu 24.04 (noble) and report what works.
# Run as root on a scratch noble VM or container with internet access.
# Usage: OTSIM_REF=main ./build_check.sh   (pin a tag or commit for the result)
# Steps follow the ot-sim README "Building" section.
set -euo pipefail

OTSIM_REF="${OTSIM_REF:-main}"
SRC="${SRC:-/usr/local/src/ot-sim}"
LOG="${LOG:-/var/tmp/otsim-noble-build.log}"
declare -A RESULT

step() {  # name command...
  local name="$1"; shift
  echo "== ${name}"
  if "$@" >>"${LOG}" 2>&1; then RESULT[${name}]=ok; else RESULT[${name}]=FAIL; fi
  echo "   ${RESULT[${name}]}"
}

: >"${LOG}"
export DEBIAN_FRONTEND=noninteractive
step apt-update apt-get update
step apt-deps apt-get install -y build-essential cmake git wget ca-certificates \
  libboost-dev libczmq-dev libxml2-dev libzmq5-dev pkg-config python3-dev \
  python3-pip python3-venv golang-go
if [ ! -d "${SRC}/.git" ]; then
  git clone https://github.com/patsec/ot-sim.git "${SRC}" >>"${LOG}" 2>&1
fi
git -C "${SRC}" checkout "${OTSIM_REF}" >>"${LOG}" 2>&1
echo "commit: $(git -C "${SRC}" rev-parse HEAD)"

# golang-go on noble is 1.22.x, which satisfies src/go/go.mod (go 1.22.0);
# the README's go1.21.8 tarball would make the toolchain download 1.22 anyway.
go version >>"${LOG}" 2>&1 || true
step submodules git -C "${SRC}" submodule update --init --recursive
step cmake-cpp bash -c "cd ${SRC} && cmake -S . -B build -DBUILD_E2E=ON && cmake --build build -j\$(nproc) --target install && ldconfig"
step go-modules make -C "${SRC}/src/go" install
# noble enforces PEP 668; a venv keeps the system Python clean.
python3 -m venv /opt/otsim-venv >>"${LOG}" 2>&1
step python-modules /opt/otsim-venv/bin/pip install "${SRC}/src/python"
# The ot-sim I/O module (HELICS federate) segfaults on noble when pyzmq's wheel
# bundles its own libzmq next to HELICS's bundled libzmq (gdb: SIGSEGV in a
# "ZMQbg/IO" thread; upstream's e2e workflow notes the same on ubuntu-latest).
# Building pyzmq from source links it to the system libzmq and avoids the crash.
step pyzmq-system /opt/otsim-venv/bin/pip install --force-reinstall --no-deps --no-binary pyzmq pyzmq
# pydnp3 is only needed by the legacy testing/dnp3/master.py; the C++
# ot-sim-e2e-dnp3-master (built above with -DBUILD_E2E=ON) replaces it.
# Informational: a failure here does not fail the script.
if /opt/otsim-venv/bin/pip install pydnp3 >>"${LOG}" 2>&1; then PYDNP3=ok; else PYDNP3=FAIL; fi

echo
echo "== summary (full log: ${LOG})"
printf '%-16s %s\n' "pydnp3(info)" "${PYDNP3}"
for k in "${!RESULT[@]}"; do printf '%-16s %s\n' "$k" "${RESULT[$k]}"; done | sort
# C++ and Go modules install to /usr/local/bin; the Python console scripts
# (ot-sim-io-module, ...) land in the venv's bin.
for b in ot-sim-cpu-module ot-sim-message-bus ot-sim-modbus-module ot-sim-dnp3-module \
  ot-sim-e2e-dnp3-master ot-sim-io-module; do
  if command -v "${b}" >/dev/null || [ -x "/opt/otsim-venv/bin/${b}" ]; then
    echo "binary present: ${b}"
  else
    echo "binary absent:  ${b}"
  fi
done
find /usr/local/bin -iname "*ot-sim*" 2>/dev/null || true
for v in "${RESULT[@]}"; do [ "${v}" = ok ] || { echo "FAIL: at least one step failed"; exit 1; }; done
echo "PASS: ot-sim builds on noble"
