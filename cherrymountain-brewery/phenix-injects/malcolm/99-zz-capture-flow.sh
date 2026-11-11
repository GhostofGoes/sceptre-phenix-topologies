#!/bin/bash
# phenix startup hook on malcolm (DESIGN.md 14, capture requirement 1). The mirror
# app's setupOVS script (99-setup-ovs.sh, which sorts before this one) creates the
# in-guest bridge `phenix` and a `sniffer` GRE port. The stock bridge learns the
# mirrored MACs on `sniffer` and then drops frames for them, so only the first
# flooded frames reach the capture port. cm-malcolm-capture adds the OpenFlow flow
# that sends everything from `sniffer` to the bridge's local port, and brings that
# port up. A unit does it, not this script, so that it waits for the bridge, is
# retried, and runs again when Open vSwitch restarts. It goes away once the mirror
# app does it (DESIGN.md 16). --no-block: this script runs inside phenix.service
# during boot.
systemctl daemon-reload
systemctl enable --now --no-block cm-malcolm-capture.service
