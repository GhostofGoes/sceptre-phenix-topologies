# Capture scripts for the Examples section

These scripts take the screenshots in [the Examples section of the
README](../../../README.md#examples). Every picture is real software showing the
range's real files; the scripts draw nothing. What is staged is data, and each
caption in the README says which.

| File | What it does |
|---|---|
| `capture_all.sh` | Runs everything below in order and writes the PNGs to `docs/examples/` |
| `viz_shots.py` | cm-viz: runs the offline federation (`LocalSource`), plays a viz scenario, serves the real page, and has headless Chromium take the picture once a condition on the frame holds |
| `fuxa_snapshot.py` | Runs the offline federation to a moment of a scenario and writes the values the stand-in plant serves (see the file's header for which values come from where, including the bottling line's staged PLC outputs) |
| `fuxa_plant.mjs`, `fuxa_s7.cjs` | The stand-in plant: OPC UA (plc-brewhouse), Modbus TCP (plc-cellar, plc-utilities, plc-power), S7 (plc-packaging), and BACnet servers on loopback that serve a snapshot. With `--live-batch` the brewhouse handshake is the tests' port of plc-brewhouse's program (`fuxa/tests/brewhouse_sim.mjs`) |
| `fuxa_prep.py` | Copies a generated FUXA project with only the device addresses changed to the loopback ones |
| `fuxa_run.sh` | Starts the plant and a real FUXA 1.3.6 server, then runs the range's own `cm-fuxa-provision.sh` (admin password, operator account, project import) |
| `fuxa_shots.cjs` | Playwright: opens FUXA views (optionally with the alarm list or the sign-in dialog open) and takes the picture |
| `hmi_kiosk.sh` | Runs the range's own `cm-kiosk` with Chromium in kiosk mode on an Xvfb display of 1440x900 and takes the display |
| `shrink_pngs.py` | Saves the FUXA and HMI pictures with a 256-color palette (about 40 % of the bytes; needs Pillow) |

## Where the values come from

- **cm-viz** runs the real federates in one process (`cherrymountain_sim.local`, no
  HELICS) and plays one of the scenarios of `viz/cherrymountain_viz/scenarios.py`.
  The weather is the simulation's synthetic Denver year, because the TMY3 file is not
  on the machine that took the pictures.
- **FUXA** is a real server with the unchanged `fuxa/full/*-project.json` (only the
  devices' addresses were changed to loopback) and the range's own
  `cm-fuxa-provision.sh`. Behind it a stand-in plant serves values over OPC UA,
  Modbus TCP, S7, and BACnet/IP in place of the PLCs and building controllers. Every
  tag the I/O map ties to a federate value shows that value from the offline
  simulation. The fermentation profile is something the PLC computes, so it is staged
  from the offline cellar's state: the profile is on for all four fermenters; a
  fermenter's stage is 0 when its extract is under 0.5 °P, 1 at 5 °P or more, 3 under
  5 °P with the side temperature under 60 °F, and 2 otherwise; and its setpoints are
  66 °F for stage 0 or 1, 70 °F for stage 2, and 34 °F for stage 3, which is what the PLC
  writes with the profile on. The bright tanks' setpoints are the targets of the
  offline cellar's built-in operator, and the other setpoints are the numbers the PLC
  programs start with. A high, high-high, or state alarm bit is 1 when the simulated
  value is past its limit in `alarms.json` (the PLC's on-delay is not applied; a
  jam alarm stays 0 while the line is Stopped or Aborted, as in the PLC). Low and
  deviation alarms, and everything else a PLC program would compute (batch state,
  the CIP step, entry permits, the power PLC's schedule and load-shed bits), read 0.
- **The bottling line** is also something the PLC computes (`plc-packaging.st`), so
  `fuxa_snapshot.py` applies the program's rules to the machines' values at the one
  moment of the snapshot. The line's state and mode are the filler's. The line's run
  bit is 1 unless that state is 2 (Stopped) or 9 (Aborted). The bottle count is the
  filler's, and the reject count is the sum of the rinser's, filler's, coder's,
  labeler's, and packer's (the keg line is not in it). The stop reason is the number
  of the first machine, in line order, that is jammed or Held (1 rinser, 2 filler,
  3 coder, 4 labeler, 5 packer, 6 keg line), else 0. The shift OEE is the filler's
  speed over the line's speed setpoint (at most 1) times the share of good bottles
  (1 while none are counted), in percent to 0.1. A conveyor runs when the machine it
  feeds is Executing and its jam eye is clear, and the filler's product valve is open
  while the filler Executes; these replace the simulation's own values, which do not
  follow the machines. The keg washer's heater is on under 165 °F and off from 175 °F;
  between the two the PLC keeps its last state, which one moment cannot show, so
  that value stays the simulation's. The line's mode command is 1 (production), where
  the program starts it. What this simplifies: the speed is one reading,
  and the PLC sees it every scan; the PLC's `REAL` to integer step may truncate where
  this rounds, so the OEE can differ by 0.1 %; and the line-held alarm (10-minute
  on-delay) is on whenever the line is Held.
- **The cellar's transfer, entry, and CO₂ bits** are staged the same way
  (`fuxa_snapshot.py`, header): the transfer is active when its pump runs and the CIP
  permit is its opposite; no entry permit is set, so every vessel may be cleaned; and
  the carbonation inhibit follows the gas relay (open means inhibited). The copies
  of these that plc-utilities reads from plc-cellar take the same values.
- **The brew** is not staged. The Brewhouse and Batch pictures of `baseline-brew-day`
  and the brewhouse HMI station come from a run with `fuxa_run.sh up ... live`. The
  plant's values are those of 11:00, and the batch engine takes the tests' port of the
  brewhouse PLC (`fuxa/tests/brewhouse_sim.mjs`) through its phases against them, so
  the phase bar, the batch counters, and the Batch view are live and the vessel
  values are not a brew's. A phase takes about 18 s.
- Unless a caption says otherwise the plant is at 11:00 on its first Wednesday
  (simulated time) with nothing injected; the packaging pictures are from 08:00, when
  the line has beer to fill. A trend holds only what FUXA stored since it started,
  a few seconds to a minute, not an hour. The FUXA and HMI pictures are saved with a
  256-color palette to keep the files small (2.7 MB of pictures become 1.0 MB, and
  at most 0.06 % of a picture's pixels change visibly, on the edges of text).

## Needs

- Python 3.12 with the repository's test dependencies (pydantic, websockets,
  opendssdirect, so that `viz` and `sim` import; the viz and sim `pyproject.toml`
  files list them). Put that environment's `bin` first on `PATH`, or set `PYTHON`.
- Node 22 and the `playwright` package with its Chromium (`NODE_PATH` must find it).
- A FUXA 1.3.6 checkout with its `node_modules` (the `server/` directory of
  `frangoteam/FUXA` at the commit `phenix-images/cm-fuxa.yml` pins, after `npm ci`).
  Set `FUXA_SERVER` to that `server/` directory.
- A directory with the two driver packages FUXA loads on demand. Set `FUXA_PKG`:

  ```shell
  mkdir -p /path/to/pkg && cd /path/to/pkg
  echo '{"name":"fuxa-plugin-runtime","private":true,"version":"1.0.0"}' > package.json
  npm install node-bacnet@0.2.4 node-snap7@1.0.9
  ```

- `Xvfb` and ImageMagick's `import` (the HMI pictures), Pillow (`shrink_pngs.py`; set
  `SHRINK_PYTHON` if `PYTHON` has none), and permission to bind port 102 (the S7
  server; root does).
- The viz capture needs no FUXA; the FUXA and HMI captures need no viz.

## Run

```shell
export FUXA_SERVER=/path/to/FUXA/server FUXA_PKG=/path/to/pkg
cherrymountain-brewery/docs/examples/capture/capture_all.sh            # all four groups
cherrymountain-brewery/docs/examples/capture/capture_all.sh viz        # or one: viz, fuxa, hmi, brand
```

Environment: `OUT` (where the PNGs go; default `docs/examples/`), `WORK` (scratch;
default `/tmp/cm-examples`), `CM_ROOT` (the `cherrymountain-brewery` directory;
default found from this file), `PYTHON`, `XDISPLAY` (default 98), and
`REMAKE_SNAPSHOTS=1` to run the simulation again (snapshots are kept in
`$WORK/snapshots/`). The whole set takes about 15 minutes, most of it the
simulation to 11:00 of the plant's day (six snapshots) and cm-viz's scenes.

## What the pieces assume

- FUXA runs in secure mode, as in the range. The views are the generated
  `fuxa/full/*-project.json` files, unchanged but for the device addresses.
- The BACnet driver of FUXA binds to a non-loopback address of the host and the
  `node-bacnet` server drops requests from `127.0.0.1`, so `fuxa_run.sh` uses the
  host's own address (`CM_BACNET_IFACE` overrides the one it finds) and the stand-in
  BACnet devices listen on `127.0.0.2`, `127.0.0.3`, and so on.
- `node-snap7`'s server crashes when it shares a process with the others, and
  copies the buffers it is given, so `fuxa_s7.cjs` is a process of its own and
  writes its values to its own server over S7.
- Headless Chromium draws the 3D scenes with SwiftShader (software WebGL); the
  footer of each cm-viz picture names the renderer.
