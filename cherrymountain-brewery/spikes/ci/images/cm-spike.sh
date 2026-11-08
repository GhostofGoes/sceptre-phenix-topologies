#!/bin/sh
# phenix image build script for the headless guest the Cherry Mountain VM
# spikes boot (cm-spike): openvswitch for the mirror capture check, socat for
# the serial app, tcpdump and python3 for the checks run through cc.
set -ex

# Experiment networks are often unrouted; do not hold the boot waiting for a
# network to come online (same as the phenix e2e image).
systemctl mask systemd-networkd-wait-online.service

# The distribution starts openvswitch-switch at boot; the mirror spike needs
# the bridge the mirror app's startup script creates.
systemctl enable openvswitch-switch.service || true

python3 --version
command -v socat tcpdump ovs-vsctl
