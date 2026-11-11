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

Round 2 (ot-sim's own Modbus RTU) is first; round 1 (stand-in RTU client and server) follows.

### Round 2: ot-sim's own Modbus RTU over the serial app

- **Decision:** **go.** ot-sim supports Modbus RTU natively, as master and as
  server, and both ran over the phenix serial app's pty link: the master read
  the server's changing value, a write made on the master's side reached the
  server's holding register, and the master logged **0 errors in about 680
  register polls** at 1 s period and 9600 baud. ot-sim needs no change, so no
  fork branch was made. The earlier "ot-sim lacks RTU: no-go" branch of the
  pass/fail list does not apply.
- **What ot-sim has** (read at commit `9dc33047ff9fb474e61c8931c7dac7bf98df38ef`,
  the pin of the otsim-noble spike): `src/go/modbus/server/server.go` and
  `client/client.go` accept a `<serial>` block (`device`, `baud-rate`,
  `data-bits`, `stop-bits`, `parity`, `timeout`) in place of `<endpoint>` and
  use `goburrow/serial` with `actshad.dev/modbus` (RTU client) and
  `actshad.dev/mbserver` (`ListenRTU`). The server takes `<id>` for the unit,
  the client `<unit-id>` and `<period>`. A server block may have both
  `<endpoint>` and `<serial>`.
- **Fixture.** `build_otsim.sh` builds, on the runner, the message bus (one C
  file, `gcc`, no CMake), the CPU, Modbus and logic modules (Go, cgo against
  libzmq) and bundles the libraries they need into a 24 MB tarball
  (`/phenix/injects/otsim-rtu.tgz`); `topology.yml` injects it into both guests
  (the cm-spike image has none of those libraries and the guests have no
  network). `otsim-chiller.xml` runs on glycol-chiller: an RTU server on
  `/dev/ttyS10` with an input register 30000 (a logic program ramps 20 to 120)
  and a holding register 40000, plus a loopback TCP server. `otsim-rio.xml` runs
  on rio-utilities: an RTU master polling both registers every second and a
  loopback TCP server that shows what it read and takes a write. `otsim_check.py`
  (stdlib) reads and writes those loopback servers.
- **Evidence** (run [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420), job "Spike serial", artifact
  `evidence-serial`; phenix `8b303da`; all 16 checks PASS, the round 1 ones
  again included):
  - All four ot-sim processes (message bus, CPU, Modbus and logic modules) ran on
    the chiller and three on rio-utilities; the master published
    `rio-master.connected = 1`.
  - Loopback reads on rio-utilities over 300 s: 300 samples, **0 errors, 101
    distinct values** (the 100-step ramp), first 79, 80, 81, last 73, 74, 75.
  - Write: `write 40000 77` on rio's loopback server; the chiller's server read
    back `{"value": 77}` inside the 16 s polling window of the check (poll interval 1 s).
  - Master log: 0 `ERROR` lines for about 680 register polls (0.00%).
  - The bus monitor saw 1366 multicast datagrams while ot-sim ran.
- **Notes for the module configs:** `type="input"` and `"holding"` addresses are
  raw (30000 and 40000 worked as written); there are no success logs, only
  `[ERROR] reading ...` lines, so count errors; the client's tag values
  also appear on its own bus, so a loopback `<modbus mode="server">` block in the
  same file is an easy readback; ot-sim's `[LOG]` lines carry the tag updates.
- **Follow-ups:**
  - The serial app's link is still socat over UDP multicast (no baud pacing),
    so the poll-loss figure says nothing about a real RS-485 bus (unchanged).
  - `cm-otsim` for the RTU nodes needs libzmq5, libczmq4 and libxml2 (and their
    dependencies); the tarball shows which.
  - Phenix PR #130 variant: see below.

### Round 2b: the serial app of phenix PR #130 (QEMU sockets and socat on the host)

Variant spike of [phenix PR #130](https://github.com/sandialabs/sceptre-phenix/pull/130) (draft, `activeshadow:serial-app`, head
`f832ad5`): the `serial` app gives each VM a QEMU `pci-serial` device on a chardev
socket (`/tmp/<exp>_serial_<vm>_<idx>`) and starts one `socat` on the minimega
host between the two sockets (TCP only across hosts). The fork has no e2e setup
in that branch, so `pr130-serial.patch` applies the PR's `src/go/app/serial.go`
and `util/mm` changes to the fork's phenix (it applies on the fork's `041b001` and on
its head, and the app and `mm` packages build); `topology-pr130.yml` has no
`type: serial` interface and `scenario-pr130.yml` lists
`connections: [{src: rio-utilities, dst: glycol-chiller}]`. Job "Spike serial
(phenix PR 130)", `ci_pr130.py`; the redeploy outcome is recorded as facts, so the job's checks are the link and ot-sim.

- **Decision:** the app works for the link; the redeploy case fails, as the PR's
  own review items say. Stopped after three runs (the cap for this spike).
  - **Works** (runs 42545422322 and 42547389340, same results): the host `socat` between the
    two sockets runs after the experiment starts; both guests see a new UART at
    `/dev/ttyS4` (`/sys/class/tty/ttyS4/type` 4, the console is ttyS0); raw bytes
    cross in both directions (the receiving port echoes the word back because it is not in
    raw mode, so the sender's `cat` also shows `lo-from-rio`; ot-sim sets raw mode itself);
    **ot-sim's Modbus RTU master and server over `/dev/ttyS4`: the write arrived,
    300 loopback samples with 0 errors and 101 distinct values in both runs, and
    0 errors in about 680 polls in the first and 1 in the second** (`reading input
    30000 from /dev/ttyS4: serial: timeout`, 0.15%).
  - **Fails: a VM redeploy.** After `vm kill glycol-chiller` the host `socat`
    exited (minimega log: `command [... socat ...] exited`) and nothing started it
    again; after `vm start` the chiller (state RUNNING, new QEMU with its socket
    file) did not register its C2 client within 300 s (42545422322) or 420 s (42547389340), so
    the link could not even be tried. Whether the missing C2 client comes from the
    PR or from killing and starting that VM is not decided (the autotunnel spike's
    kill and start of a VM brought its C2 client back); it was not isolated.
- **What failed in each run:**
  1. Run [42543352420](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42543352420): my driver. `ot-sim` setup asked for a
     `bus-monitor` VM that the PR fixture does not have (`KeyError:
     'bus-monitor'`). Raw echo had passed. Not a PR issue.
  2. Run [42545422322](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42545422322): everything up to ot-sim passed; then `vm kill`
     ended the `socat` and the chiller never returned (TimeoutError after 300 s).
     Matches the PR's review item **no reconnect after VM redeploy**; the missing C2
     client is unexplained.
  3. Run [42547389340](https://github.com/GhostofGoes/sceptre-phenix-topologies/actions/runs/42547389340): the same, with the driver recording the VM state
     (RUNNING), the QEMU command line and the sockets instead of dying: no C2 client
     after 420 s, no `socat`, and the chiller's socket file recreated at start.
- **Review items against the evidence:**
  - *No reconnect after VM redeploy*: **confirmed** (`socat` is gone after the
    kill).
  - *No wait or retry for listener readiness*: not triggered (the sockets existed
    when PostStart ran on a single host); the code connects once.
  - *Host-wide unique ports*: not exercised (one host; the TCP port is used only
    across hosts, default 40500 + index). The UNIX socket and log names include the
    experiment name, so single-host runs of two experiments do not collide.
  - *DryRun handling*, the TODO in `soh.go` and socat in the minimega container:
    not exercised. `socat` must be on the minimega host: the runner needed it
    installed (the job does).
  - **New observations:** the PR starts `socat` with `-d -d -d -d`, which writes
    every transferred byte as a hex dump to `/tmp/<exp>_serial_*.log`: 3.9 MB and
    4.4 MB after the 5-minute ot-sim run, unbounded for a long range. Both ends of
    the chardev are `server,nowait` sockets, so a VM boots whether or not `socat`
    is connected; bytes sent before `socat` connects are lost. The PR keeps the
    guest-side `type: serial` support as well, so a topology must not use both for
    one node.
- **Follow-ups:** report the three items above to the PR (reconnect after
  redeploy, log volume, C2 after redeploy); isolate the missing C2 client with a
  `vm kill` and `vm start` on the same experiment under the current serial app
  (not done: the run cap was reached). The spike stays on the current serial app
  for the range (round 2).

### Round 1: stand-in RTU client and server

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
