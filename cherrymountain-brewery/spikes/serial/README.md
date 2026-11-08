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

- **Decision:** go for the serial app and Modbus RTU framing; ot-sim RTU not
  tested (the fixture uses a stdlib RTU server and client, not ot-sim).
- **Evidence** (run [42511568076](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42511568076),
  job "Spike serial", artifact `evidence-serial`; first run of the harness was
  [42506793255](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42506793255)):
  - phenix `984869d` (fork `ci-e2e-minimega`), minimega 3.3.0, Open vSwitch
    3.3, `cm-spike` guest (Ubuntu noble, socat), `type: serial` interfaces
    with the YAML keys `device`, `baud_rate`, `udp_port` and a `serial` entry
    in the scenario: the keys in `topology.yml` are correct.
  - At boot both guests ran `serial-startup.service`; one `socat` and the
    `/dev/ttyS10` symlink existed on each with no manual step.
  - Raw echo worked in both directions (`hello-from-rio`, `hello-from-chiller`).
  - RTU round trip: read `[111, 222, 333, 444]`, write register 2 = 999 and
    read it back, then 600 polls at 0.5 s with 0 failures (0.00%).
  - A third guest on the SERIAL VLAN saw the bus: 1206 UDP datagrams to
    224.1.0.1:9000 from both ends, so Malcolm-style capture of the bus is
    possible (as UDP multicast, not as RTU frames).
- **Follow-ups:**
  - The link is socat over UDP multicast: there is no baud-rate pacing (the
    `b9600` setting does not slow it) and no collisions, so loss and timing
    figures say nothing about a real RS-485 bus. Say so in DESIGN §15.2.
  - Two readers on one pty steal each other's bytes: an earlier `cat` left
    running by the echo test made the first RTU run fail. Guest scripts must
    own the device.
  - ot-sim's Modbus RTU module still needs the TODO(verify) test on the ot-sim
    image. Capture with `tcpdump -i eth0` (not `-i any`): on `any` the
    capture is not promiscuous and misses multicast frames not addressed to
    the capturing guest.
