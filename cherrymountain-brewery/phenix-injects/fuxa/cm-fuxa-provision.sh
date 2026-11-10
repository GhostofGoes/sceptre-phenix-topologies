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
# in with the range password instead, and when FUXA already holds the same
# devices and tags it does not post (and restart) again. FUXA_EXPECT_DEVICES
# names the devices the import must produce (default: every device of the file).
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

# The devices the imported project must hold: FUXA_EXPECT_DEVICES (space
# separated, written by the cherrymountain app), else every device of the file
# except FUXA's own server device (it always exists, and its name has a space).
EXPECT="${FUXA_EXPECT_DEVICES:-}"
if [ -z "${EXPECT}" ]; then
  EXPECT=$(python3 - "${PROJECT}" <<'PY'
import json, sys
devs = json.load(open(sys.argv[1])).get("devices", {}).values()
print(" ".join(sorted(d.get("name", "") for d in devs if d.get("type") != "FuxaServer")))
PY
)
fi

# The device set of a project file: each device name with its tag count.
devices_of() {
  python3 - "$1" <<'PY'
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(1)
devs = doc.get("devices", {}) if isinstance(doc, dict) else {}
print(" ".join(sorted(f'{d.get("name")}:{len(d.get("tags") or {})}' for d in devs.values())))
PY
}

# GET /api/project into $1; the token goes on stdin, not the command line.
get_project() {
  printf 'x-access-token: %s\n' "${TOKEN}" \
    | curl -sS -m 10 -H @- "${FUXA}/api/project" -o "$1" 2>/dev/null
}

# True when every expected device is in the project file $1.
has_expected() {
  CM_EXPECT="${EXPECT}" python3 - "$1" <<'PY' 2>/dev/null
import json, os, sys
names = {d.get("name") for d in json.load(open(sys.argv[1])).get("devices", {}).values()}
sys.exit(0 if set(os.environ["CM_EXPECT"].split()) <= names else 1)
PY
}

# A re-run with the same devices and tags already imported skips the POST, which
# would replace the project and restart the FUXA runtime for nothing.
if get_project "${WORK}/before.json" \
  && [ "$(devices_of "${WORK}/before.json" || true)" = "$(devices_of "${PROJECT}")" ] \
  && has_expected "${WORK}/before.json"; then
  log "PASS: the project is already imported (devices: ${EXPECT}); not posting it again"
  exit 0
fi

log "POST /api/project (the runtime restarts)"
code=$(printf 'x-access-token: %s\n' "${TOKEN}" \
  | curl -sS -m 120 -o "${WORK}/post.out" -w '%{http_code}' \
    -H 'Content-Type: application/json' -H @- \
    --data-binary "@${PROJECT}" "${FUXA}/api/project")
[ "${code}" = 200 ] || { log "FAIL: project POST HTTP ${code}: $(cat "${WORK}/post.out")"; exit 1; }

log "reading the project back (expecting: ${EXPECT})"
for _ in $(seq 1 30); do
  sleep 2
  if get_project "${WORK}/after.json" && has_expected "${WORK}/after.json"; then
    log "PASS: project imported, devices present: ${EXPECT}"
    exit 0
  fi
done
log "FAIL: devices missing after import (expected: ${EXPECT})"
exit 1
