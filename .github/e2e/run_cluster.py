#!/usr/bin/env python3
"""Run three phenix experiments at once on a three-host cluster on one runner.

The runner is the head node. compute1 and compute2 are privileged containers
with their own hostname, network namespace, Open vSwitch and minimega, which
the cluster job in .github/workflows/e2e.yml starts. Experiments mn-a, mn-b
and mn-c each put one e2e guest on every host, in a different order, on one
shared subnet that only their VLANs keep apart. The stages, in order (a stage
is skipped when one it needs failed):

  nodes      each container runs its own Open vSwitch and the runner's
             minimega build, with KVM
  mesh       the head meshes with both containers
  trunk      a veth trunk star carries VLANs between the three bridges
  create     phenix creates the experiments (deploy mode all, disjoint VLAN
             ranges) and a user scheduler places their VMs
  start      the experiments start one after another; every VM runs on its
             scheduled host, and each experiment's VLAN has one tap per host
  c2         all nine guests reach C2 through their own host's minimega
  ping       the guests of each experiment ping each other across hosts
  isolation  no guest reaches another experiment's addresses
  steady     a minute later every VM still runs
  restart    stopping one experiment removes only its VMs and taps, the other
             two keep working, and it starts again
  parallel   all three stop, then start at the same time, and pass the start,
             c2, ping and isolation checks again
  teardown   stopping all three leaves no VMs and only the trunk ports

Usage: sudo -E python3 run_cluster.py <result.json>
"""

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
PHENIX = ["phenix", "--store.endpoint", "bolt:///etc/phenix/store.bdb"]
IMAGES = Path("/phenix/images")
HEAD = socket.gethostname()
NODES = {"compute1": "172.31.0.11", "compute2": "172.31.0.12"}
VMS = ["node1", "node2", "node3"]
# Each experiment puts one guest on every host, in a different order, so every
# pair of hosts carries traffic for every experiment. The VLAN ranges leave
# gaps: minimega rejects ranges that only touch.
EXPS = {
    "mn-a": {"vlans": (200, 210), "first": 1, "hosts": ["head", "compute1", "compute2"]},
    "mn-b": {"vlans": (220, 230), "first": 11, "hosts": ["compute1", "compute2", "head"]},
    "mn-c": {"vlans": (240, 250), "first": 21, "hosts": ["compute2", "head", "compute1"]},
}
WORK = Path("/tmp/cluster-ci")
C2_WAIT = 900


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


def errors(command, node="head", ns=None):
    return [f"{r.get('Host')}: {r['Error']}" for r in mm_resp(command, node, ns) if r.get("Error")]


def phenix(*args, timeout=900):
    return run([*PHENIX, *args], timeout)


def phenix_json(*args):
    p = subprocess.run([*PHENIX, *args, "-o", "json"], capture_output=True, text=True, check=False)
    return json.loads(p.stdout) if p.returncode == 0 else {}


def host_of(where):
    return HEAD if where == "head" else where


def placement(exp):
    return {vm: host_of(h) for vm, h in zip(VMS, EXPS[exp]["hosts"])}


def address(exp, vm):
    return f"192.168.1.{EXPS[exp]['first'] + VMS.index(vm)}"


def qemu_counts():
    """QEMU processes per host. minimega runs them as kvm, so they are found by
    the per-VM directory under each node's minimega base."""
    counts = {}
    for c in NODES:
        _, out = dexec(c, "pgrep", "-c", "-f", f"/tmp/mm-{c}/[0-9]+/")
        counts[c] = int(out) if out.strip().isdigit() else out
    pat, head = re.compile(rb"/tmp/minimega/\d+/"), 0
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            argv = p.read_bytes().split(b"\0")
        except OSError:
            continue
        if any(pat.search(a) for a in argv):
            head += 1
    counts["head"] = head
    return counts


def bridge_ports():
    ports = {"head": run(["ovs-vsctl", "list-ports", "phenix"])[1].split()}
    for c in NODES:
        ports[c] = dexec(c, "ovs-vsctl", "list-ports", "phenix")[1].split()
    return ports


def tagged_ports(vlan):
    """The OVS ports on each node that carry VLAN vlan as an access port."""
    q = ["ovs-vsctl", "--bare", "--columns=name", "find", "Port", f"tag={vlan}"]
    got = {"head": run(q)[1].split()}
    for c in NODES:
        got[c] = dexec(c, *q)[1].split()
    return got


def exp_vlan(exp):
    status = phenix_json("config", "get", f"experiment/{exp}").get("status") or {}
    return (status.get("vlans") or {}).get("LAN")


def guest_run(exp, vm, script, wait=120):
    """Run script in guest vm over C2. Returns (stdout or None, {host: command id})."""
    rows, _ = mm(".columns name,uuid vm info", ns=exp)
    uuid = next((r.get("uuid", "").lower() for r in rows if r.get("name") == vm), "")
    if not uuid:
        return None, {}
    start, end = f"@@start-{time.time_ns()}@@", f"@@end-{time.time_ns()}@@"
    enc = base64.b64encode(f"echo {start}\n{script}\necho {end}".encode()).decode()
    mm(f"cc filter uuid={uuid}", ns=exp)
    resps = mm_resp(f"cc exec bash -c 'eval \"$(echo {enc} | base64 -d)\"'", ns=exp)
    mm("clear cc filter", ns=exp)
    # minimega returns the new command's ID as the response's Data; every host
    # in the namespace answers with its own.
    ids = {r.get("Host"): str(r.get("Data", "")) for r in resps}
    cids = {c for c in ids.values() if c.isdigit()}
    t0 = time.time()
    try:
        while time.time() - t0 < wait:
            for cid in cids:
                base = IMAGES / exp / "miniccc_responses" / cid
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
            mm(f"cc delete command {cids.pop()}", ns=exp)


facts = {"head": HEAD, "cc_id_drift": [], "c2_seconds": {}, "start_seconds": {}}


# --- checks shared by several stages ------------------------------------------

def check_running(exps):
    """Every VM of exps runs on its scheduled host, and each experiment's VLAN
    is in its range, differs from the others and has one tap on every host."""
    ok, notes, vlans = True, [], {}
    for exp in exps:
        rows, _ = mm(".columns name,state vm info", ns=exp)
        got = {r.get("name"): (r["_host"], r.get("state")) for r in rows}
        want = {vm: (h, "RUNNING") for vm, h in placement(exp).items()}
        vlan = exp_vlan(exp)
        vlans[exp] = vlan
        lo, hi = EXPS[exp]["vlans"]
        taps = tagged_ports(vlan) if vlan else {}
        taps_ok = bool(taps) and all(len(v) == 1 for v in taps.values())
        good = got == want and isinstance(vlan, int) and lo <= vlan < hi and taps_ok
        ok &= good
        notes.append(f"{exp}: {'ok' if good else 'WRONG'}; VMs {got}; VLAN {vlan} (range {lo}-{hi}); taps per host {taps}")
    if len(set(vlans.values())) != len(vlans):
        ok = False
        notes.append(f"experiments share a VLAN: {vlans}")
    notes.append(f"QEMU per host {qemu_counts()}")
    return ok, notes


def wait_c2(exps):
    t0, seen = time.time(), {}
    while time.time() - t0 < C2_WAIT:
        for exp in exps:
            rows, _ = mm("cc clients", ns=exp)
            seen[exp] = sorted({r.get("hostname") for r in rows} & set(VMS))
        if all(seen.get(e) == VMS for e in exps):
            break
        time.sleep(5)
    for exp in exps:
        facts["c2_seconds"].setdefault(exp, []).append(round(time.time() - t0))
    ok = all(seen.get(e) == VMS for e in exps)
    return ok, [f"{e}: C2 clients {seen.get(e)}" for e in exps] + [f"after {round(time.time() - t0)} s"]


def ping_pairs(exps):
    ok, notes = True, []
    for exp in exps:
        for src, dst in [("node1", "node2"), ("node1", "node3"), ("node2", "node3")]:
            out, ids = guest_run(exp, src, f"ping -c 3 -W 2 {address(exp, dst)} >/dev/null && echo ok || echo fail")
            if len({c for c in ids.values() if c}) > 1:
                facts["cc_id_drift"].append({exp: ids})
            ok &= out == "ok"
            hs, hd = placement(exp)[src], placement(exp)[dst]
            notes.append(f"{exp} {src}@{hs} -> {dst}@{hd}: {out!r}")
    return ok, notes


def isolated(exps):
    ok, notes = True, []
    for exp in exps:
        others = [address(o, "node1") for o in exps if o != exp]
        for ip in others:
            out, _ = guest_run(exp, "node1", f"ping -c 2 -W 2 {ip} >/dev/null && echo reached || echo fail")
            ok &= out == "fail"
            notes.append(f"{exp} node1 -> {ip}: {out!r}")
    return ok, notes


def start(exp):
    t0 = time.time()
    ok, out = phenix("experiment", "start", exp, timeout=1200)
    facts["start_seconds"].setdefault(exp, []).append(round(time.time() - t0))
    return ok, out


def stop_all():
    """Stop every experiment that runs; return the failures."""
    notes = []
    for exp in EXPS:
        ok, out = phenix("experiment", "stop", exp, timeout=600)
        if not ok and "isn't running" not in out:
            notes.append(f"stop {exp}: {out[-600:]}")
    return notes


# --- stages -------------------------------------------------------------------

def s_nodes():
    ok_all, notes = True, []
    head_ver = text("version")
    facts["minimega"] = head_ver
    for c in NODES:
        ovs_ok, out = dexec(c, "ovs-vsctl", "show")
        ver = text("version", c)
        name = text("host name", c)
        chk = errors("check", c)
        dev_ok, _ = dexec(c, "sh", "-c", "test -c /dev/kvm && test -c /dev/net/tun")
        ok = ovs_ok and ver == head_ver and name == c and not chk and dev_ok
        notes.append(f"{c}: Open vSwitch {'ok' if ovs_ok else 'FAILED: ' + out[-300:]}; minimega name {name!r}, "
                     f"version {'same' if ver == head_ver else repr(ver)}, check {chk or 'ok'}; /dev/kvm and tun {dev_ok}")
        ok_all &= ok
    return ok_all, "\n".join(notes)


def s_mesh():
    mm("mesh timeout 300")
    errs = []
    for ip in NODES.values():
        errs += errors(f"mesh dial {ip}")
    size, t0 = "", time.time()
    while time.time() - t0 < 60:
        rows, _ = mm("mesh status")
        size = rows[0].get("size") if rows else ""
        if size == "3":
            break
        time.sleep(2)
    sent = {r.get("Host") for r in mm_resp("mesh send all host name")}
    ok = size == "3" and sent == set(NODES) and not errs
    return ok, f"size {size}; dial errors {errs}; mesh send all answered by {sorted(h for h in sent if h)}\n{text('mesh list')}"


def s_trunk():
    for c in NODES:
        _, pid = run(["docker", "inspect", "-f", "{{.State.Pid}}", c])
        for cmd in (["ip", "link", "add", f"mt-{c}", "type", "veth", "peer", "name", "mt-head"],
                    ["ip", "link", "set", "mt-head", "netns", pid.strip()],
                    ["ip", "link", "set", f"mt-{c}", "up"]):
            ok, out = run(cmd)
            if not ok:
                return False, f"{cmd}: {out}"
        dexec(c, "ip", "link", "set", "mt-head", "up")
        e = errors(f"bridge trunk phenix mt-{c}") + errors("bridge trunk phenix mt-head", c)
        if e:
            return False, f"bridge trunk on {c}: {e}"
    # Host taps prove the trunks before any guest boots: VLAN 300 on every
    # host, and 301 and 302 on one subnet that must stay apart.
    taps = [("head", 300, "10.9.0.1"), ("compute1", 300, "10.9.0.11"), ("compute2", 300, "10.9.0.12"),
            ("compute1", 301, "10.9.1.11"), ("compute2", 302, "10.9.1.12")]
    for node, vlan, ip in taps:
        e = errors(f"tap create {vlan} bridge phenix ip {ip}/24 probe{vlan}", node)
        if e:
            return False, f"tap create {vlan} on {node}: {e}"
    time.sleep(2)

    def ping(node, ip):
        cmd = ["ping", "-c", "3", "-W", "2", ip]
        return (run(cmd) if node == "head" else dexec(node, *cmd))[0]

    got = {f"{n} -> {ip}": ping(n, ip) for n, ip in
           [("head", "10.9.0.11"), ("head", "10.9.0.12"), ("compute1", "10.9.0.12"), ("compute2", "10.9.0.1")]}
    apart = not ping("compute1", "10.9.1.12")
    for node, vlan, _ in taps:
        mm_resp(f"tap delete probe{vlan}", node)
    return all(got.values()) and apart, f"pings on VLAN 300: {got}\nVLAN 301 cannot reach VLAN 302: {apart}"


def s_create():
    notes = []
    WORK.mkdir(parents=True, exist_ok=True)
    Path("/etc/phenix-ci").mkdir(parents=True, exist_ok=True)
    Path("/etc/phenix-ci/placement.json").write_text(json.dumps({e: dict(zip(VMS, x["hosts"])) for e, x in EXPS.items()}))
    ok, out = run(["install", "-m", "0755", str(HERE / "cluster" / "phenix-scheduler-ci-place"), "/usr/local/bin/"])
    if not ok:
        return False, out
    for exp, x in EXPS.items():
        nodes = [{
            "type": "VirtualMachine",
            "general": {"hostname": vm},
            "hardware": {"os_type": "linux", "vcpus": 1, "memory": 512, "drives": [{"image": "e2e.qc2"}]},
            "network": {"interfaces": [{"name": "if0", "type": "ethernet", "proto": "static",
                                        "address": address(exp, vm), "mask": 24, "vlan": "LAN"}]},
        } for vm in VMS]
        path = WORK / f"{exp}.json"
        path.write_text(json.dumps({"apiVersion": "phenix.sandia.gov/v1", "kind": "Topology",
                                    "metadata": {"name": exp}, "spec": {"nodes": nodes}}))
        lo, hi = x["vlans"]
        for label, args in [("config create", ["config", "create", str(path)]),
                            ("create", ["--deploy-mode", "all", "experiment", "create", exp, "-t", exp,
                                        "--vlan-min", str(lo), "--vlan-max", str(hi)]),
                            ("schedule", ["experiment", "schedule", exp, "ci-place"])]:
            ok, out = phenix(*args)
            if not ok:
                return False, "\n".join(notes + [f"{exp} {label}: {out[-1500:]}"])
        spec = phenix_json("config", "get", f"experiment/{exp}").get("spec") or {}
        try:
            seen = sorted(h.get("name") for h in json.loads(Path(f"/etc/phenix-ci/hosts-{exp}.json").read_text()))
        except (OSError, ValueError):
            seen = None
        good = spec.get("deployMode") == "all" and spec.get("schedules") == placement(exp)
        notes.append(f"{exp}: deployMode {spec.get('deployMode')!r}, schedules {spec.get('schedules')}; "
                     f"the scheduler saw hosts {seen}")
        if not good:
            return False, "\n".join(notes)
    return True, "\n".join(notes)


def s_start():
    notes = []
    for exp in EXPS:
        ok, out = start(exp)
        if not ok:
            return False, "\n".join(notes + [f"start {exp}: {out[-2000:]}"])
        script = "\n".join(p.read_text(errors="replace") for p in Path(f"/phenix/experiments/{exp}").glob("mm_files/*.mm"))
        if "ns add-host localhost" not in script:
            notes.append(f"{exp}: the minimega script has no 'ns add-host localhost'")
    ok, more = check_running(list(EXPS))
    return ok and not notes, "\n".join(notes + more)


def s_c2():
    ok, notes = wait_c2(list(EXPS))
    return ok, "\n".join(notes)


def s_ping():
    ok, notes = ping_pairs(list(EXPS))
    return ok, "\n".join(notes + [f"command ID drift: {facts['cc_id_drift'] or 'none'}"])


def s_isolation():
    ok, notes = isolated(list(EXPS))
    return ok, "\n".join(notes)


def s_steady():
    time.sleep(60)
    ok, notes = True, []
    for exp in EXPS:
        rows, _ = mm(".columns name,state vm info", ns=exp)
        states = {r.get("name"): r.get("state") for r in rows}
        ok &= sorted(states) == VMS and all(s == "RUNNING" for s in states.values())
        notes.append(f"{exp}: {states}")
    return ok, "\n".join(notes)


def s_restart():
    victim, rest = "mn-b", ["mn-a", "mn-c"]
    vlan = exp_vlan(victim)
    notes = []
    ok, out = phenix("experiment", "stop", victim, timeout=600)
    if not ok:
        return False, f"stop {victim}: {out[-1500:]}"
    time.sleep(3)
    rows, _ = mm(".columns name vm info", ns=victim)
    counts = qemu_counts()
    left = tagged_ports(vlan) if vlan else {}
    ports = bridge_ports()
    gone = not rows and all(v == 2 for v in counts.values()) and not any(left.values())
    trunks = "mt-compute1" in ports["head"] and "mt-compute2" in ports["head"] and all("mt-head" in ports[c] for c in NODES)
    snaps = sorted(p.name for p in IMAGES.glob(f"{HEAD}_{victim}_*"))
    notes.append(f"after stopping {victim}: its VMs {[r.get('name') for r in rows]}, QEMU per host {counts}, "
                 f"its VLAN {vlan} taps {left}, trunk ports kept {trunks}, its snapshots left {snaps}")
    p_ok, p_notes = ping_pairs(rest)
    notes += [f"while {victim} is stopped: " + n for n in p_notes]
    s_ok, s_out = start(victim)
    if not s_ok:
        return False, "\n".join(notes + [f"start {victim} again: {s_out[-1500:]}"])
    r_ok, r_notes = check_running([victim])
    c_ok, c_notes = wait_c2([victim])
    g_ok, g_notes = ping_pairs([victim]) if c_ok else (False, ["skipped: no C2"])
    notes += r_notes + c_notes + g_notes
    return gone and trunks and p_ok and r_ok and c_ok and g_ok, "\n".join(notes)


def s_parallel():
    notes = stop_all()
    time.sleep(3)
    counts = qemu_counts()
    if notes or any(counts.values()):
        return False, "\n".join(notes + [f"after stopping all: QEMU per host {counts}"])
    t0 = time.time()
    procs = {e: subprocess.Popen([*PHENIX, "experiment", "start", e], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True) for e in EXPS}
    failed = []
    for e, p in procs.items():
        try:
            out, _ = p.communicate(timeout=1500)
        except subprocess.TimeoutExpired as x:
            p.kill()
            p.wait()  # reap it, as subprocess.run does
            failed.append(f"start {e}: timed out after 1500 s\n{(x.output or b'').decode(errors='replace')[-1200:]}")
            continue
        if p.returncode:
            failed.append(f"start {e}: {(out or '')[-1200:]}")
    facts["parallel_start_seconds"] = round(time.time() - t0)
    if failed:
        return False, "\n".join(failed)
    r_ok, r_notes = check_running(list(EXPS))
    c_ok, c_notes = wait_c2(list(EXPS))
    p_ok, p_notes = ping_pairs(list(EXPS)) if c_ok else (False, ["skipped: no C2"])
    i_ok, i_notes = isolated(list(EXPS)) if c_ok else (False, [])
    notes = [f"three parallel starts took {facts['parallel_start_seconds']} s"] + r_notes + c_notes + p_notes + i_notes
    return r_ok and c_ok and p_ok and i_ok, "\n".join(notes)


def s_teardown():
    notes = stop_all()
    time.sleep(3)
    counts = qemu_counts()
    ports = bridge_ports()
    rows, _ = mm("mesh status")
    size = rows[0].get("size") if rows else "?"
    trunk_only = ports == {"head": ["mt-compute1", "mt-compute2"], "compute1": ["mt-head"], "compute2": ["mt-head"]}
    for exp in EXPS:
        ok, out = phenix("experiment", "delete", exp)
        if not ok:
            notes.append(f"delete {exp}: {out[-600:]}")
    left = sorted(str(p) for e in EXPS for p in [IMAGES / e / "miniccc_responses"] if p.exists())
    notes.append(f"QEMU left {counts}; bridge ports {ports}; mesh size {size}; C2 response directories left {left}")
    ok = not any(counts.values()) and trunk_only and size == "3" and len(notes) == 1
    return ok, "\n".join(notes)


STAGES = [
    ("nodes", s_nodes, ()),
    ("mesh", s_mesh, ("nodes",)),
    ("trunk", s_trunk, ("mesh",)),
    ("create", s_create, ("mesh",)),
    ("start", s_start, ("create", "trunk")),
    ("c2", s_c2, ("start",)),
    ("ping", s_ping, ("c2",)),
    ("isolation", s_isolation, ("c2",)),
    ("steady", s_steady, ("start",)),
    ("restart", s_restart, ("c2",)),
    ("parallel", s_parallel, ("create",)),
    ("teardown", s_teardown, ("create",)),
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
    res["result"] = "pass" if all(passed.values()) else "failed: " + ", ".join(n for n, ok in passed.items() if not ok)
    out.write_text(json.dumps(res, indent=1))
    return 0 if all(passed.values()) else 1


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
