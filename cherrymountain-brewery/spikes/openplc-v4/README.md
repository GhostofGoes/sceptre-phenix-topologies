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
  `%QW`/`%IW` variables). Without the Editor, `build_zip.sh` builds an
  equivalent ZIP from `fixture/prog.st` and `fixture/conf/*.json` using a
  built [strucpp](https://github.com/Autonomy-Logic/strucpp) checkout
  (`STRUCPP=/path ./build_zip.sh prog.zip`). v4.3.x compiles strucpp-generated C++
  output, not MatIEC C: the ZIP needs `generated.cpp`, `generated.hpp`,
  `generated_debug.cpp`, `defines.h`, `strucpp_runtime/include/` and `conf/`.
- A client with `curl`, `python3`, `pip install pymodbus`.

Upstream facts used (read from `webserver/restapi.py` and `webserver/app.py`
at tag v4.3.2):
API root `https://<plc>:8443/api`, `POST /create-user` (first user needs no
auth), `POST /login` returns `access_token`, `POST /upload-file` multipart,
`GET /compilation-status` (IDLE, UNZIPPING, COMPILING, SUCCESS, FAILED),
`GET /start-plc`, `GET /status`, unauthenticated `/version`, `/capabilities`.

## Steps

1. Boot the fixture (`topology.yml`, adjust images) or any VM with the runtime.
   In Docker: build the repo's `Dockerfile`, then `docker run --cap-add=sys_nice`
   (without it the PLC thread warns `SCHED_FIFO ... Operation not permitted`
   and runs at default priority).
2. `PLC=10.20.40.10 ZIP=prog.zip ./provision.sh` from the client. Note
   `compile_seconds`.
3. Plugin configs live in `conf/` inside the ZIP (resolved, see Result);
   `repack_zip.sh` already defaults to it. Repack with an OPC UA config
   whose endpoint is `opc.tcp://10.20.40.10:4840/...` (the template default is
   `localhost`), upload again with `provision.sh`.
4. From the client: `nc -zv 10.20.40.10 4840` and an OPC UA browse
   (`pip install asyncua; python3 -c` or `opcua-client`) to the rebound
   endpoint. Also `./modbus_read.py 10.20.40.10 --count 1` (count within the exposed registers) if a `modbus_slave` or
   `modbus_master` northbound is enabled.
5. Reboot the VM. Confirm the program auto-starts (`GET /status`) with no
   API calls.
6. On the VM, run `DURATION=300 ./memcheck.sh` while triggering a compile
   (step 2 again) with both plugins running.
7. Repeat steps 2-3 from a fresh image twice to confirm the flow is
   repeatable (no manual UI step).

## Pass / fail

All must hold for **go (v4)**:

- [x] `provision.sh` ends with PASS on a fresh image, twice, no UI.
- [x] Plugin configs inside the repacked ZIP take effect (OPC UA reachable on
      the CTRL address, not only localhost).
- [ ] The program auto-starts after reboot (container restart only).
- [ ] `peak_used_mb` during compile (361 MiB peak in Docker; VM not run) leaves at least 25% of VM RAM free, or the
      VM size you would need is acceptable (record it).
- [x] Compile completes in under 10 minutes (5 s, tiny program).

Any unchecked item with no workaround: **no-go**, take the v3 fallback
(GRFICSv3 pattern, DESIGN §12.1) and note the FUXA link changes.

## Result

Run in Docker on a no-KVM host (4 vCPU, Docker 29.9.2). No VM was booted, so
the reboot and VM-RAM items are only approximated.

- **Decision:** go (v4), with the changes listed in Follow-ups. Status:
  partial, because Docker cannot confirm a VM reboot or the VM RAM size.
- **Runtime:** `Autonomy-Logic/openplc-runtime` tag v4.3.2, commit
  `9fb5e1a29b574806f5aea3a4e78202c7a5d658ee`. Image built from the repo's
  `Dockerfile` (`debian:bookworm-slim`, `install.sh --native`), image id
  `sha256:e3c0a71fc1e4a400476c9abd7040e054cb53c76a2314162a00b1fd6d68392bcd`,
  1.46 GB. Python 3.11.2 inside; plugin venvs 276 MB. ZIPs were built with
  strucpp commit `fcccfbdf53704fad1f5e71dd2a05d440b9028b47` (package 0.7.0).
  In this sandbox the build needs the egress proxy CA added to the image
  (`install.sh` fetches cmake over HTTPS); that is a sandbox detail only.
- **Evidence:**
  - `GET /api/capabilities` -> `{"minEditorVersion":"4.3.3","projectSnapshot":true,"runtimeVersion":"v4.3.2"}`.
    The Editor must be >= 4.3.3 for this runtime.
  - `provision.sh` PASS three times: twice on one container (second run:
    `create-user` -> `HTTP 401 {"msg":"Authentication required"}`, login
    still works) and once on a fresh container (`HTTP 201 {"id":1,"msg":"User
    created","role":"admin"}`). No UI. `compile_seconds=5` (the script polls every
    5 s, so this means "at most 5 s"; a 1 s poll of an `?clean=1` upload
    measured 1 s, probably ccache hits). Final status:
    `STATUS:RUNNING`, TASK0 20 ms, 0 overruns.
  - **Upload field name:** `file` (`webserver/app.py` `_handle_upload_file`
    reads `flask.request.files["file"]`; optional `?clean=1`). Reply is
    `{"CompilationStatus":"COMPILING","UploadFileFail":"","ProjectSnapshotWarning":""}`.
  - **Plugin config location (resolves the `conf/` guess):** `conf/` at the
    ZIP root. `update_plugin_configurations()` in
    `webserver/plcapp_management.py` scans `core/generated/conf/*.json`; the
    file name without `.json` must equal the plugin name in `plugins.conf`
    (`opcua`, `modbus_slave`, `modbus_master`, `s7comm`). Log excerpt:
    `Found 4 config files in core/generated/conf: ['modbus_master', 's7comm', 'modbus_slave', 'opcua']`,
    then `Copied config file from core/generated/conf/opcua.json to
    ./core/src/drivers/plugins/python/opcua/opcua.json` (s7comm goes to
    `./build/plugins/s7comm.json`). A plugin with no matching file is
    **disabled**; a ZIP with no `conf/` disables every plugin
    (`No conf directory found ... disabling all plugins`).
  - **OPC UA rebind confirmed.** With `endpoint_url` =
    `opc.tcp://0.0.0.0:4840/openplc/opcua`, `opcua_s7_probe.py` from another
    container lists `cnt`, `inp` and reads `cnt` changing (ns=2;s=PLC.cnt).
    With `opc.tcp://localhost:4840/...` the server logs "started" but the
    remote client gets `ConnectionRefusedError(111)`. The template default
    must be rebound.
  - **Other plugins:** `modbus_read.py` -> `holding[0..0] = [1009]`, PASS;
    s7comm `DB1[0:4]` reads the live counter; `modbus_master` polling the
    local `modbus_slave` (fc 3) logged `Started TCP thread for device: self`.
  - **Auto-start:** `docker restart` -> `STATUS:RUNNING` and OPC UA/S7
    answering with no API call beyond login. This is a container restart,
    not a VM reboot.
  - **Memory:** steady state with `modbus_slave`, `modbus_master`, `opcua`,
    `s7comm` running and the program at 20 ms: 161 MiB container usage
    (`plc_main` 128 MB RSS, webserver python 64 MB). cgroup peak since boot,
    including the first cold compile and page cache: 361 MiB. The test
    program is three variables, so this says nothing about a brewhouse-size
    compile (`compile.sh` caps jobs at min(nproc-1, RAM GB), and the
    Makefile comment cites 500-700 MB per `cc1plus` on Pi-class targets).
  - **Holding-register map (DESIGN §12.5):** with a segmented
    `buffer_mapping` of `qw_count=2, mw_count=1`, holding registers 0, 1, 2
    returned `%QW0, %QW1, %MW0` (111, 222, 333) and address 3 gave
    exception 2. Log: `Segmented holding registers: %QW=0-1, %MW=2-2,
    %MD=3-2, %ML=3-2`. Input registers are `%IW` only, from 0. Counts are
    clamped to the program's located-variable image; with an empty
    `buffer_mapping` only the variables the program declares are exposed.
- **Follow-ups:**
  - v4.3.x is a strucpp-based runtime. The ZIP must carry `generated_debug.cpp`
    (without it `START:OK` is returned but the load fails with `undefined
    symbol ...debug_array_count` and status stays `STATUS:EMPTY`;
    `provision.sh` now fails on that). The `strucpp` CLI `-d` flag does not
    write it; `gen_program.mjs` uses the library API and also writes
    `debug_map.json`.
  - `conf/opcua.json` needs `format_version: 2` inside `config` and a
    `size` per variable (INT = 2), and each variable addresses the program
    by `arr`/`elem` from `debug_map.json` (not by name). The `cherrymountain`
    app must therefore derive OPC UA configs from the compiled program's
    debug map, not from `plant.yaml` alone.
  - Only `file` is needed for upload; `UploadFileFail` must be empty;
    `START:OK` does not prove the program loaded, so poll
    `/api/status` for `STATUS:RUNNING`.
  - Not exercised: a VM reboot, VM RAM sizing (use a real program),
    `modbus_master` against an ot-sim Modbus server, OPC UA security
    profiles other than `None`/Anonymous, `docker`-less native install
    (`install.sh` default mode installs Docker; `--native` is what the
    Dockerfile uses).
