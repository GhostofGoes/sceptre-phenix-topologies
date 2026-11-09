#!/usr/bin/env bash
# Provision the OpenPLC v4 runtime on plc-cellar (DESIGN.md 12.1, steps 3-5):
#   repack ZIP with plugin configs -> create-user -> login -> upload-file ->
#   poll compilation-status -> start-plc -> confirm STATUS:RUNNING.
# Follows spikes/openplc-v4/provision.sh. Installed as
# /usr/local/bin/cm-openplc-provision and run by cm-openplc-provision.service.
#
# Inputs (paths are injected by the topology):
#   ZIP   $CM_PLC_ZIP   program ZIP built in the OpenPLC Editor
#   CONF  $CM_PLC_CONF  directory of plugin JSON files (modbus_master.json, ...)
# Env (from /etc/default/cm-openplc): OPENPLC_USER, OPENPLC_PASSWORD,
# OPENPLC_PORT, OPENPLC_COMPILE_TIMEOUT.
# The runtime serves a self-signed certificate, so curl runs with -k.
# Re-running is safe: on later boots the runtime restarts the program on its
# own, and this script exits early once it reports STATUS:RUNNING.
set -euo pipefail

ZIP="${CM_PLC_ZIP:-/opt/cherrymountain/openplc/plc-cellar.zip}"
CONF="${CM_PLC_CONF:-/opt/cherrymountain/openplc/conf}"
USER_NAME="${OPENPLC_USER:-admin}"
USER_PASS="${OPENPLC_PASSWORD:?set OPENPLC_PASSWORD}"
PORT="${OPENPLC_PORT:-8443}"
TIMEOUT="${OPENPLC_COMPILE_TIMEOUT:-900}"
STATE=/var/lib/cm-openplc
BASE="https://127.0.0.1:${PORT}/api"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

log() { echo "cm-openplc-provision: $*"; }
api() { curl -ksS --max-time 60 "$@"; }
# Print a top-level JSON field, or nothing when the reply is not a JSON object
# (a transient error page during compile); callers treat empty as "retry".
json_field() {
  python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
except ValueError:
    doc = {}
print(doc.get(sys.argv[1], "") if isinstance(doc, dict) else "")
' "$1"
}

# The credentials JSON, built by python from the environment and sent on stdin,
# so the password never appears on a command line (ps, /proc/<pid>/cmdline).
creds_json() {
  CM_USER="${USER_NAME}" CM_PASS="${USER_PASS}" python3 -c '
import json, os
print(json.dumps({"username": os.environ["CM_USER"], "password": os.environ["CM_PASS"]}))'
}

login() {
  creds_json | api -H 'Content-Type: application/json' --data-binary @- \
    "${BASE}/login" | json_field access_token
}

mkdir -p "${STATE}"

log "waiting for the runtime API"
up=0
for _ in $(seq 1 120); do
  if api -o /dev/null "${BASE}/version" 2>/dev/null; then up=1; break; fi
  sleep 5
done
[ "${up}" = 1 ] || { log "FAIL: the runtime API did not answer in 600 s"; exit 1; }
api "${BASE}/version" || true; echo

# Already provisioned on an earlier boot and running again: nothing to do.
if [ -f "${STATE}/provisioned" ]; then
  token=$(login 2>/dev/null || true)
  if [ -n "${token}" ]; then
    # The runtime restarts the stored program itself; give it a moment.
    for _ in $(seq 1 24); do
      st=$(api -H "Authorization: Bearer ${token}" "${BASE}/status" || true)
      case "${st}" in *STATUS:RUNNING*) log "already running"; exit 0 ;; esac
      sleep 5
    done
  fi
  log "marker present but the program is not running; provisioning again"
fi

[ -f "${ZIP}" ] || { log "FAIL: program ZIP ${ZIP} is missing"; exit 1; }

log "repacking ${ZIP} with the plugin configs in ${CONF}"
# Verified at v4.3.2: configs live in conf/ inside the ZIP, named after the
# plugin (modbus_master.json, modbus_slave.json). A plugin with no file is
# disabled, and a ZIP with no conf/ directory disables all plugins.
unzip -q "${ZIP}" -d "${WORK}/unpacked"
mkdir -p "${WORK}/unpacked/conf"
cp -v "${CONF}"/*.json "${WORK}/unpacked/conf/"
(cd "${WORK}/unpacked" && zip -qr "${WORK}/program.zip" .)

log "create-user (the first account on an empty database needs no auth)"
code=$(creds_json | api -o "${WORK}/cu.json" -w '%{http_code}' \
  -H 'Content-Type: application/json' --data-binary @- "${BASE}/create-user" || true)
log "create-user HTTP ${code}"   # 201 created; 401 or 409 means the user already exists

token=""
for _ in 1 2 3 4 5 6; do
  token=$(login || true)
  [ -n "${token}" ] && break
  log "login returned no token; retrying"
  sleep 5
done
[ -n "${token}" ] || { log "FAIL: login failed"; exit 1; }
AUTH="Authorization: Bearer ${token}"

log "upload-file"
up=$(api -H "${AUTH}" -F "file=@${WORK}/program.zip" "${BASE}/upload-file?clean=0")
echo "${up}"
case "${up}" in *'"UploadFileFail":""'*) ;; *) log "FAIL: upload rejected"; exit 1 ;; esac

log "polling compilation-status (timeout ${TIMEOUT}s)"
start=$(date +%s)
while :; do
  st=$(api -H "${AUTH}" "${BASE}/compilation-status" | json_field status || true)
  log "$(( $(date +%s) - start ))s status=${st}"
  case "${st}" in
    SUCCESS) break ;;
    FAILED) api -H "${AUTH}" "${BASE}/runtime-logs" | tail -c 2000 || true; log "FAIL: compilation failed"; exit 1 ;;
  esac
  if (( $(date +%s) - start > TIMEOUT )); then log "FAIL: compile timeout"; exit 1; fi
  sleep 5
done

log "start-plc"
# A curl hiccup here must not fail the unit: Restart=on-failure would then
# re-upload and recompile from scratch. The status polls decide the outcome.
api -H "${AUTH}" "${BASE}/start-plc" || true; echo
for _ in $(seq 1 12); do
  sleep 5
  st=$(api -H "${AUTH}" "${BASE}/status" || true)
  case "${st}" in
    *STATUS:RUNNING*) touch "${STATE}/provisioned"; log "PASS: program is running"; exit 0 ;;
  esac
done
api -H "${AUTH}" "${BASE}/runtime-logs" | tail -c 2000 || true
log "FAIL: PLC is not RUNNING"
exit 1
