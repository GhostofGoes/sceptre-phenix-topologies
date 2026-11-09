# CherryMountain Brewery phenix apps

The topology's own phenix software
([DESIGN.md 16](../DESIGN.md#16-new-and-extended-software)), one Python
package with two executables:

| Executable | What | README |
|---|---|---|
| `phenix-app-cherrymountain` | User app: validates the scenario against `plant/plant.yaml`, renders and injects every per-host file, watches provisioning and plant KPIs, exports data at cleanup | [cherrymountain_apps/cherrymountain/README.md](cherrymountain_apps/cherrymountain/README.md) |
| `phenix-scorch-component-plantevent` | SCORCH component: posts operational events (batch, outage, genset exercise, chiller fault, CO2 release and leak, gas-detection failure, refrigerant leak, BESS thermal event, jam, CIP panel, weather step) to the sim-ctl event API | [cherrymountain_apps/plantevent/README.md](cherrymountain_apps/plantevent/README.md) |

Both follow [sceptre-phenix-apps](https://github.com/sandialabs/sceptre-phenix-apps):
the app subclasses `AppBase` (stage on argv, experiment JSON on stdin, the
updated experiment on stdout, JSON logs on stderr), and the component
subclasses `ComponentBase` with a Pydantic `metadata_model`.

## Layout

```text
apps/
├── Dockerfile                 phenix image with both executables
├── pyproject.toml             package, entry points, ruff, mypy, pytest
└── cherrymountain_apps/
    ├── simctl.py              event API client and cc tunnel lookup (shared)
    ├── cherrymountain/        the app: metadata, validation, render, soh, kpi
    │   ├── templates/         scripts, systemd units, .desktop, Mako configs
    │   └── tests/             stage tests on the recorded ci experiment
    └── plantevent/            the SCORCH component and its tests
```

The scripts, units, and `.desktop` file in `templates/` are plain files, so the
repository's shellcheck, `systemd-analyze`, and `desktop-file-validate` hooks
check them as they are injected. Values reach them through rendered
`/etc/default/*` files.

## Build the phenix image

```shell
docker build -t cherrymountain-phenix cherrymountain-brewery/apps
```

By default the image is `FROM ghcr.io/sandialabs/sceptre-phenix/phenix:main`,
pinned by digest. It already has phenix-apps and every dependency, so the
package installs with `--no-deps`. DESIGN.md 16 runs the range on the phenix
fork, built with the forked sceptre-phenix-apps (`APPS_REPO`, `APPS_BRANCH`).
Build that image first, then name it with the `PHENIX_IMAGE` build argument
(a locally built image has no registry digest to pin):

```shell
docker build --build-arg PHENIX_IMAGE=phenix:cherrymountain \
  -t cherrymountain-phenix cherrymountain-brewery/apps
```

[USER_GUIDE.md](../USER_GUIDE.md) has the whole sequence. The package needs
nothing from the fork: where the fork has a helper upstream lacks
(`utils.is_headnode`), the package falls back.

## Develop and test

Python 3.12, with phenix-apps installed from a checkout (it is not on PyPI):

```shell
python3.12 -m venv .venv && . .venv/bin/activate
pip install /path/to/sceptre-phenix-apps/src/python
pip install -e 'cherrymountain-brewery/apps[test,dev]'
cd cherrymountain-brewery/apps
python -m pytest
ruff check . && ruff format --check . && mypy
```

The tests read plant.yaml, the plantgen output, the plc-cellar program ZIP
(Git LFS), and the FUXA builder from this repository, and run each app stage
on `cherrymountain_apps/cherrymountain/tests/fixtures/experiment-ci.json`. After
a change to `phenix-configs/topology-ci.yml`, `scenario-ci.yml`, or
`phenix-ci.yml`, re-record it:

```shell
python3 -I cherrymountain_apps/cherrymountain/tests/build_fixtures.py
```

## Contracts with the rest of the topology

- **Scenario order:** `ot-sim`, `cherrymountain`, `helics`.
- **Event API** (cherrymountain_sim on sim-ctl, guest port 8080):
  `GET /api/v1/health`, `POST /api/v1/events` (`{"event": <kind>, ...}`), and
  `GET /api/v1/kpis` (see `cherrymountain/kpi.py` for the fields).
- **Program ZIPs:** `phenix-injects/openplc/<program>.zip` for ci and
  `phenix-injects/openplc/<variant>/<program>.zip` for full and lite; an OPC UA
  PLC also needs its compile's `debug_map.json`, inside the ZIP or at
  `plc/<program>/debug_map.json`.
- **BBMD:** the topology injects `cm-bbmd.service` and its settings from
  `phenix-injects/bbmd/`; the app only checks the BBMD hosts.
