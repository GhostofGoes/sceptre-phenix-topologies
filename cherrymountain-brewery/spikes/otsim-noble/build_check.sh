#!/usr/bin/env bash
# Build ot-sim from source on Ubuntu 24.04 (noble) and report what works.
# Run as root on a scratch noble VM or container with internet access.
# Usage: OTSIM_REF=main ./build_check.sh   (pin a tag or commit for the result)
# Steps follow the ot-sim README "Building" section.
set -euo pipefail

OTSIM_REF="${OTSIM_REF:-main}"
SRC="${SRC:-/usr/local/src/ot-sim}"
GO_VERSION="${GO_VERSION:-1.21.8}"
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
step apt-deps apt-get install -y build-essential cmake git wget libboost-dev \
  libczmq-dev libxml2-dev libzmq5-dev pkg-config python3-dev python3-pip python3-venv
if [ ! -d "${SRC}/.git" ]; then
  git clone https://github.com/patsec/ot-sim.git "${SRC}" >>"${LOG}" 2>&1
fi
git -C "${SRC}" checkout "${OTSIM_REF}" >>"${LOG}" 2>&1
echo "commit: $(git -C "${SRC}" rev-parse HEAD)"

if ! command -v go >/dev/null; then
  step golang bash -c "wget -qO /tmp/go.tgz https://golang.org/dl/go${GO_VERSION}.linux-amd64.tar.gz && tar -C /usr/local -xzf /tmp/go.tgz && ln -sf /usr/local/go/bin/* /usr/local/bin/"
fi
step submodules git -C "${SRC}" submodule update --init --recursive
step cmake-cpp bash -c "cd ${SRC} && cmake -S . -B build && cmake --build build -j\$(nproc) --target install && ldconfig"
step go-modules make -C "${SRC}/src/go" install
# noble enforces PEP 668; a venv keeps the system Python clean.
python3 -m venv /opt/otsim-venv >>"${LOG}" 2>&1
step python-modules /opt/otsim-venv/bin/pip install "${SRC}/src/python"
# TODO(verify): pydnp3 is what the repo's DNP3 test master uses, and README
# says it fails on Bookworm. Test it separately on noble.
step pydnp3 /opt/otsim-venv/bin/pip install pydnp3

echo
echo "== summary (full log: ${LOG})"
for k in "${!RESULT[@]}"; do printf '%-16s %s\n' "$k" "${RESULT[$k]}"; done | sort
for b in ot-sim-cpu-module ot-sim-modbus-module ot-sim-dnp3-module ot-sim-io-module; do
  # TODO(verify): exact installed binary names; list them with
  # `ls /usr/local/bin | grep ot-sim`.
  command -v "${b}" >/dev/null && echo "binary present: ${b}" || echo "binary absent:  ${b}"
done
find /usr/local/bin -iname "*ot-sim*" 2>/dev/null || true
for v in "${RESULT[@]}"; do [ "${v}" = ok ] || { echo "FAIL: at least one step failed"; exit 1; }; done
echo "PASS: ot-sim builds on noble"
