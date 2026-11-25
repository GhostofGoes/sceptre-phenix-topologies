#!/bin/bash
# phenix startup hook on the Wazuh VM (DESIGN.md 14). The first boot makes the
# certificates and passwords and starts the indexer, the manager, Filebeat, and the
# dashboard (cm-wazuh-provision); later boots find the services enabled. The app's
# 80-cm-wazuh-manager.sh runs later and turns on password enrollment.
# --no-block: this script runs inside phenix.service during boot, and the first
# provisioning takes several minutes.
systemctl daemon-reload
systemctl enable --now --no-block cm-wazuh-provision.service
