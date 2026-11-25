#!/bin/bash
# phenix startup hook on ot-svc and bas-bbmd. --no-block: this script runs
# inside phenix.service during boot.
systemctl daemon-reload
systemctl enable --now --no-block cm-bbmd.service
