#!/bin/sh
# phenix image build script for the headless guest the Cherry Mountain VM
# spikes boot (cm-spike): openvswitch for the mirror capture check, socat for
# the serial app, tcpdump and python3 for the checks run through cc, and
# BACpypes3 for the BBMD spike.
set -ex

# Experiment networks are often unrouted; do not hold the boot waiting for a
# network to come online (same as the phenix e2e image).
systemctl mask systemd-networkd-wait-online.service

# The distribution starts openvswitch-switch at boot; the mirror spike needs
# the bridge the mirror app's startup script creates.
systemctl enable openvswitch-switch.service || true

# BACnet BBMD spike: BACpypes3 for the BBMDs, the device 3003 responder and
# the Who-Is client, in a virtualenv so the Debian packages stay untouched.
python3 -m venv /opt/bacnet
/opt/bacnet/bin/pip install --no-cache-dir bacpypes3==0.0.110
/opt/bacnet/bin/python -c 'import bacpypes3'

python3 --version
command -v socat tcpdump ovs-vsctl
