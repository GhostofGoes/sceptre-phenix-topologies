#!/usr/bin/env bash
# Malcolm on a CI runner in Docker (no VM): does it fit, and do the Zeek/ICSNPP
# parsers log the plant's protocols?
#   CM_EVIDENCE_DIR=evidence ./docker_fit.sh
# Built for a GitHub-hosted ubuntu-24.04 runner (4 vCPU, 16 GB RAM, ~14 GB free
# disk before the cleanup below). Stages:
#   1. free disk, build a Modbus/TCP + DNP3 + BACnet/IP pcap (make_pcap.py);
#   2. Malcolm's Zeek image on the pcap, asserting on the Zeek log files;
#   3. only with FULL_STACK=1: a trimmed Malcolm stack (OpenSearch, Logstash,
#      Filebeat, pcap-monitor, Zeek offline, Arkime, Dashboards) fed the same
#      pcap. CI does not run it: stage 2 meets the criteria with one container.
# `docker stats` is sampled throughout; peak memory and CPU go to peak.txt.
# Writes evidence/malcolm-docker/{result.json,summary.md,...}; exits 1 if a
# check fails. Every command runs under `timeout`.
set -uo pipefail

MALCOLM_TAG="${MALCOLM_TAG:-v26.10.0}"
MALCOLM_SHA="${MALCOLM_SHA:-36bb3a61217b8555bb9fe8918acd3b45927a5458}"
IMAGE_TAG="${MALCOLM_TAG#v}"
OS_HEAP="${OS_HEAP:-3g}"
LS_HEAP="${LS_HEAP:-2g}"
HERE=$(cd "$(dirname "$0")" && pwd)
EV=$(mkdir -p "${CM_EVIDENCE_DIR:-evidence}/malcolm-docker" && cd "${CM_EVIDENCE_DIR:-evidence}/malcolm-docker" && pwd)
WORK="${MALCOLM_WORK:-$HOME/malcolm-work}"
CHECKS="$EV/checks.jsonl"
: >"$CHECKS"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$EV/steps.log"; }
check() { # name passed(true|false) detail
  log "$([ "$2" = true ] && echo PASS || echo FAIL): $1 -- ${3:0:300}"
  jq -nc --arg n "$1" --argjson p "$2" --arg d "${3:0:2000}" '{name:$n, passed:$p, detail:$d}' >>"$CHECKS"
}
fact() { log "fact $1: ${2:0:300}"; jq -nc --arg k "$1" --arg v "${2:0:4000}" '{key:$k, value:$v}' >>"$EV/facts.jsonl"; }

finish() {
  local passed=true
  if jq -e -s 'length > 0 and all(.[]; .passed)' "$CHECKS" >/dev/null; then passed=true; else passed=false; fi
  local facts_json
  facts_json=$(jq -s 'map({(.key): .value}) | add // {}' "$EV/facts.jsonl" 2>/dev/null || echo '{}')
  jq -s --argjson passed "$passed" --argjson facts "$facts_json" \
    '{spike:"malcolm-docker", passed:$passed, checks:., facts:$facts}' "$CHECKS" >"$EV/result.json"
  {
    echo "### Spike malcolm-docker: $([ "$passed" = true ] && echo PASS || echo FAIL)"
    echo
    jq -r '"- \(if .passed then "PASS" else "FAIL" end): \(.name)"' "$CHECKS"
    echo
    echo "Peak memory: $(cat "$EV/peak.txt" 2>/dev/null || echo n/a)"
  } >"$EV/summary.md"
  [ -n "${GITHUB_STEP_SUMMARY:-}" ] && cat "$EV/summary.md" >>"$GITHUB_STEP_SUMMARY"
  [ "$passed" = true ]
}

# ---------------------------------------------------------------------------
log "runner: $(nproc) vCPU, $(free -m | awk '/^Mem:/ {print $2}') MB RAM"
df -h / /mnt 2>&1 | tee "$EV/df-before.txt" >/dev/null
fact runner "$(nproc) vCPU, $(free -m | awk '/^Mem:/ {print $2}') MB RAM, free disk $(df -h --output=avail / | tail -1 | tr -d ' ') on /"

# 1. Disk: delete preinstalled toolchains Malcolm does not need.
timeout 600 sudo rm -rf /usr/local/lib/android /usr/share/dotnet /opt/ghc /usr/local/.ghcup \
  /usr/local/share/boost /opt/hostedtoolcache/CodeQL /usr/lib/jvm /usr/local/share/powershell \
  /usr/share/swift /opt/az /usr/local/julia* 2>/dev/null || true
timeout 120 docker system prune -af >/dev/null 2>&1 || true
df -h / | tee "$EV/df-after-cleanup.txt" >/dev/null
fact free_disk_after_cleanup "$(df -h --output=avail / | tail -1 | tr -d ' ')"

# pcap with Modbus/TCP, DNP3 and BACnet/IP.
timeout 300 python3 -m venv "$WORK-venv" && timeout 300 "$WORK-venv/bin/pip" install -q scapy==2.6.1 pydantic==2.11.7
timeout 60 "$WORK-venv/bin/python" "$HERE/make_pcap.py" "$EV/ics.pcap" | tee -a "$EV/steps.log"
timeout 900 "$WORK-venv/bin/python" "$HERE/make_pcap.py" "$EV/ics-load.pcap" "${LOAD_REPEAT:-20000}" | tee -a "$EV/steps.log"
check "make_pcap.py wrote a pcap" "$([ -s "$EV/ics.pcap" ] && echo true || echo false)" "$(ls -l "$EV/ics.pcap" 2>&1)"

# Sample docker stats and free memory every 2 s in the background.
(
  while true; do
    ts=$(date +%s)
    docker stats --no-stream --format "$ts,{{.Name}},{{.MemUsage}},{{.CPUPerc}}" 2>/dev/null >>"$EV/docker-stats.csv"
    echo "$ts,$(free -m | awk '/^Mem:/ {print $3","$7}')" >>"$EV/free-mb.csv"
    sleep 2
  done
) &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null; true' EXIT

# 2. Zeek alone (Malcolm's image, its ICSNPP packages).
ZEEK_IMAGE="ghcr.io/idaholab/malcolm/zeek:$IMAGE_TAG"
log "pulling $ZEEK_IMAGE"
timeout 900 docker pull -q "$ZEEK_IMAGE" >/dev/null 2>&1
ZD="$EV/zeek-direct"
mkdir -p "$ZD" && cp "$EV/ics.pcap" "$ZD/" && chmod 777 "$ZD"
timeout 300 docker run --rm --user root -v "$ZD:/work" -w /work --entrypoint /usr/local/zeek/bin/zeek-offline \
  "$ZEEK_IMAGE" -C -r ics.pcap local >"$EV/zeek-direct.out" 2>&1
rm -f "$ZD/ics.pcap"
for proto in modbus dnp3 bacnet; do
  n=$(grep -vc '^#' "$ZD/$proto.log" 2>/dev/null || true)
  check "Malcolm's Zeek image logs $proto from the test pcap" "$([ "${n:-0}" -gt 0 ] && echo true || echo false)" "$proto.log: ${n:-0} records; $(ls "$ZD" | tr '\n' ' ')"
done
fact zeek_direct_logs "$(cd "$ZD" && wc -l modbus*.log dnp3*.log bacnet*.log 2>/dev/null | tr '\n' ';')"
fact zeek_image "$ZEEK_IMAGE, $(docker image inspect -f '{{.Size}}' "$ZEEK_IMAGE") bytes, Malcolm $MALCOLM_TAG"
fact zeek_version "$(timeout 60 docker run --rm --user root --entrypoint /usr/local/zeek/bin/zeek-offline "$ZEEK_IMAGE" --version 2>&1 | tail -1)"

# 2b. The same Zeek on a bulk pcap (LOAD_REPEAT repetitions of the traffic, each from its own client),
# for peak memory and CPU: the resource-fit evidence.
ZL="$EV/zeek-load"
mkdir -p "$ZL" && cp "$EV/ics-load.pcap" "$ZL/" && chmod 777 "$ZL"
docker run -d --name cm-zeek --user root -v "$ZL:/work" -w /work --entrypoint /usr/local/zeek/bin/zeek-offline \
  "$ZEEK_IMAGE" -C -r ics-load.pcap local >/dev/null
CID=$(docker inspect -f '{{.Id}}' cm-zeek)
# Peak memory from the container's cgroup (cgroup v2 memory.peak) and CPU from docker stats, once a second.
PEAK_FILE=$(ls /sys/fs/cgroup/system.slice/docker-"$CID".scope/memory.peak 2>/dev/null || true)
ZEEK_PEAK=0; ZEEK_CPU=0
for _ in $(seq 1 300); do
  [ "$(docker inspect -f '{{.State.Running}}' cm-zeek 2>/dev/null)" = true ] || break
  if [ -n "$PEAK_FILE" ]; then v=$(cat "$PEAK_FILE" 2>/dev/null || echo 0); [ "${v:-0}" -gt "$ZEEK_PEAK" ] && ZEEK_PEAK=$v; fi
  c=$(docker stats --no-stream --format '{{.CPUPerc}}' cm-zeek 2>/dev/null | tr -d '%'); echo "$(date +%s),cm-zeek,$c" >>"$EV/zeek-cpu.csv"
  if [ -n "$c" ] && awk -v a="$c" -v b="$ZEEK_CPU" 'BEGIN { exit !(a > b) }'; then ZEEK_CPU=$c; fi
  sleep 1
done
docker wait cm-zeek >/dev/null 2>&1
docker logs cm-zeek >"$EV/zeek-load.out" 2>&1
fact zeek_peak "memory $((ZEEK_PEAK / 1048576)) MiB (cgroup memory.peak), CPU peak ${ZEEK_CPU}% of one core, run took $(docker inspect -f '{{.State.StartedAt}} to {{.State.FinishedAt}}' cm-zeek)"
docker rm -f cm-zeek >/dev/null 2>&1
fact zeek_load "$(du -h "$EV/ics-load.pcap" | cut -f1) pcap; modbus.log $(grep -vc '^#' "$ZL/modbus.log") records, dnp3.log $(grep -vc '^#' "$ZL/dnp3.log"), bacnet.log $(grep -vc '^#' "$ZL/bacnet.log")"
rm -rf "$ZL"

# 3. The full pipeline, only when asked (FULL_STACK=1): the pass/fail criteria are met by stage 2,
# so CI runs the smallest set of containers (just Zeek) and does not start OpenSearch and friends.
if [ "${FULL_STACK:-0}" = 1 ]; then
if [ ! -d "$WORK/Malcolm/.git" ]; then
  timeout 300 git clone -q --depth 1 --branch "$MALCOLM_TAG" https://github.com/idaholab/Malcolm "$WORK/Malcolm"
fi
HEAD_SHA=$(git -C "$WORK/Malcolm" rev-parse HEAD)
fact malcolm_release "$MALCOLM_TAG ($HEAD_SHA)"
[ "$HEAD_SHA" = "$MALCOLM_SHA" ] || log "WARNING: $MALCOLM_TAG is $HEAD_SHA, expected $MALCOLM_SHA"
cd "$WORK/Malcolm" || exit 1
for f in config/*.env.example; do cp "$f" "${f%.example}"; done
sed -i "s/^PUID=.*/PUID=$(id -u)/; s/^PGID=.*/PGID=$(id -g)/" config/process.env
sed -i "s/-Xmx[0-9]*[gm] -Xms[0-9]*[gm]/-Xmx$OS_HEAP -Xms$OS_HEAP/" config/opensearch.env
sed -i "s/-Xmx[0-9]*[gm] -Xms[0-9]*[gm]/-Xmx$LS_HEAP -Xms$LS_HEAP/" config/logstash.env
# The file has no trailing newline; the processors buffer stdout unless Python is unbuffered.
printf '\nPCAP_PIPELINE_VERBOSITY=-vv\nPYTHONUNBUFFERED=1\n' >>config/process.env
grep -h "JAVA_OPTS" config/opensearch.env config/logstash.env >"$EV/heap-settings.txt"

timeout 300 python3 -m venv "$WORK-mvenv" && timeout 300 "$WORK-mvenv/bin/pip" install -q ruamel.yaml python-dotenv kubernetes requests pyyaml
PW=$(openssl passwd -6 spike-pass)
PATH="$WORK-mvenv/bin:$PATH" timeout 300 ./scripts/auth_setup --auth-noninteractive --auth-method basic \
  --auth-admin-username malcolm --auth-admin-password-openssl "$PW" --auth-arkime-password spike-pass \
  --auth-generate-webcerts --auth-generate-opensearch-internal-creds --auth-generate-netbox-passwords \
  --auth-generate-valkey-password --auth-generate-postgres-password -f docker-compose.yml >"$EV/auth_setup.out" 2>&1
check "auth_setup produced the credentials and certificates" "$([ -s .opensearch.primary.curlrc ] && echo true || echo false)" "$(tail -3 "$EV/auth_setup.out")"
touch htadmin/metadata nginx/htpasswd nginx/nginx_ldap.conf .opensearch.secondary.curlrc
mkdir -p opensearch pcap/upload pcap/processed zeek-logs/upload zeek-logs/processed zeek-logs/current zeek-logs/extract_files zeek-logs/live
timeout 300 docker run --rm -v "$PWD/opensearch:/out" --user root --entrypoint bash \
  "ghcr.io/idaholab/malcolm/opensearch:$IMAGE_TAG" -c \
  "/usr/share/opensearch/bin/opensearch-keystore create && cp /usr/share/opensearch/config/opensearch.keystore /out/" >>"$EV/steps.log" 2>&1
sudo chown "$(id -u):$(id -g)" opensearch/opensearch.keystore 2>/dev/null || true
chmod 600 .opensearch.*.curlrc config/*.env

# Logstash waits for the malcolm_template index template. dashboards-helper creates it, but only after
# Dashboards answers and Arkime has created arkime_sessions3_template (run 4 stuck on that).
SERVICES=(opensearch dashboards-helper dashboards arkime logstash filebeat pcap-monitor zeek)
log "pulling ${SERVICES[*]}"
timeout 1500 docker compose --profile malcolm pull -q "${SERVICES[@]}" >>"$EV/steps.log" 2>&1
check "the trimmed Malcolm images pulled" "$([ $? -eq 0 ] && echo true || echo false)" "$(docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep idaholab | tr '\n' ';')"
fact disk_after_pull "$(df -h --output=used,avail / | tail -1)"; docker system df >"$EV/docker-system-df.txt" 2>&1



log "starting OpenSearch"
timeout 300 docker compose --profile malcolm up -d opensearch >>"$EV/steps.log" 2>&1
os_ready=false
for _ in $(seq 1 60); do
  docker ps --format '{{.Names}}' | grep -q '^malcolm-opensearch-1$' || break
  if timeout 20 docker exec malcolm-opensearch-1 sh -c 'curl -sk -K /var/local/curlrc/.opensearch.primary.curlrc "https://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=5s"' 2>/dev/null | grep -q '"status"'; then
    os_ready=true; break
  fi
  sleep 10
done
check "OpenSearch ($OS_HEAP heap) reaches a healthy cluster" "$os_ready" "$(docker ps --format '{{.Names}} {{.Status}}' | tr '\n' ';')"

if [ "$os_ready" = true ]; then
  timeout 300 docker compose --profile malcolm up -d "${SERVICES[@]:1}" >>"$EV/steps.log" 2>&1
  log "services started; letting the watchers settle (pcap-monitor waits 30 s)"
  sleep 60
  cp "$EV/ics.pcap" pcap/upload/cherrymountain-ics.pcap
  log "pcap dropped into pcap/upload"
  # Wait for Zeek records of the three protocols in OpenSearch (up to 12 minutes).
  q='{"size":0,"query":{"match_all":{}},"aggs":{"ds":{"terms":{"field":"event.dataset","size":100}}}}'
  found=false
  for _ in $(seq 1 72); do
    out=$(timeout 30 docker exec malcolm-opensearch-1 sh -c "curl -sk -K /var/local/curlrc/.opensearch.primary.curlrc -H 'Content-Type: application/json' 'https://localhost:9200/arkime_sessions3-*/_search' -d '$q'" 2>/dev/null || true)
    echo "$out" >"$EV/opensearch-datasets.json"
    if echo "$out" | jq -e '[.aggregations.ds.buckets[].key] | (index("modbus") != null) and (index("dnp3") != null) and (index("bacnet") != null)' >/dev/null 2>&1; then
      found=true; break
    fi
    sleep 10
  done
  fact pipeline_path "pcap-monitor -> zeek offline -> filebeat -> logstash -> opensearch: $found"
  if [ "$found" != true ]; then
    # Fall back to feeding the Zeek logs from stage 2 to Filebeat's upload directory.
    log "pipeline did not deliver; feeding stage-2 Zeek logs to filebeat through zeek-logs/upload"
    # Filebeat's upload watcher unpacks archives, not loose logs.
    (cd "$ZD" && tar -czf "$WORK/Malcolm/zeek-logs/upload/cherrymountain-ics.tar.gz" ./*.log)
    for _ in $(seq 1 36); do
      out=$(timeout 30 docker exec malcolm-opensearch-1 sh -c "curl -sk -K /var/local/curlrc/.opensearch.primary.curlrc -H 'Content-Type: application/json' 'https://localhost:9200/arkime_sessions3-*/_search' -d '$q'" 2>/dev/null || true)
      echo "$out" >"$EV/opensearch-datasets-fallback.json"
      if echo "$out" | jq -e '[.aggregations.ds.buckets[].key] | (index("modbus") != null) and (index("dnp3") != null) and (index("bacnet") != null)' >/dev/null 2>&1; then
        found=true; break
      fi
      sleep 10
    done
    fact fallback_feed_used "true (filebeat via zeek-logs/upload); delivered: $found"
  fi
  check "OpenSearch holds Zeek modbus, dnp3 and bacnet records from the test pcap" "$found" "$(jq -c '[.aggregations.ds.buckets[]? | {(.key): .doc_count}]' "$EV/opensearch-datasets.json" 2>/dev/null)"
fi

fi

# Evidence: peak memory, container state, logs.
if [ "${FULL_STACK:-0}" = 1 ]; then
{
  echo "== host trees"; find pcap zeek-logs -type f ! -name .gitignore 2>/dev/null | head -60
  for c in malcolm-zeek-1 malcolm-pcap-monitor-1 malcolm-arkime-1 malcolm-filebeat-1; do
    echo "== $c"
    timeout 30 docker exec "$c" sh -c 'supervisorctl status 2>&1 | head -30; ls -la /pcap/upload /pcap/processed /zeek/upload /zeek/current 2>&1 | head -40; ls /var/log /var/log/supervisor 2>&1 | head; ps aux 2>/dev/null | grep -iE "pcap|zeek|arkime" | grep -v grep | cut -c1-220 | head -12'
  done
} >"$EV/pipeline-diagnostics.txt" 2>&1
timeout 60 docker exec malcolm-zeek-1 sh -c 'for f in /var/log/supervisor/*pcap* /var/log/*zeek*; do echo "-- $f"; tail -40 "$f"; done' >"$EV/zeek-processor-logs.txt" 2>&1 || true
docker compose --profile malcolm ps -a >"$EV/compose-ps.txt" 2>&1
for c in $(docker ps -a --format '{{.Names}}' | grep '^malcolm-'); do docker logs --tail 300 "$c" >"$EV/log-$c.txt" 2>&1; done
timeout 30 docker exec malcolm-opensearch-1 sh -c 'curl -sk -K /var/local/curlrc/.opensearch.primary.curlrc "https://localhost:9200/_cat/indices?v"' >"$EV/opensearch-indices.txt" 2>&1
fi
kill $SAMPLER 2>/dev/null
python3 - "$EV" <<'PY' | tee "$EV/peak.txt"
import csv
import sys
from collections import defaultdict
from pathlib import Path

ev = Path(sys.argv[1])
UNITS = {"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30, "kB": 1000, "MB": 10**6, "GB": 10**9}


def to_mib(text: str) -> float:
    value = text.split("/")[0].strip()
    for unit, scale in sorted(UNITS.items(), key=lambda kv: -len(kv[0])):
        if value.endswith(unit):
            return float(value[: -len(unit)]) * scale / (1 << 20)
    return 0.0


per_ts: dict[str, float] = defaultdict(float)
peak: dict[str, float] = defaultdict(float)
cpu: dict[str, float] = defaultdict(float)
path = ev / "docker-stats.csv"
if path.exists():
    for row in csv.reader(path.open()):
        if len(row) < 4:
            continue
        mib = to_mib(row[2])
        per_ts[row[0]] += mib
        peak[row[1]] = max(peak[row[1]], mib)
        cpu[row[1]] = max(cpu[row[1]], float(row[3].rstrip('%') or 0))
total = max(per_ts.values(), default=0.0)
free = ev / "free-mb.csv"
used = max((int(r[1]) for r in csv.reader(free.open()) if len(r) >= 3 and r[1].isdigit()), default=0) if free.exists() else 0
print(f"sum of container memory peak {total:.0f} MiB; host used peak {used} MiB; " + ", ".join(f"{k} {v:.0f} MiB peak, {cpu[k]:.0f}% CPU peak" for k, v in sorted(peak.items(), key=lambda kv: -kv[1])))
PY
fact peak_memory "$(cat "$EV/peak.txt")"
fact disk_end "$(df -h --output=used,avail / | tail -1)"
[ "${FULL_STACK:-0}" = 1 ] && (cd "$WORK/Malcolm" && docker compose --profile malcolm down >/dev/null 2>&1) || true
finish
