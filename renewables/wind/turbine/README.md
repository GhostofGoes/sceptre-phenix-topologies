# Wind Turbine

## Background

This wind turbine model was created as part of the Department of Energy (DOE)
Wind Energy Technology Office (WETO) funded "Wind Reference Architecture"
project at DOE's National Renewable Energy Laboratory (NREL). It leverages
[OT-sim](https://github.com/patsec/ot-sim), and included the development of two
new OT-sim modules
([wind_turbine/anemometer](https://github.com/patsec/ot-sim/tree/main/src/python/otsim/wind_turbine/anemometer)
and
[wind_turbine/power_output](https://github.com/patsec/ot-sim/tree/main/src/python/otsim/wind_turbine/power_output))
and a new phenix user app
([wind-turbine](https://github.com/sandialabs/sceptre-phenix-apps/tree/main/src/python/phenix_apps/apps/wind_turbine)).

## Deploying in phēnix

A wind turbine experiment has been designed to be deployed using
[phēnix](https://github.com/sandialabs/sceptre-phenix). The easiest way to
deploy phēnix is to use the Docker Compose configs present in the `docker`
directory of the phēnix repository.

The phēnix configs in this repository include file injection references to
`/phenix/injects/wind-turbine`. To ensure these injections work correctly, users
need to copy the contents of `phenix-injects` to `/phenix/injects/wind-turbine`
on the server where phēnix is running (the "head node").

```
mkdir -p /phenix/injects/wind-turbine
cp -a phenix-injects/* /phenix/injects/wind-turbine
```

`wtg-1-main-controller` is on the `ot` VLAN with `grafana` and on the `turbine`
VLAN with the other OT-sim devices, and it is the default gateway on both. The
topology injects `main-controller/90-ip-forward.conf` to turn on its IP
forwarding, so the ground-truth module of each OT-sim device can send its data
to Elasticsearch on `grafana`.

The phēnix topology config in this repository include VM disk references to
`ot-sim.qc2` and `grafana.qc2`. Recipes for both images are in the
[sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images)
repository. Its Makefile runs `phenix` in the phēnix Docker container and gives
it paths in the clone, so the clone must be under `/phenix`, which the container
sees at the same path.

If you added the `ot-sim` and `grafana` image configs from this repository's
former `phenix-images` directory, delete them first, because the Makefile stops
when an image config with the same name exists.

```
docker exec phenix phenix image delete ot-sim
docker exec phenix phenix image delete grafana
```

To build the images, run the following commands on the server where the phēnix
Docker container is running.

```
git clone https://github.com/sandialabs/sceptre-phenix-images.git /phenix/sceptre-phenix-images
cd /phenix/sceptre-phenix-images
make ot-sim
make grafana
```

After each build, the Makefile injects minimega's `miniccc` agent into the
image. It takes the binary from `/phenix/miniccc`, or from `miniccc` at the top
of the clone. Use a statically linked `miniccc` (built with `CGO_ENABLED=0`):
`ot-sim.qc2` is Ubuntu focal, and the `miniccc` in minimega's container needs a
newer glibc than focal has.

Then, move the image files to the `/phenix/images` directory so phenix and
minimega know where to grab them from.

To create an experiment, add the topology and scenario file from
`phenix-configs` to the phēnix store either via the command line or the UI.
Next, create an experiment in the phēnix UI by referring to the appropriate
topology and scenario for the given network above.

Once you start the experiment, you can verify it is operating as expected by
checking the Grafana dashboard. To access the grafana dashboard, you will need
to use minimega's port forwarding capability since there are no VMs in this
experiment with operating systems that include a browser.

```
docker exec -it minimega /opt/minimega/bin/minimega -e \
  cc tunnel grafana 30000 localhost 3000
```

Then, from your local computer, browse to http://localhost:30000 to access the
Grafana dashboard.

## State of health

`phenix-configs/scenario.yml` also runs the phēnix state of health (soh) app.
Its checks, by node group:

- `grafana`: its address and its gateway, the main controller, which must answer
  ping; the five injected Elasticsearch and Grafana files; the `phenix`,
  `elasticsearch` and `grafana-server` services and their processes; listeners
  on 3000 (Grafana) and 9200 (Elasticsearch); a TCP connection to the DNP3
  outstation of the main controller (192.168.100.1:20000); the Turbine
  dashboard in Grafana's search, a passing Grafana health check of the
  `WindTurbine` datasource, and ground-truth documents in `wind-turbine-*` from
  the main controller, the yaw controller and the signal converter.
- `wtg-1-main-controller`: its two addresses; `/etc/ot-sim/config.xml` and the
  injected `90-ip-forward.conf`, and IP forwarding turned on; the `phenix` and
  `ot-sim` services; the OT-sim CPU module and the six modules of its config;
  listeners for the CPU API (9101), the message bus (127.0.0.1:1234 and
  127.0.0.1:5678) and DNP3 (192.168.100.1:20000); TCP connections to the
  Modbus server (502) of each field device and to Elasticsearch on `grafana`;
  `speed.high`, `yaw.current` and `turbine.mw-output` on its CPU API, which
  show that it polls the signal converter and the yaw controller over Modbus
  and that its power output module runs.
- `wtg-1-yaw-controller`, `wtg-1-signal-converter` and `wtg-1-blade-1` to
  `wtg-1-blade-3`: the address and the gateway (1.1.1.254), which must answer
  ping; `/etc/ot-sim/config.xml`; the `phenix` and `ot-sim` services; the CPU
  module and the modules of each config; listeners for the CPU API, the message
  bus and Modbus on the device's address; a TCP connection to Elasticsearch on
  `grafana` through the main controller. On the signal converter also
  `/etc/ot-sim/data/weather.csv`, which must be the data and not a Git LFS
  pointer, and `speed.high` on its CPU API.

Every guest also reports its CPU load. Creating and starting the experiment
from the two configs runs the checks. From the command line:

```
phenix config create phenix-configs/topology.yml phenix-configs/scenario.yml
phenix experiment create wind-turbine -t wind-turbine -s wind-turbine
phenix experiment start wind-turbine
```

The checks run in the post-start stage after a 5 minute `startupDelay`, and
`phenix experiment start` returns when they finish. A failed check is recorded
but does not fail the start (`exitOnError: false`). To run the checks again on
the running experiment:

```
phenix experiment trigger running wind-turbine soh
```

The results are in the SoH view of the phēnix web UI (the SoH button of the
experiment), under `status.apps.soh` in the output of
`phenix config get experiment/wind-turbine`, and at
`GET /api/v1/experiments/wind-turbine/soh` in the REST API.

`runPeriodically: 15m` runs the checks again every 15 minutes when the
experiment is started from the web UI or the REST API, or with
`phenix experiment start wind-turbine --honor-run-periodically`, which then
stays in the foreground until the experiment stops. A plain
`phenix experiment start` does not schedule them.

The checks run over minimega's command and control, so each guest needs the
`miniccc` agent, which the image recipes inject. The `weather-replay` check
fails when `phenix-injects/weather.csv` is a Git LFS pointer: run
`git lfs pull` in this repository before you copy the injects.

### Optional packet capture

soh can also capture traffic and show it in a Network Volume tab of the SoH
view. It is not configured, because it adds an Elasticsearch and Kibana VM
(2 vCPUs and 4096 MB in the block below) on the `MGMT` VLAN and one 512 MB
Packetbeat VM for each captured node, and it needs `elasticsearch.qc2` and
`packetbeat.qc2`, which the `elasticsearch` and `packetbeat` recipes of
sceptre-phenix-images build (`make elasticsearch packetbeat`). soh mirrors only
one interface of each captured node, so the block captures the Modbus traffic
on the `turbine` interface of the main controller and the ground-truth traffic
on `grafana`. To add it, paste the block under `metadata:` of the soh app in
`phenix-configs/scenario.yml`, stop and delete the experiment, delete the
scenario config (`phenix experiment stop wind-turbine`,
`phenix experiment delete wind-turbine`,
`phenix config delete scenario/wind-turbine`), and create both again.

```yaml
      packetCapture:
        elasticImage: elasticsearch.qc2
        packetBeatImage: packetbeat.qc2
        elasticServer:
          hostname: soh-elasticsearch-server
          vcpus: 2
          memory: 4096
          ipAddress: 172.16.100.250/24
        captureHosts:
          grafana:
          - IF0
          wtg-1-main-controller:
          - IF1
```
