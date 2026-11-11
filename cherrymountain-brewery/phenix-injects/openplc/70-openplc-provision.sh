#!/bin/bash
# phenix startup hook on plc-cellar. --no-block: this script runs inside
# phenix.service during boot, and provisioning waits for the runtime.
systemctl daemon-reload
systemctl enable --now --no-block cm-openplc-provision.service
