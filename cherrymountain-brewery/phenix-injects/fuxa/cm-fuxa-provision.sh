#!/usr/bin/env bash
# Provision FUXA on scada-fuxa and bas-fuxa (DESIGN.md 12.2): wait for port
# 1881, sign in as the seeded admin, set the range admin password through the
# users API, add the operator account, then POST the project to /api/project.
# Follows spikes/fuxa/secure_flow.sh. Installed as
# /usr/local/bin/cm-fuxa-provision and run by cm-fuxa-provision.service.
#
# Upstream facts (frangoteam/FUXA server/api, checked at the cm-fuxa pin,
# server 1.3.6):
#   POST /api/signin  -> {data: {token, groups}}; protected routes read the JWT
#                        from the x-access-token header
#   POST /api/users   -> admin only; body {params: {username, fullname,
#                        password, groups, info}} creates the account or, when
#                        it exists, replaces its password, groups, and info;
#                        info must be a JSON string
#   GET  /api/users   -> admin only (a refusal is HTTP 401)
#   POST /api/project -> admin only; replaces the project and restarts the
#                        runtime, and leaves the users alone (they are in
#                        users.fuxap.db, which only runtime start-up opens)
# An administrator is a user whose groups value is -1 or 255
# (server/api/jwt-helper.js adminGroups). Any other signed-in user may write a
# tag from a view and acknowledge alarms (server/runtime/index.js
# isSocketWriteAuthorized; /api/alarmack asks for no group), but cannot open the
# editor or call the project, users, settings, or device APIs. The operator
# account is in FUXA's "Operator" group, 2.
# Re-running is safe: when the seeded password is rejected, the script signs
# in with the range password instead; an operator account that already signs in
# with the range password from the Operator group is left alone; and when FUXA
# already holds the same devices and tags it does not post (and restart) again.
# FUXA_EXPECT_DEVICES names the devices the import must produce (default: every
# device of the file). Without FUXA_OPERATOR_PASSWORD no operator account is
# made.
set -euo pipefail

FUXA="${FUXA_URL:-http://127.0.0.1:1881}"
SEED_PASS="${FUXA_SEED_PASSWORD:-123456}"
NEW_PASS="${FUXA_ADMIN_PASSWORD:?set FUXA_ADMIN_PASSWORD}"
OPERATOR_PASS="${FUXA_OPERATOR_PASSWORD:-}"
OPERATOR_USER=operator
OPERATOR_GROUPS=2
PROJECT="${FUXA_PROJECT:-/etc/cherrymountain/fuxa/project.json}"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

log() { echo "cm-fuxa-provision: $*"; }

# JSON bodies are built by python from the environment and sent on stdin or from
# a file in the private work directory, so passwords never appear on a command
# line (ps, /proc/<pid>/cmdline).
signin_reply() {  # user pass -> the sign-in response body
  CM_USER="$1" CM_PASS="$2" python3 -c '
import json, os
print(json.dumps({"username": os.environ["CM_USER"], "password": os.environ["CM_PASS"]}))' \
    | curl -sS -m 20 -H 'Content-Type: application/json' --data-binary @- \
      "${FUXA}/api/signin" 2>/dev/null || true
}

reply_field() {  # name -> that field of the sign-in response on stdin (empty if none)
  python3 -c 'import json, sys; print(json.load(sys.stdin).get("data", {}).get(sys.argv[1], ""))' "$1" 2>/dev/null || true
}

signin() {  # user pass -> token (empty on failure)
  signin_reply "$@" | reply_field token
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

# --- the operator account --------------------------------------------------
# A client that opens /home with no session is the guest: it sees the views, but
# FUXA refuses its tag writes. The HMI kiosks sign in as this account themselves
# (cm-kiosk, with the password the app puts in /etc/default/cm-kiosk-credentials),
# and anyone else signs in by hand (the account icon in the header, or the dialog
# that opens when a write is refused), then writes tags and acknowledges alarms.
# The project import below replaces the project only, so the account stays.

# True when the operator signs in with the range password and is in the
# Operator group (2).
operator_ok() {
  [ "$(signin_reply "${OPERATOR_USER}" "${OPERATOR_PASS}" | reply_field groups)" = "${OPERATOR_GROUPS}" ]
}

# POST /api/users with the admin token: create the operator, or reset its
# password and group when it exists. The body goes through a file in the private
# work directory and the token through stdin, neither through a command line.
put_operator() {  # -> HTTP status
  CM_USER="${OPERATOR_USER}" CM_GROUPS="${OPERATOR_GROUPS}" CM_PASS="${OPERATOR_PASS}" CM_OUT="${WORK}/operator.json" python3 -c '
import json, os
body = {"params": {"username": os.environ["CM_USER"], "fullname": "Operator",
                   "password": os.environ["CM_PASS"], "groups": int(os.environ["CM_GROUPS"]), "info": "{}"}}
with open(os.environ["CM_OUT"], "w", encoding="utf-8") as out:
    json.dump(body, out)'
  printf 'x-access-token: %s\n' "${TOKEN}" \
    | curl -sS -m 20 -o "${WORK}/users.out" -w '%{http_code}' \
      -H 'Content-Type: application/json' -H @- \
      --data-binary "@${WORK}/operator.json" "${FUXA}/api/users"
}

if [ -z "${OPERATOR_PASS}" ]; then
  log "FUXA_OPERATOR_PASSWORD is not set: no operator account"
else
  if operator_ok; then
    log "the ${OPERATOR_USER} account is already in place"
  else
    log "set the ${OPERATOR_USER} account (group ${OPERATOR_GROUPS})"
    code=$(put_operator)
    [ "${code}" = 200 ] || { log "FAIL: users API HTTP ${code}: $(cat "${WORK}/users.out")"; exit 1; }
    operator_ok || { log "FAIL: the ${OPERATOR_USER} account cannot sign in after it was set"; exit 1; }
  fi
  # The account must not be an administrator: FUXA answers 401 to the users API
  # for any other token.
  op_token=$(signin "${OPERATOR_USER}" "${OPERATOR_PASS}")
  code=$(printf 'x-access-token: %s\n' "${op_token}" \
    | curl -sS -m 20 -o /dev/null -w '%{http_code}' -H @- "${FUXA}/api/users")
  [ "${code}" = 401 ] || { log "FAIL: the ${OPERATOR_USER} account can use the users API (HTTP ${code}); it must not be an administrator"; exit 1; }
  log "the ${OPERATOR_USER} account can sign in and cannot use the users API"
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
