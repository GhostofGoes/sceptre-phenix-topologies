#!/bin/bash
# phenix startup hook on sim-process. Startup scripts run in name order, so
# 5-wait-broker.sh (added by the helics app) has already waited for sim-ctl.
# --no-block: this script runs inside phenix.service during boot.
systemctl daemon-reload
systemctl enable --now --no-block cm-cellar.service
