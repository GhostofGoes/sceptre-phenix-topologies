The waterway topology models a notional SCADA system for a waterway lock system. The model uses Matlab Simulink for modeling the waterway physics. The model uses OPC TOP Server, mySCADA, and Wonderware historian SCADA software.  

![waterway_network](waterway_network.png)

The model consists of the following configurations:
- `waterway-topology.yaml` - This defines the network topology for the waterway model.
- `waterway-scenario.yaml` - This defines the scenario configuration including the sceptre app and the state of health checks (see [State of health](#state-of-health)).

The model uses the following backing images:
- `bennu.qc2` - the bennu image is built by the `bennu` recipe in [sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images), which publishes a new build every week. Download the latest build to `/phenix/images` with [ORAS](https://oras.land/docs/installation/):
    ```
    cd /phenix/images/
    oras pull ghcr.io/sandialabs/sceptre-phenix-images/bennu.qc2:latest
    ```
    To build it yourself, run `make bennu` in a clone of sceptre-phenix-images (see its [Makefile](https://github.com/sandialabs/sceptre-phenix-images#makefile) section).
- `engineer_workstation.qc2` - the engineer workstation image is a Windows 7 based image which requires licensing. The user must license and build their own image.  
- `historian.qc2` - the historian image is a Windows Server 2008 R2 based image with Wonderware historian SCADA software installed. Both require licensing and the user must license and build their own image.
- `hmi_win7.qc2` - the hmi image is a Windows 7 based image which requires licensing. The user must license and build their own image.  

- `minirouter.qc2` - the minirouter image is built by the `minirouter` recipe in [sceptre-phenix-images](https://github.com/sandialabs/sceptre-phenix-images), which also publishes a new build every week:
    ```
    cd /phenix/images/
    oras pull ghcr.io/sandialabs/sceptre-phenix-images/minirouter.qc2:latest
    ```
    To build it yourself, run `make minirouter` in a clone of sceptre-phenix-images.
- `opc.qc2` a Windows Server 2008 R2 based image with OPC TOP Server software installed. Both require licensing and the user must license and build their own image.
- `scada.qc2` a Windows Server 2008 R2 based image with mySCADA software installed. Both require licensing and the user must license and build their own image.

The topology injects files into partition 2 of `opc.qc2`, `historian.qc2` and `scada.qc2`, and into partition 1 (the phenix default) of `hmi_win7.qc2` and `engineer_workstation.qc2`. If the `C:` drive of an image you build is on a different partition, set `inject_partition` for that node in `waterway-topology.yaml` to match.

`waterway-scenario.yaml` reads its injects from `/phenix/topologies/waterway`, so this folder must be at that path. Clone this repository to `/phenix/topologies`, or copy this folder there from the root of your clone:
```
cp -r ./waterway /phenix/topologies/.
```
If you keep it somewhere else, change the paths in `waterway-scenario.yaml` to match.

To run the model use the following commands
```
phenix config create /phenix/topologies/waterway/waterway-topology.yaml
phenix config create /phenix/topologies/waterway/waterway-scenario.yaml
phenix exp create waterway -t waterway -s waterway
phenix exp start waterway
```

## State of health

`waterway-scenario.yaml` also runs the phenix state of health (soh) app. Its checks, by node group:

- `simulink-provider`: the injected sceptre files and the `groundTruth.txt` the solver writes; the `ssh`, `collectd`, `ntp` and `phenix` services; the provider, solver and ground truth processes; listeners on `172.16.1.2:5555`, `:8080` and `172.16.1.2:22`; the ground truth web page.
- `rtu-1` and `rtu-2`: the address and gateway of `IF0`; the injected sceptre files and `watcher.ini`; no uploaded `/home/sceptre/bennu-field-deviced.firmware`; the `ssh`, `vsftpd`, `collectd`, `ntp` and `phenix` services; `bennu-watcherd` and the field device process; listeners for Modbus (502), the provider's multicast (40000), ssh on both addresses and FTP; a provider multicast packet, an established connection to the provider, and a TCP connection to `172.16.1.2:5555`.
- `router`: its SCADA and ICS addresses; the `minirouter` and `phenix` services; the `bird` OSPF daemon; IPv4 forwarding; TCP connections to port 502 on both RTUs.
- `opc`: its address and gateway; `opc.xml` and the TOP Server startup script; the `TOPServerV5` service and its `server_runtime` process; Modbus TCP connections to both RTUs through the router.
- `historian`, `hmi` and `scada-server`: their address and gateway, and the files the sceptre app injects; on `historian`, the `MSSQLSERVER` service.
- `engineer-workstation`: its address and gateway.

The run commands above create and start the experiment with these checks. The checks run in the post-start stage after a 4 minute `startupDelay`, so `phenix exp start waterway` returns about 5 minutes after the VMs start. A failed check is recorded but does not fail the start (`exitOnError: false`). To run the checks again on the running experiment:

```
phenix experiment trigger running waterway soh
```

The results are in the SoH view of the phenix web UI (the SoH button of the experiment), under `status.apps.soh` in the output of `phenix config get experiment/waterway`, and at `GET /api/v1/experiments/waterway/soh` in the REST API.

`runPeriodically: 10m` runs the checks again every 10 minutes when the experiment is started from the web UI or the REST API, or with `phenix exp start waterway --honor-run-periodically`, which then stays in the foreground until the experiment stops. A plain `phenix exp start` does not schedule them.

The checks run over minimega's command and control (C2). The `bennu` and `minirouter` images have the miniccc agent. The Windows images you build must start miniccc at boot, or soh records a C2 error for each of them. To leave a node out, add its hostname to a `skipHosts` list in the soh metadata. soh skips nodes that do not boot.

These optional checks are not in the scenario. The SCADA ports open only after the Windows startup automation ends, several minutes after boot, and they depend on the vendor software and firewall rules of the images you build. To add them, paste this block under `hostCustomTests:` of the soh app. Then stop and delete the `waterway` experiment (`phenix exp stop waterway`, `phenix exp delete waterway`), run `phenix config delete scenario/waterway`, and create the Scenario config and the experiment again. Run the trigger command above once the HMI shows data:

```yaml
          scada-server:
          - name: opc-ua-opc
            testScript: |
              $c = New-Object Net.Sockets.TcpClient
              try { $c.Connect('10.1.2.50', 4840) } catch {}
              if ($c.Connected) { 'connected' }
              $c.Close()
            testStdout: connected
          hmi:
          - name: web-scada-server
            testScript: |
              $c = New-Object Net.Sockets.TcpClient
              try { $c.Connect('10.1.2.71', 80) } catch {}
              if ($c.Connected) { 'connected' }
              $c.Close()
            testStdout: connected
```

`testReachability: full` is also left out: it pings every address in every VLAN, and a Windows guest whose firewall drops ICMP echo fails it.

Packet capture is not part of the scenario either. It adds an Elasticsearch and Kibana VM (4 vCPUs, 4096 MB) on the MGMT VLAN and one 512 MB Packetbeat VM for each captured VM, and it needs `elasticsearch.qc2` and `packetbeat.qc2`, built by the `elasticsearch` and `packetbeat` recipes in sceptre-phenix-images. To capture the ICS traffic of both RTUs, paste this block under `metadata:` of the soh app and create the Scenario config and the experiment again as above:

```yaml
        packetCapture:
          elasticImage: elasticsearch.qc2
          packetBeatImage: packetbeat.qc2
          elasticServer:
            hostname: soh-elasticsearch-server
            ipAddress: 172.16.200.1/16
          captureHosts:
            rtu-1:
            - IF0
            rtu-2:
            - IF0
```

The SoH view then also has a Network Volume tab, with the flows that Packetbeat records.
