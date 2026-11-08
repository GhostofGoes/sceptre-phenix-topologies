# Spike (stretch): Modbus RTU over the phenix serial app

**Question.** Does Modbus RTU between rio-utilities and glycol-chiller work
over a phenix `type: serial` interface, with ot-sim as both ends? **Decides:**
whether to ship the RS-485 link in §15.2 or drop it to plain Modbus TCP.
Low priority; skip if Phase 0 is late.

## What the phenix source says

The `serial` app does not use minimega's serial passthrough. For each
`type: serial` interface it injects a startup script that runs `socat` to
bridge a pty (the `Device` link, baud rate) to UDP multicast `224.1.0.1` on
the interface's VLAN, and adds a multicast route (`serial_startup.tmpl`).
Guests need `socat`. Because the transport is multicast UDP, every guest on
that VLAN sees every frame (a shared RS-485 bus), and there is no flow
control. The YAML key names for the interface fields are unverified (see the
`TODO(verify)` in `topology.yml`).

## Prerequisites

- Guest images with `socat`, python3, pymodbus/pyserial, and ot-sim.
- A VLAN that passes multicast between the two guests (plain OVS VLAN does).

## Steps

1. Fix the interface keys in `topology.yml` against phenix's schema; start
   the experiment with `scenario.yml`.
2. On each guest, confirm the pty exists (`ls -l /dev/ttyS10`) and `socat`
   is running (`pgrep -a socat`; log `/tmp/socat.log`).
3. Raw echo: on one guest `cat /dev/ttyS10`, on the other `echo hello >
   /dev/ttyS10`. Expect `hello`.
4. Library test: `./rtu_check.py server /dev/ttyS10` on glycol-chiller, then
   `./rtu_check.py client /dev/ttyS10` on rio-utilities.
5. ot-sim test: configure an ot-sim Modbus module as RTU server on the chiller
   and as RTU master on the RIO (TODO(verify): serial options in ot-sim's
   Modbus module config); confirm a value written on one side appears on the
   other, and measure loss over 10 minutes at 9600 baud with a 1 s poll.
6. Capture: `tcpdump -ni IF0 udp port 9000` on a third guest on the VLAN to
   see the bus frames (what Malcolm would see; ICSNPP has no RTU parser,
   TODO(verify)).

## Pass / fail

- [ ] pty and socat come up on both guests at boot, no manual steps.
- [ ] Raw echo works both directions.
- [ ] RTU round trip passes with pymodbus.
- [ ] ot-sim RTU master/slave exchange values with under 1% poll failures.

Items 1-3 pass but ot-sim lacks RTU: **no-go for ot-sim RTU**; consider a
small Python RTU gateway on the chiller instead, or drop to Modbus TCP.

## Result

- **Decision:** go / no-go (drop to Modbus TCP)
- **Evidence:** (socat log, rtu_check output, loss figure)
- **Follow-ups:** (interface key names, ot-sim serial config)
