#!/bin/bash
# phenix startup hook on the historian (cherrymountain app). --no-block: this
# script runs inside phenix.service during boot, and provisioning waits for
# InfluxDB. Telegraf and Grafana read the token through their drop-ins, so they
# restart once the drop-ins are in place.
set -euo pipefail
systemctl daemon-reload
systemctl enable --now --no-block cm-historian-provision.service
systemctl try-restart --no-block telegraf.service grafana-server.service mosquitto.service
