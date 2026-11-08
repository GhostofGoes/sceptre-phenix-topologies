#!/usr/bin/env bash
# Unattended OpenPLC v4 provisioning probe (design §12.1, steps 4-5).
#   create-user -> login -> upload-file -> poll compilation-status -> start-plc
# Usage: PLC=10.20.40.10 ZIP=./prog.zip ./provision.sh
# Env: PLC (required), ZIP (required), PORT=8443, USER_NAME=admin,
#      USER_PASS=cm-spike-pass, TIMEOUT=600 (compile timeout, seconds)
# The runtime serves a self-signed certificate, so curl runs with -k.
set -euo pipefail

: "${PLC:?set PLC to the runtime address}"
: "${ZIP:?set ZIP to the program ZIP built in the OpenPLC Editor}"
PORT="${PORT:-8443}"
USER_NAME="${USER_NAME:-admin}"
USER_PASS="${USER_PASS:-cm-spike-pass}"
TIMEOUT="${TIMEOUT:-600}"
BASE="https://${PLC}:${PORT}/api"

api() { curl -ksS --max-time 60 "$@"; }
now() { date +%s; }

echo "== /version and /capabilities (unauthenticated)"
api "${BASE}/version"; echo
api "${BASE}/capabilities"; echo

echo "== create-user (first account on an empty DB is unauthenticated admin)"
code=$(api -o /tmp/cu.json -w '%{http_code}' -H 'Content-Type: application/json' \
  -d "{\"username\":\"${USER_NAME}\",\"password\":\"${USER_PASS}\"}" \
  "${BASE}/create-user" || true)
echo "HTTP ${code}: $(cat /tmp/cu.json)"
# 201 = created. 401 = a user already exists (re-run); login below decides.

echo "== login"
TOKEN=$(api -H 'Content-Type: application/json' \
  -d "{\"username\":\"${USER_NAME}\",\"password\":\"${USER_PASS}\"}" \
  "${BASE}/login" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer ${TOKEN}"
echo "token acquired (${#TOKEN} chars)"

echo "== upload-file"
# TODO(verify): the multipart field name is "file" per the upstream OpenAPI
# docstring; the handler may also want extra fields (e.g. a project snapshot).
api -H "${AUTH}" -F "file=@${ZIP}" "${BASE}/upload-file"; echo

echo "== poll compilation-status (timeout ${TIMEOUT}s)"
start=$(now)
while :; do
  st=$(api -H "${AUTH}" "${BASE}/compilation-status" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))')
  echo "$(( $(now) - start ))s status=${st}"
  case "${st}" in
    SUCCESS) break ;;
    FAILED) echo "FAIL: compilation failed"; api -H "${AUTH}" "${BASE}/runtime-logs"; exit 1 ;;
  esac
  if (( $(now) - start > TIMEOUT )); then echo "FAIL: compile timeout"; exit 1; fi
  sleep 5
done
echo "compile_seconds=$(( $(now) - start ))"

echo "== start-plc"
api -H "${AUTH}" "${BASE}/start-plc"; echo
sleep 3
echo "== status (include_stats)"
api -H "${AUTH}" "${BASE}/status?include_stats=true"; echo
echo "PASS: unattended upload, compile, and start completed"
