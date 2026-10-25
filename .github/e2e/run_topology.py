#!/usr/bin/env python3
"""Run one topology of this repository under phenix and minimega, and record how far it gets.

Usage: sudo -E python3 run_topology.py <name> <results.json>
TOPOLOGIES_DIR names the checkout whose topologies to run (default: the one
holding this harness).

.github/e2e/topologies.json describes each topology. A run goes through these
stages and stops at the first that fails:

  prepare   load the configs, check the files they name, and fit the topology
            to a CI runner: alias images, let nodes whose image no recipe
            builds stay off (phenix does that itself for a missing image), keep
            off the nodes keep_off names, size the nodes memory names, and
            scale the booted guests' memory to memory_budget, keeping each
            node memory_floor names at its floor; then set the soh metadata
            soh_metadata names, and drop soh custom reachability tests whose
            source or target stays off, the custom tests soh_needs names
            when a node they need stays off, and its packet capture of nodes
            that stay off
  configs   phenix config create
  create    phenix experiment create
  start     phenix experiment start (configure, pre-start, launch, post-start)
  running   every booted VM reaches the RUNNING state; VMs and nodes that apps
            add are recorded too, and one that is not running fails the stage
            unless its disk does not exist (an image no recipe builds)
  c2        every booted guest whose image carries miniccc registers with C2
  steady    the VMs are still running a minute later
  soh       for a scenario with phenix's state-of-health app, soh finished
            its checks and none failed
  soh-flows for a topology that lists soh_flows, soh's running stage (run
            again after the checks made traffic) found packet-capture flows
            from each listed address
  checks    the per-node checks the topology lists pass, run in the guests
            over C2 (for example that a service the topology needs is up)
  stop      phenix experiment stop

When the committed experiment cannot be created and the topology lists fixes
(text replacements in its configs) or patches (to the apps it ships), a second
attempt applies them, so the results also show how far a fixed copy gets. The
committed attempt decides "result"; the retry's goes in "result_with_fixes".
Every adaptation and fix is recorded with the results. A cancelled job ends
the attempt as "cancelled", and a fault in the harness itself as "harness
error" with its traceback.
"""

import base64
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

import yaml

HARNESS = Path(__file__).resolve().parents[2]
# The topologies can come from another ref of this repository than the harness.
REPO = Path(os.environ.get("TOPOLOGIES_DIR") or HARNESS)
PHENIX = ["phenix", "--store.endpoint", "bolt:///etc/phenix/store.bdb"]
IMAGES = Path("/phenix/images")
MM_SOCKET = os.environ.get("MM_SOCKET", "/tmp/minimega/minimega")
WORK = Path(os.environ.get("TOPO_WORK", "/tmp/topo-ci"))
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1"
APPS_PIP = "/opt/phenix-apps/bin/pip"
APPS_FAILED = Path(os.environ.get("APPS_FAILED", "/nonexistent"))


def now():
    return time.strftime("%H:%M:%S")


def phenix(*args, timeout=1800):
    t0 = time.time()
    try:
        p = subprocess.run([*PHENIX, *args], capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode == 0, (p.stdout + p.stderr).strip(), round(time.time() - t0)
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode(errors="replace") + (e.stderr or b"").decode(errors="replace")).strip()
        return False, f"timed out after {timeout} s\n{out}", round(time.time() - t0)


def phenix_json(*args, timeout=120):
    """Run a phenix command that prints JSON and parse its stdout (logs go to stderr)."""
    p = subprocess.run([*PHENIX, *args, "-o", "json"], capture_output=True, text=True, timeout=timeout, check=False)
    return json.loads(p.stdout) if p.returncode == 0 else {}


def mm_resp(command, ns=None):
    """Run one minimega command over its socket and return its responses."""
    if ns:
        command = f'namespace "{ns}" {command}'
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(300)
    s.connect(MM_SOCKET)
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


def mm(command, ns=None):
    """Run one minimega command and return (rows as dicts, errors)."""
    rows, errs = [], []
    for r in mm_resp(command, ns):
        if r.get("Error"):
            errs.append(r["Error"])
        hdr = r.get("Header") or []
        rows += [dict(zip(hdr, row)) for row in r.get("Tabular") or []]
    return rows, errs


def guest_run(exp, vm, script, wait):
    """Run script with bash in guest vm over C2 and return its stdout, or None
    when no answer comes within wait seconds. minimega rewrites $names, URLs
    and # in command arguments, so the script travels base64-encoded, as
    phenix's E2E suite sends it, between a start and an end marker: a reused
    command ID can leave an older response in the same place."""
    rows, _ = mm(".columns name,uuid vm info", exp)
    uuid = next((r.get("uuid", "").lower() for r in rows if r.get("name") == vm), "")
    if not uuid:
        return None
    start, end = f"@@start-{time.time_ns()}@@", f"@@end-{time.time_ns()}@@"
    enc = base64.b64encode(f"echo {start}\n{script}\necho {end}".encode()).decode()
    mm(f"cc filter uuid={uuid}", exp)
    resps = mm_resp(f"cc exec bash -c 'eval \"$(echo {enc} | base64 -d)\"'", exp)
    mm("clear cc filter", exp)
    # minimega returns the new command's ID as the response's Data.
    cid = next((str(r["Data"]) for r in resps if isinstance(r.get("Data"), int)), "")
    if not cid:
        return None
    base = IMAGES / exp / "miniccc_responses" / cid
    t0 = time.time()
    try:
        while time.time() - t0 < wait:
            for d in base.glob("*") if base.is_dir() else []:
                out = d / "stdout"
                if d.name.lower() == uuid and out.exists():
                    text = out.read_text(errors="replace")
                    if text.startswith(start) and end in text:
                        return text[len(start):].split(end)[0].strip()
            time.sleep(3)
        return None
    finally:
        mm(f"cc delete command {cid}", exp)


def snapshot_sources(base_dir):
    """Map each disk snapshot the experiment's minimega scripts make to its base image."""
    src = {}
    for script in Path(base_dir or "/nonexistent").glob("mm_files/*.mm"):
        for m in re.finditer(r"^disk snapshot (\S+) (\S+)", script.read_text(errors="replace"), re.M):
            src[Path(m[2]).name] = m[1]
    return src


def first_disk(column):
    """The first disk's path in a `vm info` disks column (path[,interface][,cache]), or ""."""
    fields = (column or "").split()
    if not fields or fields[0] == "N/A":
        return ""
    path = fields[0].split(",")[0]
    return path if path.startswith("/") else str(IMAGES / path)


def host_paths(v, key=""):
    """Yield the host files a config names: injection sources, and paths under
    /phenix/topologies or /phenix/injects (destinations inside guests are skipped)."""
    if isinstance(v, str):
        if v.startswith(("/phenix/topologies/", "/phenix/injects/")) or (key == "src" and v.startswith("/")):
            yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            if k != "dst":
                yield from host_paths(x, k)
    elif isinstance(v, list):
        for x in v:
            yield from host_paths(x, key)


def named_images(v):
    """Yield the disk image names (*.qc2, *.qcow2, *.img) a config names anywhere."""
    if isinstance(v, str):
        if re.fullmatch(r"[\w.-]+\.(qc2|qcow2|img)", v):
            yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from named_images(x)
    elif isinstance(v, list):
        for x in v:
            yield from named_images(x)


class Attempt:
    def __init__(self, run, label):
        self.run = run
        self.rec = {"label": label, "stages": [], "adaptations": [], "findings": [], "nodes": [], "result": "running"}
        run.res["attempts"].append(self.rec)

    def stage(self, name, ok, detail="", seconds=None):
        self.rec["stages"].append({"stage": name, "ok": ok, "detail": detail[-4000:], "seconds": seconds})
        print(f"[{now()}] {name}: {'ok' if ok else 'FAILED'} {detail[-800:]}", flush=True)
        self.run.save()
        return ok

    def note(self, key, what):
        self.rec[key].append(what)
        print(f"[{now()}] {key[:-1]}: {what}", flush=True)
        self.run.save()

    def finish(self, result):
        self.rec["result"] = result
        self.run.res[self.run.result_key(self.rec)] = result
        self.run.save()
        return result == "passed"


class Run:
    def __init__(self, name, out):
        self.cfg = json.loads((HARNESS / ".github/e2e/topologies.json").read_text())[name]
        self.name = name
        self.out = Path(out)
        head = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(REPO), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False).stdout.strip()
        self.res = {"name": name, "experiment": self.cfg["experiment"], "topologies_commit": head,
                    "attempts": [], "result": "running"}
        self.save()

    def save(self):
        self.out.write_text(json.dumps(self.res, indent=1))

    def result_key(self, rec):
        return "result" if rec is self.res["attempts"][0] else "result_with_fixes"


def load(cfg, fixes):
    """Load every config file of the topology as YAML documents, applying text fixes first."""
    docs = {}
    for key in ("extra", "topology", "scenario"):
        paths = cfg.get(key) or []
        for rel in [paths] if isinstance(paths, str) else paths:
            text = (REPO / rel).read_text()
            for old, new in fixes.items():
                text = text.replace(old, new)
            docs[rel] = [d for d in yaml.safe_load_all(text) if d]
    return docs


def patch_apps(a, patches):
    """Apply patches to this repository and reinstall the apps it ships."""
    for rel in patches:
        p = subprocess.run(["git", "-c", "safe.directory=*", "-C", str(REPO), "apply", str(HARNESS / ".github/e2e" / rel)],
                           capture_output=True, text=True, check=False)
        if p.returncode:
            return a.stage("prepare", False, f"patch {rel} does not apply: {p.stderr}")
        a.note("adaptations", f"patch: {rel}")
    for extra in a.run.cfg.get("extra_apps") or []:
        p = subprocess.run([APPS_PIP, "install", "--quiet", "--no-deps", "--force-reinstall", str(REPO / extra)],
                           capture_output=True, text=True, check=False)
        if p.returncode:
            return a.stage("prepare", False, f"reinstalling {extra} failed: {p.stdout}{p.stderr}")
    # phenix finds apps on PATH, as the workflow's install step links them.
    for script in Path(APPS_PIP).parent.glob("phenix-*"):
        if script.name.startswith(("phenix-app-", "phenix-scorch-", "phenix-scheduler-")):
            link = Path("/usr/local/bin") / script.name
            link.unlink(missing_ok=True)
            link.symlink_to(script)
    return True


def merge(dst, src):
    """Merge mapping src into dst, key by key at every level, and yield each "path=value" set."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            yield from (f"{k}.{x}" for x in merge(dst[k], v))
        else:
            dst[k] = v
            yield f"{k}={v}"


def fit_soh(a, docs, scen):
    """Fit the soh app the experiment runs to the runner and to the nodes that boot."""
    scens = {d["metadata"]["name"]: d for ds in docs.values() for d in ds if d.get("kind") == "Scenario"}
    for app in ((scen or {}).get("spec") or {}).get("apps") or []:
        if app.get("name") != "soh":
            continue
        # An app with fromScenario runs as that scenario's app of the same name.
        if app.get("fromScenario") in scens:
            app = next((x for x in scens[app["fromScenario"]]["spec"].get("apps") or []
                        if x.get("name") == "soh"), app)
        md = app.get("metadata")
        if not isinstance(md, dict):
            continue
        over = list(merge(md, a.run.cfg.get("soh_metadata") or {}))
        if over:
            a.note("adaptations", f"soh metadata for the runner: {', '.join(over)}")
        # soh records an error for a custom reachability test whose source or
        # target does not boot (no C2 on the source, no address for the target),
        # while it skips the other checks of such nodes, so drop those tests too.
        off = {i["hostname"] for i in a.rec["nodes"] if not i["boot"] and i["why_off"] != "external node"}
        tests = md.get("testCustomReachability") or []
        keep = [t for t in tests if not {t.get("src"), str(t.get("dst", "")).split("|")[0]} & off]
        if len(keep) < len(tests):
            md["testCustomReachability"] = keep
            a.note("adaptations", f"soh: {len(tests) - len(keep)} of {len(tests)} custom reachability tests "
                   "dropped, their source or target stays off: " + ", ".join(
                       f"{t.get('src')} to {t.get('dst')} {t.get('proto')}/{t.get('port')}" for t in tests if t not in keep))
        # A booted node's custom test can need nodes that stay off as well (an
        # IPsec peer, the routers a route comes through); soh_needs names them.
        custom, gone = md.get("hostCustomTests") or {}, []
        for host, needs in (a.run.cfg.get("soh_needs") or {}).items():
            for name, pat in needs.items():
                need = sorted(h for h in off if re.search(pat, h))
                if need and host not in off and any(t.get("name") == name for t in custom.get(host) or []):
                    custom[host] = [t for t in custom[host] if t.get("name") != name]
                    gone.append(f"{host} {name} (needs {', '.join(need)})")
        if gone:
            a.note("adaptations", f"soh: {len(gone)} custom tests dropped, nodes they need stay off: {'; '.join(gone)}")
        # The phenix this job runs still launches a packet capture VM for a
        # captured node that does not boot, and then fails the capture.
        caps = (md.get("packetCapture") or {}).get("captureHosts") or {}
        gone = sorted(h for h in caps if h in off)
        for h in gone:
            del caps[h]
        if gone:
            a.note("adaptations", f"soh: packet capture of {', '.join(gone)} dropped, they stay off")


def attempt(run, label, fixes, patches=()):
    a = Attempt(run, label)
    cfg = run.cfg
    exp = cfg["experiment"]
    work = WORK / run.name / str(len(run.res["attempts"]))
    work.mkdir(parents=True, exist_ok=True)
    for old, new in fixes.items():
        a.note("adaptations", f"fix: {old!r} -> {new!r} in every config")
    if patches and not patch_apps(a, patches):
        return a.finish("patches do not apply")

    # --- prepare
    t0 = time.time()
    try:
        docs = load(cfg, fixes)
    except yaml.YAMLError as e:
        a.stage("prepare", False, f"YAML does not parse: {e}")
        return a.finish("configs do not parse")
    topo = next(d for ds in docs.values() for d in ds if d.get("kind") == "Topology")
    scen = next((d for d in docs.get(cfg.get("scenario"), []) if d.get("kind") == "Scenario"), None)

    for s in sorted({s for ds in docs.values() for d in ds for s in host_paths(d.get("spec"))}):
        if "{" in s:
            continue
        p = Path(s)
        if not p.exists():
            a.note("findings", f"{s} does not exist")
        elif p.is_file() and p.open("rb").read(len(LFS_POINTER)) == LFS_POINTER:
            a.note("findings", f"{s} is a Git LFS pointer, not the file")

    lost = sorted(f"{i}.qc2" for i in cfg.get("images") or [] if not (IMAGES / f"{i}.qc2").exists())
    if lost:
        a.stage("prepare", False, f"{', '.join(lost)}, built by this workflow, did not reach the job; see its Image job")
        return a.finish("harness error: image missing")
    origins = [json.loads((IMAGES / f"{i}.source.json").read_text()) for i in cfg.get("images") or []
               if (IMAGES / f"{i}.source.json").exists()]
    if origins:
        a.note("adaptations", "images: " + "; ".join(
            f"{x['ref']} ({x['digest'][:19]})" + (f" with this job's {x['injected']}" if x.get("injected") else "")
            if x["source"] == "released" else f"built from {x['ref']}" for x in origins))
    failed_apps = APPS_FAILED.read_text().split() if APPS_FAILED.exists() else []
    if failed_apps and not patches:
        a.stage("prepare", False, f"the apps this repository ships did not install: {', '.join(failed_apps)}")
        return a.finish("apps do not install")

    aliases = cfg.get("aliases", {})
    booted, mem_total = [], 0
    for n in topo["spec"]["nodes"]:
        g = n.setdefault("general", {}) or {}
        n["general"] = g
        hw = n.setdefault("hardware", {}) or {}
        n["hardware"] = hw
        images = [d.get("image", "") for d in hw.get("drives") or []]
        info = {"hostname": g.get("hostname"), "type": n.get("type"), "os": hw.get("os_type"), "images": images,
                "memory": int(hw.get("memory") or 512), "vcpus": int(hw.get("vcpus") or 1), "boot": True, "why_off": ""}
        for pat, mb in (cfg.get("memory") or {}).items():
            if re.search(pat, info["hostname"] or ""):
                info["memory_set"] = hw["memory"] = info["memory"] = int(mb)
        if g.get("do_not_boot"):
            info["boot"], info["why_off"] = False, "do_not_boot in the topology"
        if n.get("external"):
            info["boot"], info["why_off"] = False, "external node"
        for pat, why in (cfg.get("keep_off") or {}).items():
            if info["boot"] and re.search(pat, info["hostname"] or ""):
                g["do_not_boot"] = True
                info["boot"], info["why_off"] = False, f"kept off: {why}"
        for img in images:
            if (IMAGES / img).exists():
                continue
            if img in aliases and (IMAGES / aliases[img]).exists():
                if not (IMAGES / img).is_symlink():
                    os.symlink(IMAGES / aliases[img], IMAGES / img)
                a.note("adaptations", f"image {img}: no recipe builds it; {aliases[img]} stands in")
                continue
            if info["boot"]:
                g["do_not_boot"] = True
                info["boot"], info["why_off"] = False, f"no recipe builds {img}"
        if info["boot"]:
            booted.append((n, info))
            mem_total += info["memory"]
        a.rec["nodes"].append(info)
    kept = {}
    for i in a.rec["nodes"]:
        if i["why_off"].startswith("kept off: "):
            kept.setdefault(i["why_off"][10:], []).append(i["hostname"])
    for why, hosts in kept.items():
        a.note("adaptations", f"{len(hosts)} nodes kept off for the runner: {', '.join(hosts)} ({why})")
    sized = [f"{i['hostname']}={i['memory_set']} MB" for i in a.rec["nodes"] if i.get("memory_set") and i["boot"]]
    if sized:
        a.note("adaptations", f"memory set before scaling: {', '.join(sized)}")
    off = [i for i in a.rec["nodes"] if i["why_off"].startswith("no recipe")]
    if off:
        imgs = sorted({x for i in off for x in i["images"] if not (IMAGES / x).exists()})
        a.note("adaptations", f"{len(off)} of {len(a.rec['nodes'])} nodes stay off: no recipe builds {', '.join(imgs)}")
    topo_images = {x for i in a.rec["nodes"] for x in i["images"]}
    scen_missing = sorted({x for rel in docs if rel != cfg["topology"] for d in docs[rel] for x in named_images(d.get("spec"))
                           if x not in topo_images and not (IMAGES / x).exists()})
    if scen_missing:
        a.note("adaptations", f"the scenario's apps name images no recipe builds: {', '.join(scen_missing)}")
    if not booted:
        a.stage("prepare", False, "no node can boot: every node's image is missing or the node is off")
        return a.finish("nothing to boot")
    budget = int(cfg.get("memory_budget", 12288))
    if mem_total > budget:
        # Every guest first gets its minimum: 512 MB, or the floor
        # memory_floor names, but never more than it asked for. The rest of
        # the budget is shared in proportion to what each asked above that
        # minimum, rounded down, so the total never exceeds the budget.
        floors, lows = {}, []
        for _, info in booted:
            f = max([int(mb) for pat, mb in (cfg.get("memory_floor") or {}).items()
                     if re.search(pat, info["hostname"] or "")] or [0])
            if f:
                floors[info["hostname"]] = f
            lows.append(min(info["memory"], max(512, f)))
        spare = budget - sum(lows)
        if spare < 0:
            a.stage("prepare", False, f"the booted guests need at least {sum(lows)} MB with their floors; "
                    f"memory_budget is {budget} MB")
            return a.finish("harness error: memory budget too small")
        above = mem_total - sum(lows)  # > spare >= 0, as mem_total > budget
        for (n, info), low in zip(booted, lows):
            info["memory_ci"] = low + int((info["memory"] - low) * spare / above) // 256 * 256
            n["hardware"]["memory"] = info["memory_ci"]
        scaled = sum(i["memory_ci"] for _, i in booted)
        a.note("adaptations", f"booted guests ask for {mem_total} MB; scaled to {scaled} MB "
               f"(memory_budget {budget} MB) for the runner"
               + (f", keeping floors {', '.join(f'{h}={mb} MB' for h, mb in floors.items())}" if floors else ""))
    big = [i for _, i in booted if i["vcpus"] > 2]
    for n, info in booted:
        if info["vcpus"] > 2:
            n["hardware"]["vcpus"] = info["vcpus_ci"] = 2
    if big:
        a.note("adaptations", f"{len(big)} guests with more than 2 vCPUs get 2 on the 4-vCPU runner")
    fit_soh(a, docs, scen)
    files = []
    for rel, ds in docs.items():
        # Several topologies keep configs of the same file name in different
        # directories (base/scenario.yml, nids/scenario.yml).
        f = work / rel.replace("/", "_")
        f.write_text(yaml.safe_dump_all(ds, sort_keys=False))
        files.append(str(f))
    a.rec["booted"] = len(booted)
    a.stage("prepare", True, f"{len(a.rec['nodes'])} nodes, {len(booted)} to boot", round(time.time() - t0))

    # --- configs and create
    for ds in docs.values():
        for d in ds:
            phenix("config", "delete", f"{d['kind'].lower()}/{d['metadata']['name']}", timeout=120)
    phenix("experiment", "stop", exp, timeout=300)
    phenix("experiment", "delete", exp, timeout=300)
    ok, out, s = phenix("config", "create", *files, timeout=300)
    if not a.stage("configs", ok, out, s):
        return a.finish("configs refused")
    args = ["experiment", "create", exp, "-t", topo["metadata"]["name"]]
    if scen:
        args += ["-s", scen["metadata"]["name"]]
    ok, out, s = phenix(*args, timeout=900)
    if not a.stage("create", ok, out, s):
        return a.finish("create failed")

    # --- start
    ok, out, s = phenix("experiment", "start", exp, timeout=int(cfg.get("start_timeout", 2700)))
    if not a.stage("start", ok, out, s):
        phenix("experiment", "stop", exp, timeout=900)
        return a.finish("start failed")

    # --- running
    # Apps can add topology nodes at configure (soh's Elastic server) and launch
    # VMs of their own at post-start (soh's packet capture monitors).
    started = phenix_json("config", "get", f"experiment/{exp}")
    known = {i["hostname"] for i in a.rec["nodes"]}
    for n in ((started.get("spec") or {}).get("topology") or {}).get("nodes") or []:
        g, hw = n.get("general") or {}, n.get("hardware") or {}
        if g.get("hostname") in known:
            continue
        images = [d.get("image", "") for d in hw.get("drives") or []]
        lacking = [x for x in images if x and not (IMAGES / x).exists()]
        info = {"hostname": g.get("hostname"), "type": n.get("type"), "os": hw.get("os_type"), "images": images,
                "memory": int(hw.get("memory") or 512), "vcpus": int(hw.get("vcpus") or 1), "origin": "app",
                "boot": not g.get("do_not_boot"), "why_off": ""}
        if not info["boot"]:
            info["why_off"] = f"no recipe builds {', '.join(lacking)}" if lacking else "do_not_boot set by an app"
        a.rec["nodes"].append(info)
        known.add(info["hostname"])
        if info["boot"]:
            booted.append((n, info))
    want = {i["hostname"] for _, i in booted}
    t0, state, disks, errs = time.time(), {}, {}, []
    while time.time() - t0 < 300:
        rows, errs = mm(".columns name,state,disks vm info", exp)
        state = {r.get("name"): r.get("state") for r in rows}
        disks = {r.get("name"): first_disk(r.get("disks")) for r in rows}
        if all(state.get(h) == "RUNNING" for h in want) and "BUILDING" not in state.values():
            break
        time.sleep(5)
    for _, i in booted:
        i["state"] = state.get(i["hostname"], "absent")
    ran = dict(state)
    extra = sorted(set(state) - known)
    for h in extra:
        a.rec["nodes"].append({"hostname": h, "type": "VirtualMachine", "os": "", "images": [], "memory": 0, "vcpus": 0,
                               "origin": "app", "boot": True, "why_off": "", "state": state[h]})
    bad = sorted(h for h in want if state.get(h) != "RUNNING")
    extra_bad = [h for h in extra if state[h] != "RUNNING"]
    sources = snapshot_sources((started.get("spec") or {}).get("baseDir"))
    diskless = [h for h in extra_bad if disks.get(h) and not Path(disks[h]).exists()
                and Path(disks[h]).name in sources and not (IMAGES / sources[Path(disks[h]).name]).exists()]
    for i in a.rec["nodes"]:
        if i["hostname"] in diskless:
            i["why_off"] = f"no disk: a snapshot of {sources[Path(disks[i['hostname']]).name]}, which no recipe builds"
    if diskless:
        a.note("findings", f"{len(diskless)} VMs that apps launched are not running: their disks are snapshots of "
               f"{', '.join(sorted({sources[Path(disks[h]).name] for h in diskless}))}, which no recipe builds "
               f"({', '.join(f'{h}={state[h]}' for h in diskless)})")
    bad += [h for h in extra_bad if h not in diskless]
    a.stage("running", not bad, f"{len(want) - len([h for h in bad if h in want])}/{len(want)} running"
            + (f", {len(extra)} more VMs from apps" if extra else "")
            + (f"; not running: {', '.join(f'{h}={state.get(h, 'absent')}' for h in bad)}" if bad else "")
            + (f"; minimega: {'; '.join(errs)}" if bad and errs else ""),
            round(time.time() - t0))

    # --- c2
    c2_os = cfg.get("c2_os", ["linux", "minirouter"])
    c2_want = {i["hostname"] for _, i in booted if (i["os"] or "linux").lower() in c2_os} - set(cfg.get("no_c2", []))
    t0, seen = time.time(), set()
    while c2_want and time.time() - t0 < int(cfg.get("c2_timeout", 900)):
        rows, _ = mm(".columns hostname cc clients", exp)
        seen = {r.get("hostname") for r in rows}
        if c2_want <= seen:
            break
        time.sleep(10)
    for _, i in booted:
        i["c2"] = (i["hostname"] in seen) if i["hostname"] in c2_want else None
    missing = sorted(c2_want - seen)
    a.stage("c2", not missing, f"{len(c2_want) - len(missing)}/{len(c2_want)} registered"
            + (f"; missing: {', '.join(missing)}" if missing else ""), round(time.time() - t0))

    # --- steady
    time.sleep(60)
    rows, _ = mm(".columns name,state vm info", exp)
    state = {r.get("name"): r.get("state") for r in rows}
    up = want | {h for h in extra if ran[h] == "RUNNING"}
    dropped = sorted(h for h in up if state.get(h) != "RUNNING")
    a.stage("steady", not dropped, "all still running" if not dropped else f"not running: {', '.join(dropped)}", 60)

    # --- checks
    if cfg.get("checks"):
        t0, results = time.time(), []
        for c in cfg["checks"]:
            hosts = sorted(i["hostname"] for _, i in booted if re.search(c["nodes"], i["hostname"]))
            if not hosts:
                results.append({"check": c["name"], "node": c["nodes"], "want": c["want"], "got": "(no booted node matches)", "ok": False})
            for h in hosts:
                got = guest_run(exp, h, c["script"], int(c.get("wait", 240))) if h in seen else None
                got = got if got is not None else "(not on C2)" if h not in seen else "(no answer)"
                results.append({"check": c["name"], "node": h, "want": c["want"], "got": got, "ok": got == c["want"]})
        a.rec["checks"] = results
        bad = [r for r in results if not r["ok"]]
        a.stage("checks", not bad, f"{len(results) - len(bad)}/{len(results)} passed"
                + ("; " + "; ".join(f"{r['check']} on {r['node']}: got {r['got']!r}" for r in bad) if bad else ""),
                round(time.time() - t0))
    a.rec["app_status"] = (phenix_json("config", "get", f"experiment/{exp}").get("status") or {}).get("apps")

    # --- soh: its checks run in its post-start stage, so they are done by now.
    if scen and any(x.get("name") == "soh" for x in (scen.get("spec") or {}).get("apps") or []):
        soh = (a.rec.get("app_status") or {}).get("soh") or {}
        # soh stores a reachability check under both of its hosts; count each once.
        failed = sorted({(k, json.dumps(e.get("metadata") or {"host": h.get("hostname")}, sort_keys=True), e["error"])
                         for h in soh.get("hosts") or [] for k, v in h.items()
                         if isinstance(v, list) for e in v if isinstance(e, dict) and e.get("error")})
        passed = sum(1 for h in soh.get("hosts") or [] for v in h.values() if isinstance(v, list)
                     for e in v if isinstance(e, dict) and not e.get("error"))
        timing = [c for c in failed if re.search(r"c2 (client )?not active", c[2], re.I)]
        listed = [f"{k} {' '.join(f'{x}={y}' for x, y in json.loads(m).items())}: {e}" for k, m, e in failed]
        a.rec["soh"] = {"initialized": bool(soh.get("initialized")), "passed": passed, "failed": listed}
        a.stage("soh", bool(soh.get("initialized")) and not failed,
                ("not initialized; " if not soh.get("initialized") else "")
                + f"{passed} checks passed, {len(failed)} failed"
                + (f" ({len(timing)} before the guest's C2 was active)" if timing else "")
                + ("; " + "; ".join(listed)[:1500] if listed else ""), 0)

    # --- soh-flows: soh's running stage queries the flows its packet capture
    # collected; the checks before it made traffic between captured hosts.
    if cfg.get("soh_flows"):
        ok, out, s = phenix("experiment", "trigger", "running", exp, "soh", timeout=1800)
        pc = ((phenix_json("config", "get", f"experiment/{exp}").get("status") or {}).get("apps") or {}).get("soh") or {}
        pc = pc.get("packetCapture") or {}
        hosts = pc.get("hosts") or []
        seen = set(hosts) if isinstance(hosts, list) else set(hosts or {})
        missing = [ip for ip in cfg["soh_flows"] if ip not in seen]
        a.rec["soh_flows"] = {"hosts": sorted(seen), "missing": missing}
        a.stage("soh-flows", ok and not missing,
                (f"flows from {', '.join(sorted(seen))}" if seen else "no flows")
                + (f"; missing {', '.join(missing)}" if missing else "")
                + ("" if ok else f"; trigger failed: {out[-600:]}"), s)

    # --- stop
    ok, out, s = phenix("experiment", "stop", exp, timeout=900)
    a.stage("stop", ok, out, s)
    failed = [st["stage"] for st in a.rec["stages"] if not st["ok"]]
    return a.finish("passed" if not failed else "failed: " + ", ".join(failed))


class Cancelled(BaseException):
    """The job was cancelled or timed out: the runner sent SIGINT, then SIGTERM."""


def cancel(signum, _frame):
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, signal.SIG_IGN)
    raise Cancelled(signal.Signals(signum).name)


def guarded(run, label, fixes, patches=()):
    """Run an attempt; a cancel or a fault in the harness ends it as such, not as "running"."""
    try:
        return attempt(run, label, fixes, patches)
    except Cancelled as e:
        # The runner kills the step 10 s after its first signal: save first.
        rec = run.res["attempts"][-1]
        rec["stages"].append({"stage": "cancelled", "ok": False, "detail": f"{e} during the attempt", "seconds": None})
        rec["result"] = f"cancelled ({e})"
        run.res[run.result_key(rec)] = rec["result"]
        run.save()
        phenix("experiment", "stop", run.cfg["experiment"], timeout=5)
        raise SystemExit(130) from e
    except BaseException as e:
        rec = run.res["attempts"][-1]
        rec["stages"].append({"stage": "harness", "ok": False, "detail": traceback.format_exc()[-4000:], "seconds": None})
        rec["result"] = f"harness error: {type(e).__name__}: {e}"[:300]
        run.res[run.result_key(rec)] = rec["result"]
        run.save()
        phenix("experiment", "stop", run.cfg["experiment"], timeout=300)
        raise SystemExit(2) from e


def main():
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, cancel)
    run = Run(sys.argv[1], sys.argv[2])
    if guarded(run, "as committed", {}):
        return 0
    first = run.res["attempts"][0]
    loaded = any(st["stage"] == "create" and st["ok"] for st in first["stages"])
    if not loaded and not first["result"].startswith("harness error") and (run.cfg.get("fixes") or run.cfg.get("patches")):
        guarded(run, "with fixes", run.cfg.get("fixes") or {}, run.cfg.get("patches") or [])
    return 1


if __name__ == "__main__":
    sys.exit(main())
