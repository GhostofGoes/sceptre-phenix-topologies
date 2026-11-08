#!/usr/bin/env bash
# FUXA secure-mode provisioning probe (design §12.2):
#   signin (seeded admin) -> set new admin password -> POST /api/project.
# Usage: FUXA=http://10.20.30.10:1881 PROJECT=./project.json ./secure_flow.sh
# Env: FUXA (required), PROJECT (optional JSON; default is a tiny test project),
#      NEW_PASS=cm-spike-pass, SEED_USER=admin, SEED_PASS=123456
# Upstream (frangoteam/FUXA server/api): POST /api/signin returns
# {data:{token}}; protected routes read the JWT from the x-access-token header;
# POST /api/project needs an admin group and restarts the runtime.
set -euo pipefail

: "${FUXA:?set FUXA to the base URL}"
NEW_PASS="${NEW_PASS:-cm-spike-pass}"
SEED_USER="${SEED_USER:-admin}"
SEED_PASS="${SEED_PASS:-123456}"
PROJECT="${PROJECT:-}"

jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

signin() {  # user pass -> token (empty on failure)
  curl -sS -m 20 -H 'Content-Type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"$2\"}" "${FUXA}/api/signin" \
    | jget "d.get('data',{}).get('token','')" 2>/dev/null || true
}

echo "== signin as seeded ${SEED_USER}"
TOKEN=$(signin "${SEED_USER}" "${SEED_PASS}")
if [ -z "${TOKEN}" ]; then
  echo "seeded password rejected, trying the new password (re-run case)"
  TOKEN=$(signin "${SEED_USER}" "${NEW_PASS}")
  [ -n "${TOKEN}" ] || { echo "FAIL: cannot sign in; is secureEnabled true?"; exit 1; }
else
  echo "== set admin password via users API"
  # TODO(verify): the body shape for POST /api/users. server/api/users calls
  # runtime.users.setUsers(req.body.params); the editor sends the full user
  # object (username, fullname, groups, password). Confirm against the
  # Angular client (user-edit dialog) for the pinned FUXA release. groups is
  # the bitmask; -1 is the seeded admin value (TODO(verify)).
  code=$(curl -sS -m 20 -o /tmp/fuxa-users.out -w '%{http_code}' \
    -H 'Content-Type: application/json' -H "x-access-token: ${TOKEN}" \
    -d "{\"params\":{\"username\":\"${SEED_USER}\",\"fullname\":\"Administrator\",\"password\":\"${NEW_PASS}\",\"groups\":-1}}" \
    "${FUXA}/api/users")
  echo "HTTP ${code} $(cat /tmp/fuxa-users.out)"
  [ "${code}" = 200 ] || { echo "FAIL: users API"; exit 1; }
  TOKEN=$(signin "${SEED_USER}" "${NEW_PASS}")
  [ -n "${TOKEN}" ] || { echo "FAIL: new password does not work"; exit 1; }
  echo "old password must now fail:"
  [ -z "$(signin "${SEED_USER}" "${SEED_PASS}")" ] && echo "ok" || { echo "FAIL: seed password still valid"; exit 1; }
fi

echo "== GET /api/project (baseline)"
curl -sS -m 20 -H "x-access-token: ${TOKEN}" "${FUXA}/api/project" -o /tmp/fuxa-before.json
python3 -c "import json; d=json.load(open('/tmp/fuxa-before.json')); print('keys:', sorted(d)[:12])"

if [ -z "${PROJECT}" ]; then
  PROJECT=/tmp/fuxa-spike-project.json
  # TODO(verify): minimal valid project schema. Safer: export a project from
  # the pinned FUXA editor and pass it as PROJECT.
  cat > "${PROJECT}" <<'JSON'
{"version":"1.01","name":"spike","devices":{},"hmi":{"views":[],"layout":null},"alarms":[],"notifications":[],"scripts":[],"reports":[],"texts":[],"charts":[],"graphs":[],"mapsLocations":[]}
JSON
fi

echo "== POST /api/project (runtime restarts)"
code=$(curl -sS -m 60 -o /tmp/fuxa-post.out -w '%{http_code}' \
  -H 'Content-Type: application/json' -H "x-access-token: ${TOKEN}" \
  --data-binary "@${PROJECT}" "${FUXA}/api/project")
echo "HTTP ${code} $(cat /tmp/fuxa-post.out)"
[ "${code}" = 200 ] || { echo "FAIL: project POST"; exit 1; }

echo "== wait for restart, read back"
for _ in $(seq 1 30); do
  sleep 2
  if curl -sS -m 5 -H "x-access-token: ${TOKEN}" "${FUXA}/api/project" -o /tmp/fuxa-after.json 2>/dev/null \
    && python3 -c "import json; json.load(open('/tmp/fuxa-after.json'))" 2>/dev/null; then
    break
  fi
done
python3 - "${PROJECT}" <<'PY'
import json, sys
want = json.load(open(sys.argv[1])); got = json.load(open("/tmp/fuxa-after.json"))
miss = [k for k in want if k not in got]
print("missing top-level keys after round trip:", miss or "none")
sys.exit(1 if miss else 0)
PY
echo "== token survives restart? (needs secretCode in mysettings.json)"
code=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' -H "x-access-token: ${TOKEN}" "${FUXA}/api/project")
echo "old token after restart: HTTP ${code} (200 = secretCode works, 401/403 = token invalidated)"
echo "PASS: signin, password change, project round trip"
