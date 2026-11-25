#!/bin/bash
# phenix startup hook on every PLC (plc-cellar in ci). --no-block: this script runs inside
# phenix.service during boot, and provisioning waits for the runtime.
systemctl daemon-reload
systemctl enable --now --no-block cm-openplc-provision.service
