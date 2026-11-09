#!/usr/bin/env bash
# Integration test for the ci variant's cellar slice, in Docker (no KVM needed).
#
#   rio-cellar (ot-sim)  <--modbus_master--  plc-cellar (OpenPLC v4.3.2)  <--Modbus--  scada-fuxa (FUXA 1.3.5)
#         ^
#   HELICS broker + the real cherrymountain_sim cellar federate (same container)
#
# Everything is the repo's own artifact: the ot-sim config is rendered from
# plant/generated/ci/phenix/ot-sim.yaml by the phenix-apps ot-sim app, the PLC
# runs phenix-injects/openplc/plc-cellar.zip uploaded by spikes/openplc-v4/
# provision.sh, and FUXA gets phenix-injects/fuxa/scada-fuxa-project.json through
# phenix-injects/fuxa/cm-fuxa-provision.sh. The containers sit on one bridge
# network with the topology's own addresses (rio-cellar 10.20.12.10, plc-cellar
# 10.20.10.12), so no config is edited.
#
# Phases (ci_slice_checks.py): baseline, control, fault, then layout probes that
# upload deliberately different modbus_slave.json files to show the clamp rules.
#
# Needs: docker, git, python3 >= 3.12 with venv, curl, unzip, zip, and network
# access (Docker Hub, GitHub, PyPI, npm). The checks run on the host and reach
# the containers by IP, so use a Linux Docker engine.
#
# Environment:
#   CM_WORK          scratch directory (default: a new mktemp -d directory, printed at
#                    the start and the end; set it to reuse the venv between runs)
#   CM_PHENIX_APPS   sceptre-phenix-apps checkout (default: under CM_WORK; fetched shallow
#                    from GhostofGoes/sceptre-phenix-apps at PHENIX_APPS_COMMIT if absent)
#   CM_EXTRA_CA      PEM bundle of a TLS-inspecting egress proxy; builds CA-aware base images
#                    and builds with --network host and the proxy settings of this shell
#   CM_SKIP_BUILD=1  reuse images that already exist
#   CM_KEEP=1        leave the containers and network running afterwards
#   CM_SKIP_PROBES=1 skip the two layout-probe uploads
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "${here}/../.." && pwd)
work="${CM_WORK:-$(mktemp -d -t cm-ci-slice.XXXXXX)}"
phenix_apps="${CM_PHENIX_APPS:-${work}/sceptre-phenix-apps}"
gen="${root}/plant/generated/ci"
injects="${root}/phenix-injects"

# Pins (see spikes/openplc-v4 and spikes/fuxa for how they were chosen).
OPENPLC_COMMIT=9fb5e1a29b574806f5aea3a4e78202c7a5d658ee   # runtime v4.3.2
# The fork commit the app tests use too (prek.toml, cm-app-tests).
PHENIX_APPS_COMMIT=771e8d87a64770265862468664a1368dcfcbb99d
OPENPLC_IMAGE=cm-openplc:v4.3.2
OTSIM_IMAGE=cm-otsim:noble
FUXA_IMAGE="${CM_FUXA_IMAGE:-frangoteam/fuxa:1.3.5}"
NET=cm-ci-slice
SUBNET=10.20.0.0/16
RIO=rio-cellar
PLC=plc-cellar
FUXA=scada-fuxa
FUXA_IP=10.20.30.10
FUXA_ADMIN_PASSWORD=cm-ci-fuxa-range-only
PLC_USER="admin"
PLC_PASS=cm-ci-openplc-range-only

log() { echo "[ci-slice $(date +%H:%M:%S)] $*"; }
die() { echo "[ci-slice] FAIL: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "$1 is required"; }

for tool in docker git python3 curl unzip zip; do need "${tool}"; done
timeout 20 docker info >/dev/null 2>&1 || die "the Docker daemon is not reachable (start dockerd)"
mkdir -p "${work}/logs"
log "work directory ${work}"

cleanup() {
  local rc=$?
  for c in "${FUXA}" "${PLC}" "${RIO}"; do
    docker logs "${c}" >"${work}/logs/${c}.log" 2>&1 || true
  done
  docker exec "${RIO}" sh -c 'cat /var/log/cm-*.log' >"${work}/logs/rio-stack.log" 2>&1 || true
  if [ "${CM_KEEP:-0}" != 1 ]; then
    docker rm -f "${FUXA}" "${PLC}" "${RIO}" >/dev/null 2>&1 || true
    docker network rm "${NET}" >/dev/null 2>&1 || true
  fi
  if [ "${rc}" = 0 ]; then log "PASS (logs in ${work}/logs)"; else log "FAILED rc=${rc} (logs in ${work}/logs)"; fi
}
trap cleanup EXIT

# --- addresses come from the generated files, not from this script ----------
read -r RIO_IP < <(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["config"]["host"])' \
  "${gen}/openplc/plc-cellar/modbus_master.json")
read -r PLC_IP < <(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1]))["devices"]; print(next(iter(d.values()))["property"]["address"])' \
  "${injects}/fuxa/scada-fuxa-project.json")
log "rio-cellar ${RIO_IP}, plc-cellar ${PLC_IP}, scada-fuxa ${FUXA_IP}"

# --- 1. host venv: phenix-apps (renders ot-sim), pymodbus, requests -----------
if [ ! -d "${phenix_apps}/.git" ]; then
  log "fetching sceptre-phenix-apps ${PHENIX_APPS_COMMIT:0:12} (shallow, read-only use)"
  mkdir -p "${phenix_apps}"
  if ! { git -C "${phenix_apps}" init -q \
      && timeout 300 git -C "${phenix_apps}" fetch -q --depth 1 \
        https://github.com/GhostofGoes/sceptre-phenix-apps.git "${PHENIX_APPS_COMMIT}" \
      && git -C "${phenix_apps}" checkout -q FETCH_HEAD; }; then
    log "fetch failed; the ot-sim config will be hand-rendered"
  fi
fi
if [ ! -x "${work}/venv/bin/python" ]; then
  log "creating the test venv"
  python3 -m venv "${work}/venv"
fi
py="${work}/venv/bin/python"
timeout 600 "${work}/venv/bin/pip" install -q --disable-pip-version-check \
  'pymodbus==3.11.4' 'requests==2.32.5' 'pyyaml==6.0.3' 'pydantic==2.14.3' 'pytest==9.2.0' >/dev/null
render_mode=app
if [ -d "${phenix_apps}/src/python" ] \
  && timeout 600 "${work}/venv/bin/pip" install -q --disable-pip-version-check "${phenix_apps}/src/python" >/dev/null 2>&1 \
  && "${py}" -c 'import phenix_apps.apps.otsim.app' >/dev/null 2>&1; then
  render_flag=()
else
  render_mode=hand
  render_flag=(--hand-render)
fi
log "rendering the ot-sim config (${render_mode}-rendered: $([ "${render_mode}" = app ] && echo 'phenix_apps.apps.otsim.app pre_start' || echo 'render_otsim.hand_render'))"
rm -rf "${work}/otsim"
"${py}" -I "${here}/render_otsim.py" --ot-sim-yaml "${gen}/phenix/ot-sim.yaml" \
  --rio-address "${RIO_IP}" --broker-address 127.0.0.1 --out "${work}/otsim" "${render_flag[@]}" \
  >"${work}/logs/render.log" 2>&1 || { cat "${work}/logs/render.log"; die "ot-sim render failed"; }
tail -n 1 "${work}/logs/render.log"
"${py}" -m pytest -q "${here}/test_render_otsim.py" 2>&1 | tail -n 1

# --- 2. images ---------------------------------------------------------------
build_args=()
bookworm_base=debian:bookworm-slim
noble_base=ubuntu:24.04
if [ -n "${CM_EXTRA_CA:-}" ]; then
  [ -r "${CM_EXTRA_CA}" ] || die "CM_EXTRA_CA=${CM_EXTRA_CA} is not readable"
  build_args=(--network host --build-arg "HTTPS_PROXY=${HTTPS_PROXY:-}" --build-arg "https_proxy=${HTTPS_PROXY:-}")
fi
image_exists() { docker image inspect "$1" >/dev/null 2>&1; }

build_images() {
  if [ -n "${CM_EXTRA_CA:-}" ]; then
    local ctx="${work}/ca-ctx"
    mkdir -p "${ctx}"
    cp "${CM_EXTRA_CA}" "${ctx}/ca.crt"
    cp "${here}/Dockerfile.ca" "${ctx}/Dockerfile"
    timeout 600 docker build -q --network host --build-arg BASE=debian:bookworm-slim -t cm-base-bookworm "${ctx}" >/dev/null
    timeout 600 docker build -q --network host --build-arg BASE=ubuntu:24.04 -t cm-base-noble "${ctx}" >/dev/null
    bookworm_base=cm-base-bookworm
    noble_base=cm-base-noble
  fi
  if ! image_exists "${OPENPLC_IMAGE}"; then
    log "building OpenPLC runtime v4.3.2 (${OPENPLC_COMMIT:0:7}) from source"
    if [ ! -d "${work}/openplc-runtime/.git" ]; then
      timeout 600 git clone --quiet https://github.com/Autonomy-Logic/openplc-runtime.git "${work}/openplc-runtime"
    fi
    git -C "${work}/openplc-runtime" checkout --quiet "${OPENPLC_COMMIT}"
    sed "s#^FROM debian:bookworm-slim#FROM ${bookworm_base}#" "${work}/openplc-runtime/Dockerfile" >"${work}/Dockerfile.openplc"
    timeout 1800 docker build -q "${build_args[@]}" --build-arg RUNTIME_VERSION=v4.3.2 \
      -f "${work}/Dockerfile.openplc" -t "${OPENPLC_IMAGE}" "${work}/openplc-runtime" >/dev/null
  fi
  if ! image_exists "${OTSIM_IMAGE}"; then
    log "building ot-sim on noble (Go, C, and Python modules; a few minutes)"
    timeout 1800 docker build -q "${build_args[@]}" --build-arg "BASE=${noble_base}" \
      -f "${here}/Dockerfile.otsim" -t "${OTSIM_IMAGE}" "${here}" >/dev/null
  fi
  image_exists "${FUXA_IMAGE}" || timeout 600 docker pull -q "${FUXA_IMAGE}" >/dev/null
}
if [ "${CM_SKIP_BUILD:-0}" = 1 ]; then log "CM_SKIP_BUILD=1, reusing images"; else build_images; fi
for img in "${OPENPLC_IMAGE}" "${OTSIM_IMAGE}" "${FUXA_IMAGE}"; do image_exists "${img}" || die "image ${img} is missing"; done
log "images: $(docker image inspect -f '{{index .RepoTags 0}}={{.Id}}' "${OPENPLC_IMAGE}" "${OTSIM_IMAGE}" "${FUXA_IMAGE}" | sed 's/sha256://' | cut -c1-70 | tr '\n' ' ')"

# --- 3. network and containers -----------------------------------------------
docker rm -f "${FUXA}" "${PLC}" "${RIO}" >/dev/null 2>&1 || true
docker network rm "${NET}" >/dev/null 2>&1 || true
docker network create --subnet "${SUBNET}" --gateway 10.20.0.1 "${NET}" >/dev/null \
  || die "cannot create ${SUBNET} (does it overlap a host route?)"

rio_mounts=(
  -v "${here}:/cm/t:ro"
  -v "${work}/otsim/rio-cellar.xml:/cm/ot-sim.xml:ro"
  -v "${gen}/helics.json:/cm/helics.json:ro"
  -v "${root}/sim:/cm/sim:ro"
  -v "${here}/fault_federate.py:/cm/fault_federate.py:ro"
)
log "starting ${RIO}"
docker run -d --name "${RIO}" --network "${NET}" --ip "${RIO_IP}" --hostname "${RIO}" "${rio_mounts[@]}" "${OTSIM_IMAGE}" >/dev/null
timeout 120 docker exec "${RIO}" bash /cm/t/rio_start.sh real

log "starting ${PLC} (OpenPLC v4.3.2; --cap-add sys_nice lets the PLC thread use SCHED_FIFO)"
docker run -d --name "${PLC}" --network "${NET}" --ip "${PLC_IP}" --hostname "${PLC}" --cap-add sys_nice "${OPENPLC_IMAGE}" >/dev/null

log "starting ${FUXA}"
rm -rf "${work}/fuxa" && mkdir -p "${work}/fuxa/appdata"
cp "${injects}/fuxa/mysettings.json" "${work}/fuxa/appdata/mysettings.json"
fuxa_extra=()
if [ -n "${CM_EXTRA_CA:-}" ]; then
  fuxa_extra=(-v "${CM_EXTRA_CA}:/ca.crt:ro" -e NODE_EXTRA_CA_CERTS=/ca.crt -e npm_config_cafile=/ca.crt)
fi
docker run -d --name "${FUXA}" --network "${NET}" --ip "${FUXA_IP}" --hostname "${FUXA}" \
  -v "${work}/fuxa/appdata:/usr/src/app/FUXA/server/_appdata" "${fuxa_extra[@]}" "${FUXA_IMAGE}" >/dev/null

# --- 4. provision the PLC and FUXA --------------------------------------------
wait_http() {  # url seconds
  local end=$((SECONDS + $2))
  until curl -ksS -m 5 -o /dev/null "$1" 2>/dev/null; do
    [ "${SECONDS}" -lt "${end}" ] || die "$1 did not come up"
    sleep 2
  done
}
provision_plc() {  # zip
  PLC="${PLC_IP}" ZIP="$1" USER_NAME="${PLC_USER}" USER_PASS="${PLC_PASS}" TIMEOUT=600 CLEAN=0 \
    timeout 900 bash "${root}/spikes/openplc-v4/provision.sh" >"${work}/logs/provision-plc.log" 2>&1 \
    || { tail -n 30 "${work}/logs/provision-plc.log"; die "OpenPLC provisioning failed"; }
  grep -E 'compile_seconds|PASS' "${work}/logs/provision-plc.log" | tr '\n' ' '; echo
}
wait_http "https://${PLC_IP}:8443/api/version" 180
log "uploading plc-cellar.zip with spikes/openplc-v4/provision.sh"
provision_plc "${injects}/openplc/plc-cellar.zip"

wait_http "http://${FUXA_IP}:1881/api/version" 120
log "installing FUXA's Modbus driver (modbus-serial 8.0.19) with the admin API, as spikes/fuxa/install_plugins.sh does"
token=$(curl -sS -m 20 -H 'Content-Type: application/json' -d '{"username":"admin","password":"123456"}' \
  "http://${FUXA_IP}:1881/api/signin" | "${py}" -I -c 'import json,sys; print(json.load(sys.stdin)["data"]["token"])')
# The last key is the runtime-install flag, spelled as FUXA's plugins API expects.
plugin='{"params":{"name":"modbus-serial","module":"./modbus","type":"Modbus","version":"8.0.19","group":"connection-device","dinamic":true}}' # codespell:ignore dinamic
code=$(curl -sS -m 600 -o "${work}/logs/fuxa-plugin.out" -w '%{http_code}' -H 'Content-Type: application/json' \
  -H "x-access-token: ${token}" -d "${plugin}" "http://${FUXA_IP}:1881/api/plugins")
[ "${code}" = 200 ] || die "FUXA plugin install HTTP ${code}: $(cat "${work}/logs/fuxa-plugin.out")"
log "provisioning FUXA with phenix-injects/fuxa/cm-fuxa-provision.sh"
FUXA_URL="http://${FUXA_IP}:1881" FUXA_ADMIN_PASSWORD="${FUXA_ADMIN_PASSWORD}" \
  FUXA_PROJECT="${injects}/fuxa/scada-fuxa-project.json" timeout 300 bash "${injects}/fuxa/cm-fuxa-provision.sh" 2>&1 | tail -n 2

check() {  # phase [extra args]
  timeout 600 "${py}" -I "${here}/ci_slice_checks.py" "$@" \
    --rio "${RIO_IP}" --plc "${PLC_IP}" --fuxa "http://${FUXA_IP}:1881" --plc-container "${PLC}" \
    --fuxa-password "${FUXA_ADMIN_PASSWORD}" \
    --registers "${gen}/registers.json" --project "${injects}/fuxa/scada-fuxa-project.json"
}

log "letting the plant, the PLC scan, and FUXA's 1 s poll settle"
sleep 25

# --- 5. assertions -------------------------------------------------------------
check baseline
check control

log "fault phase: restarting ${RIO} with the fault federate (FV-101 head pressure 27.0 psia)"
docker restart "${RIO}" >/dev/null
sleep 3
timeout 120 docker exec "${RIO}" bash /cm/t/rio_start.sh fault
check fault

# --- 6. layout probes: the runtime's clamp and segment rules --------------------
probe_zip() {  # out.zip qw mw   (counts for modbus_slave.json; the tight values are in the real ZIP)
  local dir="${work}/probe" ; rm -rf "${dir}"; mkdir -p "${dir}"
  unzip -q "${injects}/openplc/plc-cellar.zip" -d "${dir}"
  "${py}" -I - "$2" "$3" "${dir}/conf/modbus_slave.json" <<'PY'
import json, sys
qw, mw, path = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
cfg = json.load(open(path))
cfg["buffer_mapping"]["holding_registers"].update(qw_count=qw, mw_count=mw)
if qw == 1024:  # the v3 map: 1024 of everything
    cfg["buffer_mapping"]["coils"]["qx_bits"] = 8192
    cfg["buffer_mapping"]["discrete_inputs"]["ix_bits"] = 8192
    cfg["buffer_mapping"]["input_registers"]["iw_count"] = 1024
json.dump(cfg, open(path, "w"), indent=2)
PY
  (cd "${dir}" && rm -f "$1" && zip -qr -X "$1" .)
}
if [ "${CM_SKIP_PROBES:-0}" != 1 ]; then
  docker restart "${RIO}" >/dev/null; sleep 2
  timeout 120 docker exec "${RIO}" bash /cm/t/rio_start.sh real
  log "probe 1: the v3 buffer_mapping (1024 of everything) on the same program"
  probe_zip "${work}/probe-v3.zip" 1024 1024
  provision_plc "${work}/probe-v3.zip"; sleep 5
  check layout-probe --mode clamp
  log "probe 2: qw_count=10"
  probe_zip "${work}/probe-qw10.zip" 10 8
  provision_plc "${work}/probe-qw10.zip"; sleep 5
  check layout-probe --mode shifted
  log "restoring the real ZIP and re-running the baseline"
  provision_plc "${injects}/openplc/plc-cellar.zip"; sleep 20
  check baseline
fi
log "all phases passed"
