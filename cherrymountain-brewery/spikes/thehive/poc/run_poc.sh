#!/bin/bash
# TheHive spike proof (spikes/thehive/README.md, "Result"), in Docker on one host.
#
#   ./run_poc.sh thehive    TheHive 5 single container (local BerkeleyDB + Lucene +
#                           files), the cherrymountain org, a service account and key
#   ./run_poc.sh wazuh      a Wazuh 4.14 manager with the CherryMountain decoders and
#                           rules and the custom-cm-case integration pointed at TheHive
#   ./run_poc.sh send       write sample lines of phenix-injects/wazuh/samples.txt into a
#                           file the manager reads, wait for TheHive alerts, re-deliver one
#   ./run_poc.sh iris       DFIR-IRIS 2.4 (Postgres, RabbitMQ, app, worker) with generated
#                           secrets in $WORK/iris.env
#   ./run_poc.sh iris-send  replay the alerts that `send` saved ($WORK/wazuh-alerts.json)
#                           into IRIS with the same script, twice for one rule (dedup)
#   ./run_poc.sh stats      docker stats of the containers
#   ./run_poc.sh clean      remove the containers and the network
#
# Env: THEHIVE_IMAGE (strangebee/thehive:5.8.1), WAZUH_IMAGE (wazuh/wazuh-manager:4.14.1),
# WORK (a scratch directory, default ./.work). The TheHive admin password is the
# image default (admin@thehive.local / secret): this is a throwaway container on
# 127.0.0.1, never a range VM.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CM="$(cd "${HERE}/../../.." && pwd)"
WORK="${WORK:-${HERE}/.work}"
NET=thpoc
THEHIVE_IMAGE="${THEHIVE_IMAGE:-strangebee/thehive:5.8.1}"
WAZUH_IMAGE="${WAZUH_IMAGE:-wazuh/wazuh-manager:4.14.1}"
TH=http://127.0.0.1:9000
ADMIN='admin@thehive.local:secret'
mkdir -p "$WORK"

log() { echo "run_poc: $*"; }

wait_for() {
  local seconds="$1" what="$2"
  shift 2
  local deadline=$((SECONDS + seconds))
  until "$@" > /dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      log "FAIL: ${what} not ready within ${seconds} s"
      return 1
    fi
    sleep 2
  done
  log "${what} ready after $((SECONDS - deadline + seconds)) s"
}

thehive() {
  docker network inspect "$NET" > /dev/null 2>&1 || docker network create "$NET" > /dev/null
  if ! docker container inspect th-bdb > /dev/null 2>&1; then
    openssl rand -hex 24 > "${WORK}/play-secret"
    docker run -d --name th-bdb --network "$NET" -p 127.0.0.1:9000:9000 "$THEHIVE_IMAGE" \
      --no-config-cortex --index-backend lucene --bdb-directory /data/db \
      --storage-directory /data/files --secret "$(cat "${WORK}/play-secret")" > /dev/null
  fi
  wait_for 300 "TheHive API" curl -fsS -u "$ADMIN" "${TH}/api/v1/user/current"
  curl -sS -u "$ADMIN" -H 'Content-Type: application/json' -X POST "${TH}/api/v1/organisation" \
    -d '{"name":"cherrymountain","description":"CherryMountain Brewery SOC"}' > /dev/null || true
  curl -sS -u "$ADMIN" -H 'Content-Type: application/json' -X POST "${TH}/api/v1/user" \
    -d '{"login":"wazuh@cherrymountain.local","name":"Wazuh integration","profile":"analyst","organisation":"cherrymountain","type":"Service"}' > /dev/null || true
  curl -fsS -u "$ADMIN" -X POST "${TH}/api/v1/user/wazuh@cherrymountain.local/key/renew" > "${WORK}/thehive.key"
  chmod 0600 "${WORK}/thehive.key"
  log "TheHive license state:"
  curl -sS -u "$ADMIN" "${TH}/api/v1/license/current"
  echo
}

wazuh() {
  # The manager runs on an internal network (no internet), like the range: with
  # internet, Wazuh 4.14's content manager downloads the vulnerability feed and the
  # container grew by 3 GB in five minutes. TheHive joins that network too.
  docker network inspect "${NET}-int" > /dev/null 2>&1 || docker network create --internal "${NET}-int" > /dev/null
  docker network connect "${NET}-int" th-bdb 2> /dev/null || true
  docker container inspect wz-poc > /dev/null 2>&1 \
    || docker run -d --name wz-poc --network "${NET}-int" "$WAZUH_IMAGE" > /dev/null
  # wazuh-control status exits 1 while optional daemons (clusterd, maild) are off.
  wait_for 300 "Wazuh manager" docker exec wz-poc pgrep -f wazuh-analysisd
  docker cp "${CM}/phenix-injects/wazuh/cherrymountain_rules.xml" wz-poc:/var/ossec/etc/rules/cherrymountain_rules.xml
  docker cp "${CM}/phenix-injects/wazuh/cherrymountain_decoders.xml.in" wz-poc:/var/ossec/etc/decoders/cherrymountain_decoders.xml
  docker cp "${HERE}/cm_wazuh_case.py" wz-poc:/var/ossec/integrations/custom-cm-case.py
  docker cp "${HERE}/custom-cm-case" wz-poc:/var/ossec/integrations/custom-cm-case
  docker exec wz-poc chown root:wazuh /var/ossec/integrations/custom-cm-case /var/ossec/integrations/custom-cm-case.py \
    /var/ossec/etc/rules/cherrymountain_rules.xml /var/ossec/etc/decoders/cherrymountain_decoders.xml
  docker exec wz-poc chmod 0750 /var/ossec/integrations/custom-cm-case /var/ossec/integrations/custom-cm-case.py
  # Wheels are fetched on the host and installed with --no-index, as an offline
  # image build would (the range has no internet; the image build does).
  python3 -m pip download -q --dest "${WORK}/wheels" --python-version 3.10 \
    --platform manylinux2014_x86_64 --implementation cp --only-binary=:all: 'pydantic==2.12.*'
  docker cp "${WORK}/wheels" wz-poc:/tmp/cm-wheels
  docker exec wz-poc /var/ossec/framework/python/bin/pip3 install -q --no-index --find-links /tmp/cm-wheels \
    --target /var/ossec/integrations/cm-lib 'pydantic==2.12.*'
  docker exec wz-poc rm -rf /tmp/cm-wheels
  docker exec wz-poc chown -R root:wazuh /var/ossec/integrations/cm-lib
  # The package ships its vulnerability feed (a 320 MB .tar.xz) and the first start
  # unpacks it to 2.5 GB in /var/ossec/tmp, internet or not. cm-wazuh-provision turns
  # vulnerability detection off before the first start; do the same here.
  docker exec wz-poc /var/ossec/bin/wazuh-control stop > /dev/null 2>&1 || true
  docker exec wz-poc sed -i '/<vulnerability-detection>/,/<\/vulnerability-detection>/s|<enabled>yes</enabled>|<enabled>no</enabled>|' /var/ossec/etc/ossec.conf
  docker exec wz-poc find /var/ossec/tmp -maxdepth 1 -name 'vd_*.tar' -delete
  docker exec wz-poc rm -rf /var/ossec/queue/vd
  docker exec wz-poc touch /var/log/cm-poc.log
  sed -e "s|@HOOK_URL@|http://th-bdb:9000|" -e "s|@API_KEY@|$(cat "${WORK}/thehive.key")|" \
    -e "s|@BACKEND@|thehive|" "${HERE}/ossec-case.conf" > "${WORK}/ossec-case.conf"
  docker cp "${WORK}/ossec-case.conf" wz-poc:/tmp/ossec-case.conf
  if ! docker exec wz-poc grep -q custom-cm-case /var/ossec/etc/ossec.conf; then
    docker exec wz-poc bash -c 'cat /tmp/ossec-case.conf >> /var/ossec/etc/ossec.conf'
  fi
  docker exec wz-poc rm -f /tmp/ossec-case.conf
  docker exec wz-poc /var/ossec/bin/wazuh-control restart > /dev/null
  wait_for 120 "integratord" docker exec wz-poc pgrep -f wazuh-integratord
}

count_alerts() {
  curl -fsS -H "Authorization: Bearer $(cat "${WORK}/thehive.key")" -H 'Content-Type: application/json' \
    -X POST "${TH}/api/v1/query?name=cm-poc" \
    -d '{"query":[{"_name":"listAlert"},{"_name":"filter","_eq":{"_field":"source","_value":"wazuh-cherrymountain"}},{"_name":"count"}]}'
}

send() {
  # One line per rule: blocked vendor forward (100105), cmadmin from the tunnel
  # (100302 or 100307/100308 outside the window), session recording deleted
  # (100314 needs FIM, so not here), PLC upload (100411), a firewall drop pair
  # (100101 twice: one bucketed alert), and a level-6 vendor tunnel-up (not forwarded).
  local start
  start=$(count_alerts)
  log "TheHive alerts before: ${start}"
  {
    grep -E '^100105\|' "${CM}/phenix-injects/wazuh/samples.txt"
    grep -E '^100302,' "${CM}/phenix-injects/wazuh/samples.txt"
    grep -E '^100411\|' "${CM}/phenix-injects/wazuh/samples.txt"
    grep -E '^100101\|' "${CM}/phenix-injects/wazuh/samples.txt"
    grep -E '^100101\|' "${CM}/phenix-injects/wazuh/samples.txt"
    grep -E '^100201,' "${CM}/phenix-injects/wazuh/samples.txt"
  } | cut -d'|' -f2- > "${WORK}/lines.log"
  local t0=$SECONDS
  docker cp "${WORK}/lines.log" wz-poc:/tmp/lines.log
  docker exec wz-poc bash -c 'cat /tmp/lines.log >> /var/log/cm-poc.log'
  local deadline=$((SECONDS + 120)) now
  while :; do
    now=$(count_alerts)
    [ "$now" -ge $((start + 4)) ] && break
    [ "$SECONDS" -lt "$deadline" ] || break
    sleep 1
  done
  log "TheHive alerts after: ${now} (+$((now - start)) in $((SECONDS - t0)) s)"
  docker exec wz-poc tail -n 12 /var/ossec/logs/integrations.log | grep custom-cm-case || true
  # Re-deliver the newest PLC-upload alert the way integratord would (same alert file
  # contents): TheHive must refuse it as a duplicate.
  docker exec wz-poc bash -c "grep '\"id\":\"100411\"' /var/ossec/logs/alerts/alerts.json | tail -n 1 > /tmp/redeliver.alert"
  docker exec wz-poc /var/ossec/integrations/custom-cm-case /tmp/redeliver.alert "$(cat "${WORK}/thehive.key")" http://th-bdb:9000
  docker exec wz-poc tail -n 1 /var/ossec/logs/integrations.log
  log "TheHive alerts after the re-delivery: $(count_alerts)"
  # Keep the manager's alerts: iris-send replays them into DFIR-IRIS.
  docker cp wz-poc:/var/ossec/logs/alerts/alerts.json "${WORK}/wazuh-alerts.json"
  curl -fsS -H "Authorization: Bearer $(cat "${WORK}/thehive.key")" -H 'Content-Type: application/json' \
    -X POST "${TH}/api/v1/query?name=cm-poc-list" \
    -d '{"query":[{"_name":"listAlert"},{"_name":"filter","_eq":{"_field":"source","_value":"wazuh-cherrymountain"}},{"_name":"sort","_fields":[{"_createdAt":"desc"}]},{"_name":"page","from":0,"to":10,"extraData":["observableStats"]}]}' \
    > "${WORK}/alerts.json"
  python3 -I -c 'import json, sys
for a in json.load(open(sys.argv[1])):
    obs = a.get("extraData", {}).get("observableStats", {}).get("total", "?")
    print("  sev", a["severity"], a["sourceRef"].ljust(34), "obs", obs, "|", a.get("caseTemplate"), "|", a["title"][:70])' "${WORK}/alerts.json"
}

iris() {
  # DFIR-IRIS 2.4 (the recommended alternative): Postgres, RabbitMQ, the app, and the
  # worker; no nginx (the proof talks to the app on 8000). The admin password and
  # API key are set from the environment, as the cherrymountain app would render them.
  docker network inspect "$NET" > /dev/null 2>&1 || docker network create "$NET" > /dev/null
  if [ ! -f "${WORK}/iris.env" ]; then
    (
      umask 077
      cat > "${WORK}/iris.env" <<EOF
POSTGRES_USER=postgres
POSTGRES_PASSWORD=$(openssl rand -hex 16)
POSTGRES_ADMIN_USER=raptor
POSTGRES_ADMIN_PASSWORD=$(openssl rand -hex 16)
POSTGRES_DB=iris_db
POSTGRES_SERVER=iris-db
POSTGRES_PORT=5432
DOCKERIZED=1
IRIS_SECRET_KEY=$(openssl rand -hex 24)
IRIS_SECURITY_PASSWORD_SALT=$(openssl rand -hex 16)
CELERY_BROKER=amqp://iris-mq
IRIS_AUTHENTICATION_TYPE=local
IRIS_ADM_PASSWORD=Cm-$(openssl rand -hex 12)
IRIS_ADM_API_KEY=$(openssl rand -hex 32)
IRIS_ADM_EMAIL=admin@cherrymountain.local
IRIS_ADM_USERNAME=administrator
EOF
    )
  fi
  local tag="${IRIS_TAG:-v2.4.29}"
  log "start $(date -u +%T)"
  docker run -d --name iris-mq --network "$NET" "${RABBITMQ_IMAGE:-public.ecr.aws/docker/library/rabbitmq:3-alpine}" > /dev/null
  docker run -d --name iris-db --network "$NET" --env-file "${WORK}/iris.env" "ghcr.io/dfir-iris/iriswebapp_db:${tag}" > /dev/null
  docker run -d --name iris-app --network "$NET" -p 127.0.0.1:8000:8000 --env-file "${WORK}/iris.env" \
    "ghcr.io/dfir-iris/iriswebapp_app:${tag}" nohup ./iris-entrypoint.sh iriswebapp > /dev/null
  docker run -d --name iris-worker --network "$NET" --env-file "${WORK}/iris.env" -e IRIS_WORKER=1 \
    "ghcr.io/dfir-iris/iriswebapp_app:${tag}" ./wait-for-iriswebapp.sh iris-app:8000 ./iris-entrypoint.sh iris-worker > /dev/null
  wait_for 600 "IRIS API" curl -fsS -H "Authorization: Bearer $(grep IRIS_ADM_API_KEY "${WORK}/iris.env" | cut -d= -f2)" \
    http://127.0.0.1:8000/api/ping
}

iris_send() {
  # Post the Wazuh alerts that the TheHive run saved (wazuh-alerts.json) to IRIS with
  # the same script, then once more to check the dedup.
  local key plc_type
  key=$(grep IRIS_ADM_API_KEY "${WORK}/iris.env" | cut -d= -f2)
  # An ICS asset type for the PLCs (IRIS ships IT types only); the icon is a 1x1 PNG.
  plc_type=$(curl -fsS -H "Authorization: Bearer ${key}" http://127.0.0.1:8000/manage/asset-type/list \
    | python3 -I -c 'import json, sys; print(next((t["asset_id"] for t in json.load(sys.stdin)["data"] if t["asset_name"] == "PLC"), ""))')
  if [ -z "$plc_type" ]; then
    printf 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==' \
      | base64 -d > "${WORK}/px.png"
    plc_type=$(curl -fsS -H "Authorization: Bearer ${key}" -F asset_name=PLC \
      -F 'asset_description=Programmable logic controller (OpenPLC)' \
      -F "asset_icon_not_compromised=@${WORK}/px.png" -F "asset_icon_compromised=@${WORK}/px.png" \
      http://127.0.0.1:8000/manage/asset-type/add | python3 -I -c 'import json, sys; print(json.load(sys.stdin)["data"]["asset_id"])')
  fi
  printf '{"backend": "iris", "customer_id": 1, "plc_asset_type_id": %s}' "$plc_type" > "${WORK}/iris.options"
  local rule
  for rule in 100105 100302 100411 100412 100201 100302; do
    grep "\"id\":\"${rule}\"" "${WORK}/wazuh-alerts.json" | tail -n 1 > "${WORK}/a${rule}.alert"
    python3 "${HERE}/cm_wazuh_case.py" "${WORK}/a${rule}.alert" "$key" http://127.0.0.1:8000 "${WORK}/iris.options" || true
  done
}

stats() {
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' \
    th-bdb wz-poc iris-app iris-worker iris-db iris-mq 2>/dev/null || true
}

clean() {
  docker rm -f th-bdb wz-poc iris-app iris-worker iris-db iris-mq > /dev/null 2>&1 || true
  docker network rm "$NET" "${NET}-int" > /dev/null 2>&1 || true
}

case "${1:-}" in
  thehive | wazuh | send | iris | stats | clean) "$1" ;;
  iris-send) iris_send ;;
  *)
    echo "usage: $0 thehive|wazuh|send|iris|iris-send|stats|clean" >&2
    exit 2
    ;;
esac
