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
