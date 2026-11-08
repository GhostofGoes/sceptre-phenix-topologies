The helloworld topology models a simple 2 VM topology as an introductory example.  

The model consists of the following configurations:
- `helloworld.yaml` - This defines the network topology for the helloworld model.
- `helloworld-scenario.yaml` - An optional Scenario that runs state of health checks on both VMs (see [State of health](#state-of-health)).

The model uses the following backing images:
- `ubuntu.qc2` - headless Ubuntu Noble, built by the `ubuntu` recipe in [sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images)

To build it, clone sceptre-phenix-images under `/phenix`, so that the phenix container sees the recipe files at the same path, and run `make ubuntu` there:

```shell
git clone https://github.com/sandialabs/sceptre-phenix-images.git /phenix/sceptre-phenix-images
cd /phenix/sceptre-phenix-images
make ubuntu
mv ubuntu.qc2 /phenix/images/
```

> [!NOTE]
> These examples assume the [sceptre-phenix-topologies](https://github.com/sandialabs/sceptre-phenix-topologies/) git repository has been cloned into `/phenix/topologies/` or the files have been manually copied into `/phenix/topologies/`.
> For example: `git clone https://github.com/sandialabs/sceptre-phenix-topologies/ /phenix/topologies`


To run the helloworld model, `phenix config create` first creates the `helloworld` Topology config in the phenix database (only needed once), then `phenix experiment create` creates a phenix Experiment, referencing the `helloworld` Topology from the phenix database.

```shell
phenix config create /phenix/topologies/helloworld/helloworld.yaml
phenix experiment create hello-world -t helloworld
phenix experiment start hello-world

# To stop:
phenix experiment stop hello-world
```

To run it again, `phenix experiment start hello-world` restarts the stopped experiment. To recreate it, for example after editing the topology, delete it and create it again from the stored Topology:

```shell
phenix experiment delete hello-world
phenix experiment create hello-world -t helloworld
phenix experiment start hello-world
```

> [!NOTE]
> `phenix experiment create hello-world -t /phenix/topologies/helloworld/helloworld.yaml` also works, but it creates the `helloworld` Topology config too, so it fails with `config already exists` once that config is in the phenix database.
> After editing `helloworld.yaml`, run `phenix config delete topology/helloworld` before `phenix config create` again.

## State of health

`helloworld-scenario.yaml` holds the `helloworld` Scenario, which runs the phenix state of health (soh) app. `test1` and `test2` get the same checks:

- Network: the VM has its `if0` address, pings both SW1 addresses (`testReachability: full`), and opens a TCP connection to port 22 on the other VM.
- Startup: the three scripts phenix injects into `/etc/phenix/startup/` exist, the `phenix` startup service that runs them is active, and the timezone is `Etc/UTC`.
- SSH: a socket listens on port 22, and `ssh-keyscan` gets the ed25519 host key of the other VM over SW1.

With the `helloworld` Topology config in the phenix database (see above), create the Scenario config once, then create the experiment with it. Stop and delete an existing `hello-world` experiment first.

```shell
phenix config create /phenix/topologies/helloworld/helloworld-scenario.yaml
phenix experiment create hello-world -t helloworld -s helloworld
phenix experiment start hello-world
```

The checks run in the post-start stage, so `phenix experiment start` returns after they finish. A failed check is recorded but does not fail the start (`exitOnError: false`). To run the checks again on the running experiment:

```shell
phenix experiment trigger running hello-world soh
```

The results are in the SoH view of the phenix web UI (the SoH button of the experiment), under `status.apps.soh` in the output of `phenix config get experiment/hello-world`, and at `GET /api/v1/experiments/hello-world/soh` in the REST API.

`runPeriodically: 15m` runs the checks again every 15 minutes when the experiment is started from the web UI or the REST API, or with `phenix experiment start hello-world --honor-run-periodically`, which then stays in the foreground until the experiment stops. A plain `phenix experiment start` does not schedule them.

Packet capture is not part of the Scenario. It adds an Elasticsearch and Kibana VM (4 vCPUs, 4096 MB) on a new MGMT VLAN and one 512 MB Packetbeat VM for each captured VM, and it needs `elasticsearch.qc2` and `packetbeat.qc2`, built by the `elasticsearch` and `packetbeat` recipes in sceptre-phenix-images. To add it, paste this block under `metadata:` of the soh app, run `phenix config delete scenario/helloworld`, and create the Scenario config and the experiment again:

```yaml
        packetCapture:
          elasticImage: elasticsearch.qc2
          packetBeatImage: packetbeat.qc2
          elasticServer:
            hostname: soh-elasticsearch-server
            ipAddress: 172.16.10.1/24
          captureHosts:
            test1:
            - if0
            test2:
            - if0
```

The SoH view then also has a Network Volume tab, with the flows that Packetbeat records.
