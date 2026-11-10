# Capture scripts for the Examples section

These scripts take the screenshots in [the Examples section of the
README](../../../README.md#examples). Every picture is real software showing the
range's real files; the scripts draw nothing. What is staged is data, and each
caption in the README says which.

| File | What it does |
|---|---|
| `capture_all.sh` | Runs everything below in order and writes the PNGs to `docs/examples/` |
| `viz_shots.py` | cm-viz: runs the offline federation (`LocalSource`), plays a viz scenario, serves the real page, and has headless Chromium take the picture once a condition on the frame holds |
| `fuxa_snapshot.py` | Runs the offline federation to a moment of a scenario and writes the values the stand-in plant serves (see the file's header for which values come from where) |
| `fuxa_plant.mjs`, `fuxa_s7.cjs` | The stand-in plant: OPC UA (plc-brewhouse), Modbus TCP (plc-cellar, plc-utilities, plc-power), S7 (plc-packaging), and BACnet servers on loopback that serve a snapshot. With `--live-batch` the brewhouse handshake is the tests' port of plc-brewhouse's program (`fuxa/tests/brewhouse_sim.mjs`) |
| `fuxa_prep.py` | Copies a generated FUXA project with only the device addresses changed to the loopback ones |
| `fuxa_run.sh` | Starts the plant and a real FUXA 1.3.6 server, then runs the range's own `cm-fuxa-provision.sh` (admin password, operator account, project import) |
| `fuxa_shots.cjs` | Playwright: opens FUXA views (optionally with the alarm list or the sign-in dialog open) and takes the picture |
| `hmi_kiosk.sh` | Runs the range's own `cm-kiosk` with Chromium in kiosk mode on an Xvfb display of 1440x900 and takes the display |
| `shrink_pngs.py` | Saves the FUXA and HMI pictures with a 256-color palette (about 40 % of the bytes; needs Pillow) |

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
simulation to 11:00 of the plant's day (seven snapshots) and cm-viz's scenes.

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
