# Overview
The SOAP topology models a notional SCADA system for a 300 bus microgrid system. The model uses PyPower to model the physical process itself, Ignition SCADA software, and additionally includes the ControlThings.io environment to additionally provide a testing suite for the ICS environment.

![SOAP](SOAP.png)

# Configuration Files
The model consists of the following configurations:
- `soap-topology.yaml` - This defines the network topology for the SOAP model.
- `sceptre.yaml` - This defines the sceptre app configuration needed to add the ICS specifics to the topology.
- `soh.yaml` - Defines the state of health (soh) app configuration for the SOAP topology. See [State of health](#state-of-health).
- `soap-scenario.yaml` - This defines the entirety of the scenario configurations needed. This pulls together the `sceptre` and `soh` configurations and adds the mirror app.

# Images
The model uses the following backing images:
- `bennu.qc2`
- `ignitionhmi.qc2`
- `controlthingsplatform.qc2`
- `elasticsearch.qc2` and `packetbeat.qc2` - the soh app boots these for its packet capture.

The bennu, elasticsearch and packetbeat images are built from [sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images), for example with `make bennu`. A pre-built bennu image can also be downloaded with `oras pull ghcr.io/sandialabs/sceptre-phenix-images/bennu.qc2:latest`.
The hmi and control things images are not included in this repository; you must provide them yourself.

# Deploying SOAP
1. Copy the entire contents of this topology to `/phenix/topologies/soap-legacy`. Phenix topologies can technically be located anywhere, but the SOAP topology looks for injects from this location.
2. Put the images listed above in `/phenix/images`.
3. Add the SOAP configs to the data store using the following commands:
```
phenix config create <path_to_topos>/soap-topology.yaml
phenix config create <path_to_topos>/sceptre.yaml
phenix config create <path_to_topos>/soh.yaml
phenix config create <path_to_topos>/soap-scenario.yaml
```
4. Run the SOAP model using the following commands:
```
phenix exp create soap-legacy -t soap-legacy -s soap-legacy
phenix exp start soap-legacy
```
5. Stop and delete the model using the following commands:
```
phenix exp stop soap-legacy
phenix exp delete soap-legacy
```

For more information on the SOAP topology see the `SOAP_User_Guide__UUR__20200817.pdf`
The guide calls the ControlThings VM `controlthings_elk`; it is now `controlthings-elk`, because phenix host names cannot contain underscores.
For more information on installing and running phenix see the Phenix Quick Start Guide.

# State of health
`soap-scenario.yaml` runs the phenix state of health (soh) app with the configuration in `soh.yaml` (`fromScenario: soap-soh`). The checks, by node group:

- `provider`: the injected `config.ini`, `case300.py` and sceptre start scripts; the `pybennu-power-solver` process and its server endpoint on `172.16.1.2:5555`; ssh on `172.16.1.2:22`; the `miniccc`, `phenix`, `ssh`, `ntp` and `collectd` services. The `phenix` service is active once the startup scripts have finished and the last one, the sceptre start script, exited 0.
- `rtu-1` to `rtu-4`: the EXP address; ping to every EXP and MGMT address of the RTUs (and of `ignition` and `controlthings-elk` when they boot); a TCP connection to `172.16.1.2:5555`; the injected `config.xml` and the files the sceptre start script writes; no uploaded `/home/sceptre/bennu-field-deviced.firmware`; the `bennu-watcherd` and `bennu-field-deviced` processes; the provider's services plus `vsftpd`; listeners for DNP3 on the EXP address (20000), the pybennu publish group (40000), FTP (21) and ssh on both addresses; an established session to `172.16.1.2:5555` and a multicast packet from the provider.
- `soh-elasticsearch-server`, the Elasticsearch and Kibana VM that soh adds for its packet capture: `query-flows.sh` and the injected Elasticsearch and Kibana configs; the `miniccc` and `phenix` services; cluster health green or yellow, an answer from the Kibana status API, and at least one Packetbeat flow document, each waited for up to 5 minutes.
- `ignition`: the Ignition gateway listener on port 8088.

soh skips nodes that do not boot, so `ignition` and `controlthings-elk` get their address, ping and listener checks only when `ignitionhmi.qc2` and `controlthingsplatform.qc2` exist. soh finds their C2 client by VM UUID, because this repository does not build their images.

The packet capture adds the Elasticsearch and Kibana VM (4 vCPUs, 4096 MB) at `172.16.1.200/16` on MGMT, and one 512 MB Packetbeat VM for `provider` (mgmt) and for each RTU (exp) at `172.16.1.201` to `172.16.1.205`. The SoH view then also has a Network Volume tab.

Create and start the experiment as in [Deploying SOAP](#deploying-soap). The checks run in the post-start stage after an 8 minute `startupDelay`, and the start returns when they finish. The delay covers guests that reach C2 within about 5 minutes, since the field devices and pybennu start about 90 seconds after C2. A fast host needs only 3m. On a 4-vCPU host, such as a CI runner, the 11 VMs reach C2 after about 14 minutes and the delay must be 20m or more. A failed check is recorded but does not fail the start. To run the checks again on the running experiment:
```
phenix experiment trigger running soap-legacy soh
```

The results are in the SoH view of the phenix web UI (the SoH button of the experiment), under `status.apps.soh` in the output of `phenix config get experiment/soap-legacy`, and at `GET /api/v1/experiments/soap-legacy/soh` in the REST API.

`runPeriodically: 15m` runs the checks again every 15 minutes when the experiment is started from the web UI or the REST API, or with `phenix exp start soap-legacy --honor-run-periodically`, which stays in the foreground until the experiment stops. A plain `phenix exp start` does not schedule them.

## Optional checks
Two checks are not in `soh.yaml`, because they pass only after a manual step. Ignition polls the RTUs over DNP3 only while its trial license is active (see the user guide), and the mirror check on `controlthings-elk` waits for one GRE packet from the mirror app, which needs EXP traffic such as that polling. Both assume that the images have `bash`, `ss` and `tcpdump`. To add them, paste this block under `hostCustomTests:` in `soh.yaml`:
```yaml
          ignition:
          - name: dnp3-polling
            testScript: |
              for _ in $(seq 60); do
                polled=$(ss -Htn state established '( dport = :20000 )' | grep -oE '10\.117\.4\.10[1-4]:20000' | sort -u | wc -l)
                if [ "$polled" -eq 4 ]; then
                  echo "polling all 4 rtus"
                  exit 0
                fi
                sleep 5
              done
              echo "polling $polled of 4 rtus"
            testStdout: polling all 4 rtus
          controlthings-elk:
          - name: mirror-traffic
            testScript: |
              timeout 30 tcpdump -c 1 -ni any ip proto 47
            testStderr: 1 packet captured
```

Then replace the stored config, and create and start the experiment again:
```
phenix exp stop soap-legacy
phenix exp delete soap-legacy
phenix config delete scenario/soap-soh
phenix config create <path_to_topos>/soh.yaml
phenix exp create soap-legacy -t soap-legacy -s soap-legacy
phenix exp start soap-legacy
```

After you activate the trial license in the running experiment, run `phenix experiment trigger running soap-legacy soh` to check again.
