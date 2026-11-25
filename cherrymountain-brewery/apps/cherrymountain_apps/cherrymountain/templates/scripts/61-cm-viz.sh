#!/bin/bash
# phenix startup hook on sim-ctl (cherrymountain app): start cm-viz, the white
# cell's 3D view (DESIGN.md 15.10). It runs after 60-cm-federates.sh, which
# starts the weather federate whose event API cm-viz reads. --no-block: this
# runs inside phenix.service.
set -euo pipefail

[ -f /etc/default/cm-viz ] || { echo "cm-viz: /etc/default/cm-viz is missing" >&2; exit 1; }
if ! /opt/cherrymountain/venv/bin/python -c 'import cherrymountain_viz' 2>/dev/null; then
  echo "cm-viz: cherrymountain_viz is not installed in this image (cm-sim older than the 3D view)" >&2
  exit 0
fi
systemctl daemon-reload
systemctl enable --now --no-block cm-viz.service
