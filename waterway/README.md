The waterway topology models a notional SCADA system for a waterway lock system. The model uses Matlab Simulink for modeling the waterway physics. The model uses OPC TOP Server, mySCADA, and Wonderware historian SCADA software.  

![waterway_network](waterway_network.png)

The model consists of the following configurations:
- `waterway-topology.yaml` - This defines the network topology for the waterway model.
- `waterway-scenario.yaml` - This defines the scenario configuration including the sceptre app.

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
