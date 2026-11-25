#!/usr/bin/env bash
# Start the Luanti client on a fresh world under Xvfb (DISPLAY, default :99),
# follow the mod's tour (cm_world.tour in CONF), and take a screenshot each
# time the tour file changes. usage: shoot_tour.sh OUTDIR CONF [TIMEOUT_S]
set -euo pipefail
out=$(realpath -m "$1"); conf=$(realpath "$2"); timeout_s=${3:-400}
here=$(cd "$(dirname "$0")/.." && pwd)
client=${CM_CLIENT:-/usr/games/minetest}
export DISPLAY=${DISPLAY:-:99}
mkdir -p "$out"
world=$(mktemp -d)/world; mkdir -p "$world/worldmods"
printf 'gameid = minetest\nbackend = sqlite3\nplayer_backend = sqlite3\nauth_backend = sqlite3\ncreative_mode = true\nenable_damage = false\n' > "$world/world.mt"
printf 'mg_name = singlenode\nseed = 1\n[end_of_params]\n' > "$world/map_meta.txt"
cp -r "$here/mod/cm_world" "$world/worldmods/"
"$client" --config "$conf" --world "$world" --gameid minetest --go --name whitecell --logfile "$out/client.log" >/dev/null 2>&1 &
pid=$!
last=""; n=0; t0=$(date +%s)
while kill -0 $pid 2>/dev/null; do
  cur=$(cat "$world/cm_world_tour.txt" 2>/dev/null || true)
  if [[ -n "$cur" && "$cur" != "$last" ]]; then
    last=$cur
    [[ "$cur" == "done" ]] && break
    n=$((n+1)); name=$(printf '%02d-%s' $n "${cur%% *}" | tr '@' '-')
    import -window root "$out/$name.png"
    cp "$world/cm_world_status.json" "$out/$name.status.json" 2>/dev/null || true
    echo "$(($(date +%s)-t0))s $name ($cur)"
  fi
  (( $(date +%s) - t0 > timeout_s )) && break
  sleep 0.5
done
kill -9 $pid 2>/dev/null || true
