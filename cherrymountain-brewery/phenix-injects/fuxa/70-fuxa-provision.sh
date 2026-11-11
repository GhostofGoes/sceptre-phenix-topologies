#!/bin/bash
# phenix startup hook on scada-fuxa. --no-block: this script runs inside
# phenix.service during boot, and provisioning waits for FUXA.
systemctl daemon-reload
systemctl enable --now --no-block cm-fuxa-provision.service
