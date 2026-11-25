#!/usr/bin/env bash
# Run ON a guest: serve a one-line banner on each port given (stand-ins for
# Grafana 3000, event API 8080, OpenSearch 9200, FUXA 1881).
# Usage: ./serve_stubs.sh 3000 8080 9200      (stays in the foreground)
set -euo pipefail

host=$(hostname)
for port in "$@"; do
  dir=$(mktemp -d)
  echo "stub ${host}:${port}" >"${dir}/index.html"
  (cd "${dir}" && python3 -m http.server "${port}" --bind 0.0.0.0 >/dev/null 2>&1) &
done
echo "serving on: $*"
wait
