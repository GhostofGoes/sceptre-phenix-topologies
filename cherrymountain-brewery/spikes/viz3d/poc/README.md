# viz3d proof of concept

A small, local proof for the [viz3d spike](../README.md): a three.js view of
the cellar, driven live by the real federates through the offline harness, a
2.5D SVG view of the same frames, and a Playwright script that measures both in
Chromium with no GPU. It proves feasibility only; it is not the product.

| File | What it is |
|---|---|
| `bridge.py` | Python WebSocket bridge (Pydantic frames, `websockets` 15). Serves `web/` and a read-only `/ws` on one port, plus `/frame.json` and `/stats.json`. Sources: `local` (the seven federates in-process, `cherrymountain_sim.local.LocalFederation`, no HELICS) or `fake` (sinusoids) |
| `web/index.html`, `web/app.js`, `web/style.css` | The 3D cellar: four unitanks and two bright tanks (fill, temperature color, pressure band, spunding vent and leak plumes), the CO₂ layer, four low sensors, two purge fans, the alarm beacon, and a table of simulation truth beside what the HMI shows. Geometry is built in code; no binary assets |
| `web/svg.html` | The 2.5D baseline: the same frames drawn as SVG, redrawn only when a frame arrives |
| `fetch_vendor.sh` | Downloads three.js 0.186.1 (MIT) into `web/vendor/`, checked against a pinned SHA-256. Not committed |
| `measure.cjs` | Playwright: renderer string, frames per second, browser CPU and memory (from `/proc`), frame age, bridge CPU, a glTF export and reload, a 20-client fan-out, screenshots |
| `run_evidence.sh` | Runs every measurement and writes `../evidence/` (about six minutes) |

## Run it

```shell
python3 -m venv .venv
.venv/bin/pip install -e '../../../sim[power]' websockets==15.0.1
./fetch_vendor.sh
.venv/bin/python bridge.py --source local --scenario co2-attack --speed 10
# open http://127.0.0.1:8090/ (3D) or http://127.0.0.1:8090/svg.html (2.5D)
```

- `--scenario`: `none` (normal running), `co2-leak` (a 40 g/s release at FV-102
  from simulated second 60, the event the SCORCH `cellar-co2-leak` run sends),
  or `co2-attack` (the same release, then at second 90 a BACnet write that stops
  both purge fans, as a compromised `bas-fuxa` could, and a replay that freezes
  what the HMI shows).
- `--speed` is simulated seconds per wall second; `--fast-forward S` runs `S`
  simulated seconds before serving; `--rate` caps frames per second to browsers
  (the federates publish once per simulated second, the building federate every
  10 s).
- Page options: `?fps=15` caps the render rate, `?aa=1` turns on antialiasing.
  The page shows a red "stale" banner when no frame has arrived for 3 s.
- The bridge binds `127.0.0.1` like the sim-ctl event API, answers `GET` only,
  and ignores anything a browser sends.
- Without the simulation installed, `--source fake` needs only `pydantic` and
  `websockets`.

Evidence: `PYTHON=.venv/bin/python ./run_evidence.sh` (needs node with the
`playwright` package, `taskset`, `xvfb-run`, and ImageMagick `convert`).
