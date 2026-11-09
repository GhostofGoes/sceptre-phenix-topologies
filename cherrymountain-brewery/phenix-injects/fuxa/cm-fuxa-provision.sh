#!/usr/bin/env bash
# Provision FUXA on scada-fuxa (DESIGN.md 12.2): wait for port 1881, sign in as
# the seeded admin, set the range admin password through the users API, then
# POST the project to /api/project. Follows spikes/fuxa/secure_flow.sh.
# Installed as /usr/local/bin/cm-fuxa-provision and run by
# cm-fuxa-provision.service.
#
# Upstream facts (frangoteam/FUXA server/api, checked at 1.3.6):
#   POST /api/signin  -> {data: {token}}; protected routes read the JWT from
#                        the x-access-token header
#   POST /api/users   -> body {params: {username, fullname, password, groups,
#                        info}}; info must be a JSON string
#   POST /api/project -> admin only; replaces the project and restarts the
#                        runtime
# Re-running is safe: when the seeded password is rejected, the script signs
# in with the range password instead.
set -euo pipefail

FUXA="${FUXA_URL:-http://127.0.0.1:1881}"
SEED_PASS="${FUXA_SEED_PASSWORD:-123456}"
NEW_PASS="${FUXA_ADMIN_PASSWORD:?set FUXA_ADMIN_PASSWORD}"
PROJECT="${FUXA_PROJECT:-/etc/cherrymountain/fuxa/project.json}"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

log() { echo "cm-fuxa-provision: $*"; }

# JSON bodies are built by python from the environment and sent on stdin, so
# passwords never appear on a command line (ps, /proc/<pid>/cmdline).
signin() {  # user pass -> token (empty on failure)
  CM_USER="$1" CM_PASS="$2" python3 -c '
import json, os
print(json.dumps({"username": os.environ["CM_USER"], "password": os.environ["CM_PASS"]}))' \
    | curl -sS -m 20 -H 'Content-Type: application/json' --data-binary @- \
      "${FUXA}/api/signin" 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("data", {}).get("token", ""))' 2>/dev/null || true
}

[ -f "${PROJECT}" ] || { log "FAIL: project ${PROJECT} is missing"; exit 1; }

log "waiting for FUXA on ${FUXA}"
up=0
for _ in $(seq 1 120); do
  if curl -sS -m 5 -o /dev/null "${FUXA}/api/version" 2>/dev/null; then up=1; break; fi
  sleep 5
done
[ "${up}" = 1 ] || { log "FAIL: FUXA did not answer on ${FUXA} in 600 s"; exit 1; }

log "sign in as the seeded admin"
TOKEN=$(signin admin "${SEED_PASS}")
if [ -z "${TOKEN}" ]; then
  log "seeded password rejected; trying the range password (re-run)"
  TOKEN=$(signin admin "${NEW_PASS}")
  [ -n "${TOKEN}" ] || { log "FAIL: cannot sign in; is secureEnabled true?"; exit 1; }
else
  log "set the admin password"
  code=$(CM_PASS="${NEW_PASS}" python3 -c '
import json, os
print(json.dumps({"params": {"username": "admin", "fullname": "Administrator",
                             "password": os.environ["CM_PASS"], "groups": -1, "info": "{}"}}))' \
    | curl -sS -m 20 -o "${WORK}/users.out" -w '%{http_code}' \
      -H 'Content-Type: application/json' -H "x-access-token: ${TOKEN}" \
      --data-binary @- "${FUXA}/api/users")
  [ "${code}" = 200 ] || { log "FAIL: users API HTTP ${code}: $(cat "${WORK}/users.out")"; exit 1; }
  TOKEN=$(signin admin "${NEW_PASS}")
  [ -n "${TOKEN}" ] || { log "FAIL: new password does not work"; exit 1; }
  [ -z "$(signin admin "${SEED_PASS}")" ] || { log "FAIL: seeded password still valid"; exit 1; }
fi

log "POST /api/project (the runtime restarts)"
code=$(curl -sS -m 120 -o "${WORK}/post.out" -w '%{http_code}' \
  -H 'Content-Type: application/json' -H "x-access-token: ${TOKEN}" \
  --data-binary "@${PROJECT}" "${FUXA}/api/project")
[ "${code}" = 200 ] || { log "FAIL: project POST HTTP ${code}: $(cat "${WORK}/post.out")"; exit 1; }

log "reading the project back"
for _ in $(seq 1 30); do
  sleep 2
  if curl -sS -m 10 -H "x-access-token: ${TOKEN}" "${FUXA}/api/project" -o "${WORK}/after.json" 2>/dev/null \
    && python3 - "${WORK}/after.json" <<'PY' 2>/dev/null
import json, sys
names = [d.get("name") for d in json.load(open(sys.argv[1])).get("devices", {}).values()]
sys.exit(0 if "plc-cellar" in names else 1)
PY
  then
    log "PASS: project imported, device plc-cellar present"
    exit 0
  fi
done
log "FAIL: device plc-cellar not found after import"
exit 1
