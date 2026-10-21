#!/usr/bin/env python3
"""Probe a three-host phenix cluster on one runner, one risky part at a time.

The runner is the head node; compute1 and compute2 are privileged containers
with their own hostname, network namespace, Open vSwitch and minimega. Each
stage proves one assumption the cluster job depends on, and later stages are
skipped when a stage they need fails:

  ovs        each container runs its own Open vSwitch beside the runner's
  minimega   the runner-built minimega runs in the containers, with KVM
  mesh       the head meshes with both containers over the Docker network
  trunk      a veth trunk star carries VLANs between the three bridges
  placement  minimega schedules VMs to each of the three hosts
  phenix     phenix places one real guest on each host (deploy mode all and
             a user scheduler) from the shared image directory
  c2         every guest reaches C2 through its own host's minimega
  ping       the guests ping each other across hosts, through the trunks
  stop       stopping the experiment cleans up every host

Usage: probe.py <result.json>   (as root, after the job starts the containers)
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
PHENIX = ["phenix", "--store.endpoint", "bolt:///etc/phenix/store.bdb"]
IMAGES = Path("/phenix/images")
HEAD = socket.gethostname()
NODES = {"compute1": "172.31.0.11", "compute2": "172.31.0.12"}
EXP = "mnprobe"
# One guest per host, each named for where it runs.
PLACEMENT = {"node1": "head", "node2": "compute1", "node3": "compute2"}
ADDRESSES = {"node1": "192.168.1.1", "node2": "192.168.1.2", "node3": "192.168.1.3"}
WORK = Path("/tmp/cluster-ci")


def run(cmd, timeout=300):
    """Run an argument vector and return (ok, combined output)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode == 0, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout} s: {cmd}"


def dexec(node, *cmd, timeout=120):
    return run(["docker", "exec", node, *cmd], timeout)


def sock(node):
    return "/tmp/minimega/minimega" if node == "head" else f"/mnt/mm/{node}/minimega"


def mm_resp(command, node="head", ns=None, timeout=300):
    """Run one minimega command over a node's socket and return its responses."""
    if ns:
        command = f'namespace "{ns}" {command}'
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(sock(node))
    s.sendall(json.dumps({"Command": command}).encode())
    dec = json.JSONDecoder()
    buf, resps, done = "", [], False
    while not done:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk.decode(errors="replace")
        while buf.strip():
            try:
                obj, end = dec.raw_decode(buf.lstrip())
            except json.JSONDecodeError:
                break
            buf = buf.lstrip()[end:]
            if obj.get("Status"):
                continue
            resps += obj.get("Resp") or []
            if not obj.get("More"):
                done = True
                break
    s.close()
    return resps


def mm(command, node="head", ns=None):
    """Run one minimega command; return (rows with their host, errors)."""
    rows, errs = [], []
    for r in mm_resp(command, node, ns):
        if r.get("Error"):
            errs.append(f"{r.get('Host')}: {r['Error']}")
        hdr = r.get("Header") or []
        rows += [{"_host": r.get("Host"), **dict(zip(hdr, row))} for row in r.get("Tabular") or []]
    return rows, errs


def text(command, node="head", ns=None):
    return "\n".join((r.get("Response") or "").strip() for r in mm_resp(command, node, ns)).strip()


def phenix(*args, timeout=900):
    return run([*PHENIX, *args], timeout)


def phenix_json(*args):
    p = subprocess.run([*PHENIX, *args, "-o", "json"], capture_output=True, text=True, check=False)
    return json.loads(p.stdout) if p.returncode == 0 else {}


def host_of(where):
    return HEAD if where == "head" else where


def qemu_counts():
    counts = {}
    for c in NODES:
        _, out = dexec(c, "pgrep", "-c", "-f", "qemu-system")
        counts[c] = int(out) if out.strip().isdigit() else out
    head = 0
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            argv = p.read_bytes().split(b"\0")
        except OSError:
            continue
        if argv and b"qemu-system" in argv[0] and any(b"/tmp/minimega/" in a for a in argv):
            head += 1
    counts["head"] = head
    return counts


def guest_run(vm, script, wait=120):
    """Run script in guest vm over C2. Returns (stdout or None, {host: command id})."""
    rows, _ = mm(".columns name,uuid vm info", ns=EXP)
    uuid = next((r.get("uuid", "").lower() for r in rows if r.get("name") == vm), "")
    if not uuid:
        return None, {}
    start, end = f"@@start-{time.time_ns()}@@", f"@@end-{time.time_ns()}@@"
    enc = base64.b64encode(f"echo {start}\n{script}\necho {end}".encode()).decode()
    mm(f"cc filter uuid={uuid}", ns=EXP)
    resps = mm_resp(f"cc exec bash -c 'eval \"$(echo {enc} | base64 -d)\"'", ns=EXP)
    mm("clear cc filter", ns=EXP)
    # minimega returns the new command's ID as the response's Data.
    ids = {r.get("Host"): str(r.get("Data", "")) for r in resps}
    cids = {c for c in ids.values() if c.isdigit()}
    t0 = time.time()
    try:
        while time.time() - t0 < wait:
            for cid in cids:
                base = IMAGES / EXP / "miniccc_responses" / cid
                for d in base.glob("*") if base.is_dir() else []:
                    out = d / "stdout"
                    if d.name.lower() == uuid and out.exists():
                        t = out.read_text(errors="replace")
                        if t.startswith(start) and end in t:
                            return t[len(start):].split(end)[0].strip(), ids
            time.sleep(3)
        return None, ids
    finally:
        # A broadcast delete removes the same ID on every host, which is only
        # this command when all hosts agree on it.
        if len(cids) == 1:
            mm(f"cc delete command {cids.pop()}", ns=EXP)


# --- stages -----------------------------------------------------------------

facts = {"head": HEAD}


def s_ovs():
    ok_all, notes = True, []
    _, before = run(["ovs-vsctl", "list-br"])
    for c in NODES:
        ok, out = dexec(c, "ovs-vsctl", "show")
        _, ver = dexec(c, "ovs-vsctl", "--version")
        notes.append(f"{c}: ovs-vsctl show {'ok' if ok else 'FAILED: ' + out[-300:]}; {ver.splitlines()[0] if ver else ''}")
        ok_all &= ok
        ok, out = dexec(c, "ovs-vsctl", "add-br", "probe-br0")
        _, host_brs = run(["ovs-vsctl", "list-br"])
        leaked = "probe-br0" in host_brs.split()
        dexec(c, "ovs-vsctl", "del-br", "probe-br0")
        notes.append(f"{c}: add-br {'ok' if ok else 'FAILED: ' + out[-300:]}, seen on the runner: {leaked}")
        ok_all &= ok and not leaked
    _, after = run(["ovs-vsctl", "list-br"])
    notes.append(f"runner bridges before {before.split()} after {after.split()}")
    return ok_all and before == after, "\n".join(notes)


def s_minimega():
    ok_all, notes = True, []
    head_ver = text("version")
    facts["minimega"] = head_ver
    for c in NODES:
        ver = text("version", c)
        name = text("host name", c)
        chk = [r.get("Error") for r in mm_resp("check", c) if r.get("Error")]
        dev_ok, _ = dexec(c, "sh", "-c", "test -c /dev/kvm && test -c /dev/net/tun")
        ok = ver == head_ver and name == c and not chk and dev_ok
        notes.append(f"{c}: name {name!r}, version {'same' if ver == head_ver else repr(ver)}, check {chk or 'ok'}, /dev/kvm and tun {dev_ok}")
        ok_all &= ok
    return ok_all, "\n".join(notes)


def s_mesh():
    mm("mesh timeout 300")
    errs = []
    for ip in NODES.values():
        errs += [r["Error"] for r in mm_resp(f"mesh dial {ip}") if r.get("Error")]
    size, t0 = "", time.time()
    while time.time() - t0 < 60:
        rows, _ = mm("mesh status")
        size = rows[0].get("size") if rows else ""
        if size == "3":
            break
        time.sleep(2)
    listing = text("mesh list")
    far, _ = mm("mesh status", "compute1")
    sent = {r.get("Host") for r in mm_resp("mesh send all host name")}
    facts["mesh"] = {"size": size, "list": listing, "compute1 size": far[0].get("size") if far else ""}
    ok = size == "3" and sent == set(NODES) and not errs
    return ok, f"size {size}; dial errors {errs}; mesh send all answered by {sorted(h for h in sent if h)}; list:\n{listing}"


def s_trunk():
    notes = []
    for c in NODES:
        _, pid = run(["docker", "inspect", "-f", "{{.State.Pid}}", c])
        for cmd in (["ip", "link", "add", f"mt-{c}", "type", "veth", "peer", "name", "mt-head"],
                    ["ip", "link", "set", "mt-head", "netns", pid.strip()],
                    ["ip", "link", "set", f"mt-{c}", "up"]):
            ok, out = run(cmd)
            if not ok:
                return False, f"{cmd}: {out}"
        dexec(c, "ip", "link", "set", "mt-head", "up")
        e1 = [r["Error"] for r in mm_resp(f"bridge trunk phenix mt-{c}") if r.get("Error")]
        e2 = [r["Error"] for r in mm_resp("bridge trunk phenix mt-head", c) if r.get("Error")]
        if e1 or e2:
            return False, f"bridge trunk on {c}: {e1 + e2}"
    taps = [("head", 300, "10.9.0.1"), ("compute1", 300, "10.9.0.11"), ("compute2", 300, "10.9.0.12"),
            ("compute1", 301, "10.9.1.11"), ("compute2", 302, "10.9.1.12")]
    for node, vlan, ip in taps:
        e = [r["Error"] for r in mm_resp(f"tap create {vlan} bridge phenix ip {ip}/24 probe{vlan}", node) if r.get("Error")]
        if e:
            return False, f"tap create {vlan} on {node}: {e}"
    time.sleep(2)

    def ping(node, ip):
        cmd = ["ping", "-c", "3", "-W", "2", ip]
        return (run(cmd) if node == "head" else dexec(node, *cmd))[0]

    pairs = [("head", "10.9.0.11"), ("head", "10.9.0.12"), ("compute1", "10.9.0.12"), ("compute2", "10.9.0.1")]
    got = {f"{n} -> {ip}": ping(n, ip) for n, ip in pairs}
    isolated = not ping("compute1", "10.9.1.12")
    notes.append(f"pings on VLAN 300: {got}")
    notes.append(f"VLAN 301 on compute1 cannot reach VLAN 302 on compute2: {isolated}")
    _, ports = run(["ovs-vsctl", "list-ports", "phenix"])
    notes.append(f"runner phenix bridge ports: {ports.split()}")
    for node, vlan, _ in taps:
        mm_resp(f"tap delete probe{vlan}", node)
    return all(got.values()) and isolated, "\n".join(notes)


def s_placement():
    ns, notes = "probe", []
    mm("ns queueing true", ns=ns)
    before = text("ns hosts", ns=ns)
    mm("ns add-host localhost", ns=ns)
    after = text("ns hosts", ns=ns)
    notes.append(f"namespace hosts: {before!r}, after add-host localhost: {after!r}")
    want = {f"hollow{i}": h for i, h in enumerate([HEAD, *NODES])}
    for vm, host in want.items():
        for cmd in ("clear vm config", f"vm config schedule {host}", "vm config memory 128", f"vm launch kvm {vm}"):
            _, e = mm(cmd, ns=ns)
            if e:
                return False, f"{cmd}: {e}"
    _, e = mm("vm launch", ns=ns)
    _, e2 = mm("vm start all", ns=ns)
    time.sleep(5)
    rows, _ = mm(".columns name,state vm info", ns=ns)
    got = {r.get("name"): (r["_host"], r.get("state")) for r in rows}
    notes.append(f"launch errors {e + e2}; placed {got}")
    _, neg = mm("vm config schedule nosuchhost", ns=ns)
    notes.append(f"schedule to a host outside the namespace: {neg or 'ACCEPTED'}")
    mm("vm kill all", ns=ns)
    time.sleep(2)
    mm("vm flush", ns=ns)
    mm(f"clear namespace {ns}")
    ok = all(got.get(vm) == (h, "RUNNING") for vm, h in want.items()) and bool(neg)
    return ok, "\n".join(notes)


def s_phenix():
    notes = []
    WORK.mkdir(parents=True, exist_ok=True)
    Path("/etc/phenix-ci").mkdir(parents=True, exist_ok=True)
    Path("/etc/phenix-ci/placement.json").write_text(json.dumps({EXP: PLACEMENT}))
    run(["install", "-m", "0755", str(HARNESS / "phenix-scheduler-ci-place"), "/usr/local/bin/"])
    nodes = [{
        "type": "VirtualMachine",
        "general": {"hostname": vm},
        "hardware": {"os_type": "linux", "vcpus": 1, "memory": 512, "drives": [{"image": "e2e.qc2"}]},
        "network": {"interfaces": [{"name": "if0", "type": "ethernet", "proto": "static",
                                    "address": ADDRESSES[vm], "mask": 24, "vlan": "LAN"}]},
    } for vm in PLACEMENT]
    topo = {"apiVersion": "phenix.sandia.gov/v1", "kind": "Topology", "metadata": {"name": EXP}, "spec": {"nodes": nodes}}
    path = WORK / f"{EXP}.json"
    path.write_text(json.dumps(topo))
    steps = [("config create", ["config", "create", str(path)]),
             ("create", ["--deploy-mode", "all", "experiment", "create", EXP, "-t", EXP, "--vlan-min", "200", "--vlan-max", "210"]),
             ("schedule", ["experiment", "schedule", EXP, "ci-place"])]
    for label, args in steps:
        ok, out = phenix(*args)
        if not ok:
            return False, f"{label}: {out[-1500:]}"
    spec = phenix_json("config", "get", f"experiment/{EXP}").get("spec") or {}
    want = {vm: host_of(h) for vm, h in PLACEMENT.items()}
    try:
        hosts = [h.get("name") for h in json.loads(Path(f"/etc/phenix-ci/hosts-{EXP}.json").read_text())]
    except (OSError, ValueError):
        hosts = None
    notes.append(f"spec deployMode {spec.get('deployMode')!r}, schedules {spec.get('schedules')}; scheduler saw hosts {hosts}")
    t0 = time.time()
    ok, out = phenix("experiment", "start", EXP, timeout=1200)
    facts["start_seconds"] = round(time.time() - t0)
    if not ok:
        return False, "\n".join(notes + [f"start: {out[-2000:]}"])
    script = "\n".join(p.read_text(errors="replace") for p in Path(spec.get("baseDir") or "/nonexistent").glob("mm_files/*.mm"))
    notes.append(f"script has 'ns add-host localhost': {'ns add-host localhost' in script}; schedule lines: "
                 f"{[l for l in script.splitlines() if l.startswith('vm config schedule')]}")
    rows, _ = mm(".columns name,state vm info", ns=EXP)
    got = {r.get("name"): r["_host"] for r in rows}
    states = {r.get("name"): r.get("state") for r in rows}
    notes.append(f"placed {got}; states {states}; qemu per host {qemu_counts()}")
    notes.append(f"namespace hosts: {text('ns hosts', ns=EXP)!r}")
    status = phenix_json("config", "get", f"experiment/{EXP}").get("status") or {}
    notes.append(f"status schedules {status.get('schedules')}, vlans {status.get('vlans')}")
    ok = got == want and all(s == "RUNNING" for s in states.values()) and spec.get("deployMode") == "all"
    return ok, "\n".join(notes)


def s_c2():
    t0, seen = time.time(), set()
    while time.time() - t0 < 900:
        rows, _ = mm("cc clients", ns=EXP)
        seen = {r.get("hostname") for r in rows}
        if set(PLACEMENT) <= seen:
            break
        time.sleep(5)
    facts["c2_seconds"] = round(time.time() - t0)
    return set(PLACEMENT) <= seen, f"C2 clients {sorted(h for h in seen if h)} after {facts['c2_seconds']} s"


def s_ping():
    notes, ok_all, drift = [], True, []
    for src, dst in [("node1", "node2"), ("node1", "node3"), ("node2", "node3"), ("node3", "node1")]:
        out, ids = guest_run(src, f"ping -c 3 -W 2 {ADDRESSES[dst]} >/dev/null && echo ok || echo fail")
        if len({c for c in ids.values() if c}) > 1:
            drift.append(ids)
        notes.append(f"{src} ({host_of(PLACEMENT[src])}) -> {dst} ({host_of(PLACEMENT[dst])}): {out!r}; command ids {ids}")
        ok_all &= out == "ok"
    facts["cc_id_drift"] = drift
    return ok_all, "\n".join(notes)


def s_stop():
    ok, out = phenix("experiment", "stop", EXP, timeout=600)
    time.sleep(3)
    counts = qemu_counts()
    rows, _ = mm("mesh status")
    ports = {"head": run(["ovs-vsctl", "list-ports", "phenix"])[1].split()}
    for c in NODES:
        ports[c] = dexec(c, "ovs-vsctl", "list-ports", "phenix")[1].split()
    phenix("experiment", "delete", EXP)
    clean = ok and not any(counts.values())
    return clean, f"stop {'ok' if ok else out[-800:]}; qemu left {counts}; mesh size {rows[0].get('size') if rows else '?'}; phenix bridge ports {ports}"


def s_budget():
    _, free = run(["free", "-m"])
    _, up = run(["uptime"])
    return True, f"{up}\n{free}"


STAGES = [
    ("ovs", s_ovs, ()),
    ("minimega", s_minimega, ()),
    ("mesh", s_mesh, ("minimega",)),
    ("trunk", s_trunk, ("ovs", "mesh")),
    ("placement", s_placement, ("mesh",)),
    ("phenix", s_phenix, ("mesh",)),
    ("c2", s_c2, ("phenix",)),
    ("budget", s_budget, ()),
    ("ping", s_ping, ("c2",)),
    ("stop", s_stop, ("phenix",)),
]


def main():
    out = Path(sys.argv[1])
    res, passed = {"stages": [], "facts": facts}, {}
    for name, fn, needs in STAGES:
        missing = [n for n in needs if not passed.get(n)]
        t0 = time.time()
        if missing:
            ok, detail = False, f"skipped: needs {missing}"
        else:
            try:
                ok, detail = fn()
            except Exception:
                ok, detail = False, traceback.format_exc()[-2000:]
        passed[name] = ok
        res["stages"].append({"stage": name, "ok": ok, "seconds": round(time.time() - t0), "detail": detail})
        print(f"== {name}: {'ok' if ok else 'FAILED'} ({round(time.time() - t0)} s)\n{detail}\n", flush=True)
        out.write_text(json.dumps(res, indent=1))
    res["ok"] = all(passed.values())
    out.write_text(json.dumps(res, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
