#!/usr/bin/env bash
# Take every screenshot of the Examples section into OUT (default: the
# docs/examples directory above this one). See README.md in this directory.
#
#   FUXA_SERVER=... FUXA_PKG=... capture_all.sh [viz|fuxa|hmi|brand ...]
#
# With no argument it runs all four groups. Environment: FUXA_SERVER and
# FUXA_PKG (required for fuxa and hmi), CM_ROOT, WORK, OUT, PYTHON, and
# SHRINK_PYTHON (a Python with Pillow, for shrink_pngs.py; default PYTHON).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export CM_ROOT=${CM_ROOT:-$(cd "${HERE}/../../.." && pwd)}
export WORK=${WORK:-/tmp/cm-examples}
export PYTHONDONTWRITEBYTECODE=1
OUT=${OUT:-$(cd "${HERE}/.." && pwd)}
PY=${PYTHON:-python3}
SNAP="${WORK}/snapshots"
RUN="${HERE}/fuxa_run.sh"
OPERATOR_PW='Oper8tor-example-pw'
mkdir -p "${OUT}" "${SNAP}"
trap '"${RUN}" down 2>/dev/null || true' EXIT

groups=("$@"); [ ${#groups[@]} -gt 0 ] || groups=(viz fuxa hmi brand)
want() { local g; for g in "${groups[@]}"; do [ "$g" = "$1" ] && return 0; done; return 1; }

snapshot() {  # SCENARIO -> ${SNAP}/SCENARIO.json (made once per run of this script)
  [ -s "${SNAP}/$1.json" ] && [ -z "${REMAKE_SNAPSHOTS:-}" ] || "${PY}" "${HERE}/fuxa_snapshot.py" "$1" "${SNAP}/$1.json"
}

# shots BASE_PORT WIDTH HEIGHT JSON_LIST_OF_SHOTS
shots() {
  node "${HERE}/fuxa_shots.cjs" "{\"base\":\"http://127.0.0.1:$1\",\"out\":\"${OUT}\",\"width\":$2,\"height\":$3,\"shots\":$4}" | cut -c1-160
}

if want viz; then
  "${PY}" "${HERE}/viz_shots.py" "${OUT}" | cut -c1-120
fi

if want fuxa; then
  : "${FUXA_SERVER:?set FUXA_SERVER}" "${FUXA_PKG:?set FUXA_PKG}"
  for s in midday co2-attack spunding-attack outage packaging-run packaging-jam; do snapshot "$s"; done

  # A graphic's pointers and fills move for about 9 s after the view opens, and its
  # trends load FUXA's stored history a little later, so every view waits 10 s or more.

  # The plant at 11:00 on a working day, nothing injected.
  "${RUN}" up scada "${SNAP}/midday.json" | tail -1
  shots 11881 1440 900 '[
    {"name":"fuxa-scada-overview","view":"Overview","wait":14000},
    {"name":"fuxa-scada-brewhouse","view":"Brewhouse","wait":12000},
    {"name":"fuxa-scada-cellar","view":"Cellar","wait":12000},
    {"name":"fuxa-scada-utilities","view":"Utilities","wait":10000},
    {"name":"fuxa-scada-power","view":"Power","wait":12000},
    {"name":"fuxa-scada-cellar-co2","view":"Cellar CO2","wait":12000},
    {"name":"fuxa-scada-sign-in","view":"Overview","login":true,"wait":15000}]'
  shots 11881 1440 1620 '[{"name":"fuxa-scada-cellar-points","view":"Cellar points"}]'

  # The packaging line running, at 08:00 (the cellar crew has put beer in BBT-202).
  "${RUN}" up scada "${SNAP}/packaging-run.json" | tail -1
  shots 11881 1440 900 '[{"name":"fuxa-scada-packaging","view":"Packaging","wait":14000}]'

  # baseline-brew-day: the real batch engine brewing against the stand-in brewhouse PLC.
  # The values are the plant's at 11:00; the phase, step, and batch tags are the
  # brewhouse PLC port's. A phase takes about 18 s, so both pictures, taken 25 s
  # after the request, show the brew in its first two phases.
  "${RUN}" up scada "${SNAP}/midday.json" live | tail -1
  sleep 12
  curl -sS "http://127.0.0.1:18181/request?n=1" > /dev/null
  sleep 25
  shots 11881 1440 900 '[
    {"name":"fuxa-scenario-brew-day-brewhouse","view":"Brewhouse","wait":3000},
    {"name":"fuxa-scenario-brew-day-batch","view":"Batch","wait":2000}]'

  # cellar-co2-leak with the purge fans stopped (the co2-attack of the viz scenarios).
  # FUXA raises a "high" CO2 alarm only after 30 s above its limit, so these two
  # pictures wait 40 s: by then the flags and the bell agree. The alarm list is
  # closed, because it would cover the trend (Cellar CO2) and the cellar's sensors
  # (Building); the spunding and outage pictures below have it open.
  "${RUN}" up scada "${SNAP}/co2-attack.json" | tail -1
  shots 11881 1440 900 '[{"name":"fuxa-scenario-co2-attack-scada","view":"Cellar CO2","wait":40000}]'
  "${RUN}" up bas "${SNAP}/co2-attack.json" | tail -1
  shots 11882 1440 900 '[{"name":"fuxa-scenario-co2-attack-bas","view":"Building","wait":40000}]'

  # A rogue Modbus master raises FV-102's spunding setpoint to 30 psig.
  "${RUN}" up scada "${SNAP}/spunding-attack.json" | tail -1
  shots 11881 1440 900 '[{"name":"fuxa-scenario-spunding-attack-cellar","view":"Cellar","alarms":true,"wait":12000}]'

  # utility-outage: the utility breaker open, the genset carrying the emergency loads.
  "${RUN}" up scada "${SNAP}/outage.json" | tail -1
  shots 11881 1440 900 '[{"name":"fuxa-scenario-utility-outage-power","view":"Power","alarms":true,"wait":12000}]'

  # packaging-shift: the labeler and the filler jammed.
  "${RUN}" up scada "${SNAP}/packaging-jam.json" | tail -1
  shots 11881 1440 900 '[{"name":"fuxa-scenario-packaging-jam","view":"Packaging","wait":14000}]'

  # The building automation view, no event.
  "${RUN}" up bas "${SNAP}/midday.json" | tail -1
  shots 11882 1440 900 '[{"name":"fuxa-bas-building","view":"Building","wait":12000}]'
  "${RUN}" down
fi

if want hmi; then
  : "${FUXA_SERVER:?set FUXA_SERVER}" "${FUXA_PKG:?set FUXA_PKG}"
  snapshot midday; snapshot packaging-jam
  # The brewhouse station during a brew: the batch handshake is live, as for the
  # Batch picture above, and the kiosk picture is taken about 35 s after the request.
  "${RUN}" up scada "${SNAP}/midday.json" live | tail -1
  sleep 12
  curl -sS "http://127.0.0.1:18181/request?n=1" > /dev/null
  sleep 15
  OPERATOR_PW="${OPERATOR_PW}" "${HERE}/hmi_kiosk.sh" Brewhouse "${OUT}/hmi-brewhouse-station.png"
  "${RUN}" up scada "${SNAP}/packaging-jam.json" | tail -1
  OPERATOR_PW="${OPERATOR_PW}" "${HERE}/hmi_kiosk.sh" Packaging "${OUT}/hmi-packaging-station-jam.png"
  "${RUN}" down
fi

# FUXA's and the kiosk's pictures: a 256-color palette keeps the set small.
if want fuxa || want hmi; then
  shopt -s nullglob
  "${SHRINK_PYTHON:-${PY}}" "${HERE}/shrink_pngs.py" "${OUT}"/fuxa-*.png "${OUT}"/hmi-*.png > /dev/null
  shopt -u nullglob
fi

if want brand; then
  cp "${CM_ROOT}/brand/wallpaper-1080p.png" "${OUT}/brand-wallpaper-1080p.png"
fi
du -cb "${OUT}"/*.png | tail -1
