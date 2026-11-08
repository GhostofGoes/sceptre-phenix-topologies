# Spike: OpenPLC v4 unattended provisioning

**Question.** Can an OpenPLC v4 runtime be provisioned with no GUI (create
user, upload a program ZIP carrying plugin configs, compile, start) and run
`opcua` + `modbus_master` + on-device compile inside the planned VM memory?
**Decides:** v4 or the v3 fallback (DESIGN §12.1, §19).

## Prerequisites

- A VM image with the OpenPLC v4 runtime
  ([Autonomy-Logic/openplc-runtime](https://github.com/Autonomy-Logic/openplc-runtime)),
  pinned to a release tag. Record the tag below.
- One program ZIP built in the OpenPLC Editor with the `opcua` and
  `modbus_master` plugins enabled (any trivial ST program with a few
  `%QW`/`%IW` variables).
- A client with `curl`, `python3`, `pip install pymodbus`.

Upstream facts used (read from `webserver/restapi.py`, development branch):
API root `https://<plc>:8443/api`, `POST /create-user` (first user needs no
auth), `POST /login` returns `access_token`, `POST /upload-file` multipart,
`GET /compilation-status` (IDLE, UNZIPPING, COMPILING, SUCCESS, FAILED),
`GET /start-plc`, `GET /status`, unauthenticated `/version`, `/capabilities`.

## Steps

1. Boot the fixture (`topology.yml`, adjust images) or any VM with the runtime.
2. `PLC=10.20.40.10 ZIP=prog.zip ./provision.sh` from the client. Note
   `compile_seconds`.
3. Inspect the ZIP's contents (`unzip -l`) to find where plugin configs live;
   edit `repack_zip.sh` `member_dir` default. Repack with an OPC UA config
   whose endpoint is `opc.tcp://10.20.40.10:4840/...` (the template default is
   `localhost`), upload again with `provision.sh`.
4. From the client: `nc -zv 10.20.40.10 4840` and an OPC UA browse
   (`pip install asyncua; python3 -c` or `opcua-client`) to the rebound
   endpoint. Also `./modbus_read.py 10.20.40.10` if a `modbus_slave` or
   `modbus_master` northbound is enabled.
5. Reboot the VM. Confirm the program auto-starts (`GET /status`) with no
   API calls.
6. On the VM, run `DURATION=300 ./memcheck.sh` while triggering a compile
   (step 2 again) with both plugins running.
7. Repeat steps 2-3 from a fresh image twice to confirm the flow is
   repeatable (no manual UI step).

## Pass / fail

All must hold for **go (v4)**:

- [ ] `provision.sh` ends with PASS on a fresh image, twice, no UI.
- [ ] Plugin configs inside the repacked ZIP take effect (OPC UA reachable on
      the CTRL address, not only localhost).
- [ ] The program auto-starts after reboot.
- [ ] `peak_used_mb` during compile leaves at least 25% of VM RAM free, or the
      VM size you would need is acceptable (record it).
- [ ] Compile completes in under 10 minutes.

Any unchecked item with no workaround: **no-go**, take the v3 fallback
(GRFICSv3 pattern, DESIGN §12.1) and note the FUXA link changes.

## Result

- **Decision:** go (v4) / no-go (v3)
- **Runtime tag:**
- **Evidence:** (provision.sh output, memcheck peak, OPC UA browse)
- **Follow-ups:** (e.g. where plugin configs live in the ZIP, REST on 8443 vs firewall rule)
