#!/usr/bin/env bash
# Start the rio-cellar stack inside the ot-sim container (run by
# ci_slice_docker.sh): HELICS broker, the ot-sim CPU module (message bus,
# Modbus outstation, HELICS I/O module), and one process that drives the plant.
#
# Usage: rio_start.sh real|fault
#   real   the real cherrymountain_sim cellar federate
#   fault  fault_federate.py, which is the same model with FV-101 head pressure
#          forced above the high alarm limit
# Paths (container side): /cm/ot-sim.xml, /cm/helics.json, /cm/sim (package
# source), /cm/fault_federate.py. Logs go to /var/log/cm-*.log.
set -euo pipefail

mode="${1:?usage: rio_start.sh real|fault}"
export PATH="/opt/otsim-venv/bin:${PATH}"
export PYTHONPATH=/cm/sim
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"

# Two federates join the broker: the plant model and rio-cellar's I/O module.
nohup helics_broker --federates=2 --coretype=zmq --loglevel=summary \
  >/var/log/cm-broker.log 2>&1 &
sleep 2
nohup ot-sim-cpu-module /cm/ot-sim.xml >/var/log/cm-otsim.log 2>&1 &
sleep 3
case "${mode}" in
  real)
    nohup python -m cherrymountain_sim --config /cm/helics.json \
      --broker-address 127.0.0.1 --log-level INFO >/var/log/cm-plant.log 2>&1 &
    ;;
  fault)
    nohup python /cm/fault_federate.py --config /cm/helics.json \
      --broker-address 127.0.0.1 >/var/log/cm-plant.log 2>&1 &
    ;;
  *) echo "unknown mode ${mode}" >&2; exit 2 ;;
esac
echo "rio_start: ${mode} stack started"
