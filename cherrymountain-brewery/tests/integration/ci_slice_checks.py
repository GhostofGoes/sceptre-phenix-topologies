#!/usr/bin/env python3
"""Assertions for ``ci_slice_docker.sh`` (the cellar slice over Modbus and REST).

Phases (run by the shell script in this order):

``baseline``
    The real plant model is running. ``CEL.FV101.TT01.PV`` flows ot-sim input
    register 30002 -> plc-cellar ``%IW101`` -> FUXA tag, and the value changes
    over time. Every analog PV in the PLC image, every FUXA tag, the spunding
    outputs (``%QW100..103`` -> ot-sim holding 40001..40004), the setpoint words
    (``%MW0..7``), and the alarm bits are read at their generated addresses.
``control``
    A setpoint written through FUXA (``%MW0``) and one written by Modbus
    (``%MW1``) land in the right words and open the glycol valves; raising
    them closes the valves again.
``fault``
    The plant model is replaced by ``fault_federate.py`` (FV-101 head pressure
    forced to 27.0 psia): after the 30 s on-delay the PLC raises
    ``CEL.FV101.PT01.ALM_HIGH`` and FUXA shows it.
``layout-probe``
    A ZIP whose ``modbus_slave.json`` is not tight (the v3 map, or a small
    ``qw_count``) is running: the runtime clamps or shifts the segments exactly
    as plantgen's rules say.

Prints every asserted value; exits non-zero if any assertion fails.

Usage (all paths are repo files, addresses are container IPs):
    python ci_slice_checks.py PHASE --rio IP --plc IP --fuxa URL \\
        --plc-container NAME --registers registers.json --project project.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
from pydantic import BaseModel
from pymodbus.client import ModbusTcpClient

UNIT: int = 1
#: Seconds between samples; ot-sim publishes at 1 Hz and the PLC polls at 10 Hz.
SAMPLE_S: float = 0.25
#: Setpoint defaults the PLC loads on its first scan (degF x 100).
DEFAULT_SP: list[int] = [6600, 6600, 6600, 6600, 6800, 6800, 5000, 5000]
#: Spunding setpoints the PLC writes (psia x 100): psig + 12.1 ambient.
SPUND_RAW: list[int] = [1310, 1310, 2410, 2410]


class Row(BaseModel):
    """The columns of a registers.json row the checks use."""

    tag: str
    type: str
    scaling: int | None
    rio_space: str | None
    otsim_register: int | None
    plc_iec: str
    slave_space: str
    slave_pdu_address: int
    scada_access: str


class Ctx(BaseModel):
    """Everything a phase needs."""

    rio: str
    plc: str
    fuxa: str
    plc_container: str
    rows: dict[str, Row]
    layout: dict[str, dict[str, int]]
    fuxa_ids: dict[str, str]
    fuxa_user: str
    fuxa_password: str


class Results:
    """Collects PASS/FAIL lines."""

    def __init__(self) -> None:
        self.failed: list[str] = []

    def check(self, ok: bool, name: str, detail: str) -> bool:
        print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
        if not ok:
            self.failed.append(name)
        return ok


class Modbus:
    """A thin pymodbus client that returns lists or the exception code."""

    def __init__(self, host: str) -> None:
        self.client: ModbusTcpClient = ModbusTcpClient(host, port=502, timeout=3)
        if not self.client.connect():
            raise SystemExit(f"cannot connect to Modbus {host}:502")

    def read(self, space: str, address: int, count: int = 1) -> list[int] | int:
        """Registers (ints) or bits (0/1), else the Modbus exception code."""
        fn: dict[str, Callable[..., Any]] = {
            "coil": self.client.read_coils,
            "discrete": self.client.read_discrete_inputs,
            "holding": self.client.read_holding_registers,
            "input": self.client.read_input_registers,
        }
        rr: Any = fn[space](address, count=count, device_id=UNIT)
        if rr.isError():
            return int(getattr(rr, "exception_code", -1))
        if space in ("coil", "discrete"):
            return [int(b) for b in rr.bits[:count]]
        return [int(v) for v in rr.registers]

    def ints(self, space: str, address: int, count: int = 1) -> list[int]:
        got: list[int] | int = self.read(space, address, count)
        if isinstance(got, int):
            raise RuntimeError(f"{space} {address}+{count}: exception {got}")
        return got

    def write_register(self, address: int, value: int) -> None:
        rr: Any = self.client.write_register(address, value, device_id=UNIT)
        if rr.isError():
            raise RuntimeError(f"write_register {address}: {rr}")


class Fuxa:
    """FUXA's REST tag API (admin token in ``x-access-token``)."""

    def __init__(self, ctx: Ctx) -> None:
        self.base: str = ctx.fuxa
        self.ids: dict[str, str] = ctx.fuxa_ids
        r: requests.Response = requests.post(
            f"{self.base}/api/signin",
            json={"username": ctx.fuxa_user, "password": ctx.fuxa_password},
            timeout=10,
        )
        r.raise_for_status()
        self.headers: dict[str, str] = {"x-access-token": r.json()["data"]["token"]}

    def get(self, names: list[str]) -> dict[str, float | None]:
        r: requests.Response = requests.get(
            f"{self.base}/api/getTagValue",
            params={"ids": json.dumps([self.ids[n] for n in names])},
            headers=self.headers,
            timeout=10,
        )
        r.raise_for_status()
        return {n: item.get("value") for n, item in zip(names, r.json(), strict=True)}

    def set(self, name: str, value: float) -> None:
        r: requests.Response = requests.post(
            f"{self.base}/api/setTagValue",
            json={"tags": [{"id": self.ids[name], "value": value}]},
            headers=self.headers,
            timeout=10,
        )
        r.raise_for_status()


def wait_until(pred: Callable[[], bool], timeout_s: float, step_s: float = 0.5) -> bool:
    """Poll ``pred`` until true or ``timeout_s`` elapses."""
    end: float = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step_s)
    return pred()


def load(args: argparse.Namespace) -> Ctx:
    doc: dict[str, Any] = json.loads(Path(args.registers).read_text())
    rows: dict[str, Row] = {
        r["tag"]: Row(**{k: r[k] for k in Row.model_fields}) for r in doc["registers"]
    }
    project: dict[str, Any] = json.loads(Path(args.project).read_text())
    ids: dict[str, str] = {
        t["name"]: t["id"] for d in project["devices"].values() for t in d.get("tags", {}).values()
    }
    return Ctx(
        rio=args.rio,
        plc=args.plc,
        fuxa=args.fuxa,
        plc_container=args.plc_container,
        rows=rows,
        layout=doc["slave_layouts"]["plc-cellar"],
        fuxa_ids=ids,
        fuxa_user=args.fuxa_user,
        fuxa_password=args.fuxa_password,
    )


def scale(row: Row) -> int:
    return 10 ** (row.scaling or 0)


def plc_logs(ctx: Ctx) -> str:
    out: subprocess.CompletedProcess[str] = subprocess.run(
        ["docker", "logs", ctx.plc_container], capture_output=True, text=True, check=False
    )
    return out.stdout + out.stderr


def current_logs(ctx: Ctx) -> str:
    """The PLC log since the last time ``modbus_slave`` was configured.

    The container's log accumulates over uploads; the checks are about the
    program that is running now.
    """
    logs: str = plc_logs(ctx)
    start: int = logs.rfind("[MODBUS_SLAVE] Configuration loaded")
    return logs[start:] if start >= 0 else logs


def segment_line(layout: dict[str, dict[str, int]]) -> str:
    """The runtime's startup line for a holding-register layout."""
    hr: dict[str, int] = layout["holding_registers"]
    qw, mw = hr["qw_count"], hr["mw_count"]
    return f"%QW=0-{qw - 1}, %MW={qw}-{qw + mw - 1}"


def last_segment_line(ctx: Ctx) -> str:
    lines: list[str] = [
        ln for ln in current_logs(ctx).splitlines() if "Segmented holding registers" in ln
    ]
    return lines[-1] if lines else ""


def alarm_rows(ctx: Ctx) -> list[Row]:
    return [r for r in ctx.rows.values() if ".ALM_" in r.tag]


# --------------------------------------------------------------------------- #


def baseline(ctx: Ctx, res: Results) -> None:
    rio, plc, fuxa = Modbus(ctx.rio), Modbus(ctx.plc), Fuxa(ctx)
    hr: dict[str, int] = ctx.layout["holding_registers"]
    mw0: int = hr["qw_count"]

    # The runtime exposes the segments plantgen asked for, clamped by nothing.
    line: str = last_segment_line(ctx)
    res.check(
        segment_line(ctx.layout) in line, "runtime layout", line.split("] ", 2)[-1] or "no line"
    )
    res.check(
        "exposure reduced" not in current_logs(ctx),
        "runtime clamps nothing",
        "no 'Modbus exposure reduced' warning in the PLC log",
    )

    # -- flow: ot-sim -> PLC -> FUXA, sampled for 9 s ---------------------------------
    pvs: list[Row] = [r for r in ctx.rows.values() if r.rio_space == "input"]
    e2e: Row = ctx.rows["CEL.FV101.TT01.PV"]
    o_seen: dict[str, set[int]] = {r.tag: set() for r in pvs}
    p_seen: dict[str, set[int]] = {r.tag: set() for r in pvs}
    p_late: dict[str, set[int]] = {r.tag: set() for r in pvs}
    f_late: dict[str, set[int]] = {r.tag: set() for r in pvs}
    f_series: list[float] = []
    t0: float = time.monotonic()
    while time.monotonic() - t0 < 9.0:
        elapsed: float = time.monotonic() - t0
        first: int = min(r.otsim_register or 0 for r in pvs)
        o_vals: list[int] = rio.ints("input", first, len(pvs))
        plc0: int = min(r.slave_pdu_address for r in pvs)
        p_vals: list[int] = plc.ints("input", plc0, len(pvs))
        f_vals: dict[str, float | None] = fuxa.get([r.tag for r in pvs])
        for r in pvs:
            o_seen[r.tag].add(o_vals[(r.otsim_register or 0) - first])
            p: int = p_vals[r.slave_pdu_address - plc0]
            p_seen[r.tag].add(p)
            f: float | None = f_vals[r.tag]
            if elapsed >= 3.0:
                p_late[r.tag].add(p)
                if f is not None:
                    f_late[r.tag].add(round(f * scale(r)))
        f_e2e: float | None = f_vals[e2e.tag]
        if f_e2e is not None:
            f_series.append(f_e2e)
        time.sleep(SAMPLE_S)

    bad_plc: list[str] = [
        t for t in o_seen if not p_late[t] <= o_seen[t] | p_seen[t] or not p_late[t] & o_seen[t]
    ]
    res.check(
        not bad_plc,
        "ot-sim -> plc-cellar, all 17 input registers",
        f"every %IW100..116 value over {len(pvs)} tags was served by ot-sim IR30001..30017"
        if not bad_plc
        else f"mismatch on {bad_plc}",
    )
    bad_fuxa: list[str] = [
        t for t in o_seen if not f_late[t] <= (o_seen[t] | p_seen[t]) or not f_late[t]
    ]
    res.check(
        not bad_fuxa,
        "plc-cellar -> FUXA, all PV tags",
        f"{len(pvs)} FUXA PV tags equal the PLC image (raw / divisor)"
        if not bad_fuxa
        else f"mismatch on {bad_fuxa}",
    )
    distinct: int = len(set(f_series))
    res.check(
        distinct >= 2,
        "e2e CEL.FV101.TT01.PV changes over time",
        f"ot-sim IR{e2e.otsim_register}={sorted(o_seen[e2e.tag])} -> plc %IW101=IR{e2e.slave_pdu_address}"
        f"={sorted(p_seen[e2e.tag])} -> FUXA {sorted(set(f_series))} ({len(f_series)} samples, {distinct} distinct)",
    )

    # -- discrete inputs: spunding valve open flags ----------------------------------
    zso: list[Row] = [r for r in ctx.rows.values() if r.tag.endswith(".ZSO01.PV")]
    o_di: list[int] = [rio.ints("discrete", r.otsim_register or 0)[0] for r in zso]
    p_di: list[int] = [plc.ints("discrete", r.slave_pdu_address)[0] for r in zso]
    res.check(
        o_di == p_di, "discrete inputs %IX100.0..3", f"ot-sim DI10001..={o_di} plc DI800..={p_di}"
    )

    # -- spunding outputs: %QW100..103 -> modbus_master -> ot-sim holding ---------------
    sp_rows: list[Row] = sorted(
        (r for r in ctx.rows.values() if r.tag.endswith(".PCV01.SP")),
        key=lambda r: r.otsim_register or 0,
    )
    q0: int = sp_rows[0].slave_pdu_address
    plc_q: list[int] = plc.ints("holding", q0, len(sp_rows))
    ot_q: list[int] = rio.ints("holding", sp_rows[0].otsim_register or 0, len(sp_rows))
    fx_q: dict[str, float | None] = fuxa.get([r.tag for r in sp_rows])
    res.check(
        plc_q == SPUND_RAW and ot_q == SPUND_RAW,
        "spunding %QW100..103 reach ot-sim holding 40001..40004",
        f"plc HR{q0}..={plc_q} ot-sim HR40001..={ot_q} FUXA={[fx_q[r.tag] for r in sp_rows]}",
    )
    res.check(
        [round((fx_q[r.tag] or 0) * 100) for r in sp_rows] == SPUND_RAW,
        "FUXA spunding tags",
        f"{[fx_q[r.tag] for r in sp_rows]} psia",
    )

    # -- setpoint words %MW0..7 ------------------------------------------------------------
    sps: list[Row] = sorted(
        (r for r in ctx.rows.values() if r.plc_iec.startswith("%MW")),
        key=lambda r: r.slave_pdu_address,
    )
    mw: list[int] = plc.ints("holding", mw0, len(sps))
    res.check(
        mw == DEFAULT_SP and [r.slave_pdu_address for r in sps] == list(range(mw0, mw0 + 8)),
        f"setpoints %MW0..7 at holding {mw0}..{mw0 + 7}",
        f"plc HR{mw0}..{mw0 + 7}={mw}",
    )
    fx_sp: dict[str, float | None] = fuxa.get([r.tag for r in sps])
    res.check(
        [round((fx_sp[r.tag] or 0) * 100) for r in sps] == DEFAULT_SP,
        "FUXA setpoint tags",
        f"{[fx_sp[r.tag] for r in sps]} degF",
    )
    edge: list[int] | int = plc.read("holding", mw0 + len(sps))
    res.check(
        edge == 2,
        "holding past %MW7 is Illegal Data Address",
        f"HR{mw0 + len(sps)} -> exception {edge}",
    )
    old: list[int] | int = plc.read("holding", 1024)
    res.check(old == 2, "the v3 address (1024) is not %MW0", f"HR1024 -> exception {old}")

    # -- alarm bits --------------------------------------------------------------------------
    alarms: list[Row] = alarm_rows(ctx)
    a_pdu: list[int] = [r.slave_pdu_address for r in alarms]
    bits: list[int] = plc.ints("coil", min(a_pdu), max(a_pdu) - min(a_pdu) + 1)
    fx_a: dict[str, float | None] = fuxa.get([r.tag for r in alarms])
    res.check(
        len(alarms) == 20 and sum(bits) == 0 and all(not v for v in fx_a.values()),
        f"{len(alarms)} alarm bits %QX0.0..2.3 read clear",
        f"plc coils {min(a_pdu)}..{max(a_pdu)} sum={sum(bits)}; FUXA alarm tags all 0",
    )
    beyond: list[int] | int = plc.read("coil", ctx.layout["coils"]["qx_bits"])
    res.check(
        beyond == 2,
        "coil past %QX100.7 is Illegal Data Address",
        f"coil {ctx.layout['coils']['qx_bits']} -> exception {beyond}",
    )

    # -- valve command coils equal ot-sim coils ------------------------------------------------
    valves: list[Row] = sorted(
        (r for r in ctx.rows.values() if r.tag.endswith((".TV01.CMD", ".TV02.CMD"))),
        key=lambda r: r.otsim_register or 0,
    )
    v_plc: list[int] = plc.ints("coil", valves[0].slave_pdu_address, len(valves))
    v_rio: list[int] = rio.ints("coil", valves[0].otsim_register or 0, len(valves))
    res.check(
        v_plc == v_rio,
        "valve commands %QX100.0..7 reach ot-sim coils 1..8",
        f"plc coil 800..={v_plc} ot-sim coil 1..={v_rio}",
    )


def control(ctx: Ctx, res: Results) -> None:
    rio, plc, fuxa = Modbus(ctx.rio), Modbus(ctx.plc), Fuxa(ctx)
    qw: int = ctx.layout["holding_registers"]["qw_count"]
    sp0, sp1 = ctx.rows["CEL.FV101.TT01.SP"], ctx.rows["CEL.FV101.TT02.SP"]
    v0, v1 = ctx.rows["CEL.FV101.TV01.CMD"], ctx.rows["CEL.FV101.TV02.CMD"]
    pv0 = ctx.rows["CEL.FV101.TT01.PV"]
    res.check(
        sp0.slave_pdu_address == qw and sp1.slave_pdu_address == qw + 1,
        "setpoint addresses",
        f"MW0=HR{sp0.slave_pdu_address} MW1=HR{sp1.slave_pdu_address}",
    )

    def coil(row: Row) -> int:
        return plc.ints("coil", row.slave_pdu_address)[0]

    def rio_coil(row: Row) -> int:
        return rio.ints("coil", row.otsim_register or 0)[0]

    pv: int = plc.ints("input", pv0.slave_pdu_address)[0]
    res.check(
        coil(v0) == 0 and coil(v1) == 0,
        "valves start closed",
        f"PV={pv / 100:.2f} degF, SP=66.00, coil800={coil(v0)} coil801={coil(v1)}",
    )

    # 1. Setpoint through FUXA -> %MW0 -> valve opens (PV >= SP + 0.5).
    fuxa.set(sp0.tag, 60.0)
    ok: bool = wait_until(lambda: plc.ints("holding", sp0.slave_pdu_address)[0] == 6000, 10)
    words: list[int] = plc.ints("holding", qw - 1, 4)
    res.check(
        ok and words == [2410, 6000, 6600, 6600],
        "FUXA write 60.0 lands in %MW0 only",
        f"plc HR{qw - 1}..{qw + 2}={words} (%QW103, %MW0, %MW1, %MW2)",
    )
    ok = wait_until(lambda: coil(v0) == 1 and rio_coil(v0) == 1, 15)
    res.check(
        ok and coil(v1) == 0,
        "setpoint 60.0 opens FV-101 side valve",
        f"plc coil800={coil(v0)} ot-sim coil1={rio_coil(v0)}; cone coil801={coil(v1)}",
    )

    # 2. Setpoint by Modbus write -> %MW1 -> cone valve opens.
    plc.write_register(sp1.slave_pdu_address, 6000)
    ok = wait_until(lambda: coil(v1) == 1 and rio_coil(v1) == 1, 15)
    words = plc.ints("holding", qw, 3)
    res.check(
        ok and words == [6000, 6000, 6600],
        "Modbus write HR105=6000 lands in %MW1 only",
        f"plc HR{qw}..{qw + 2}={words}; cone coil801={coil(v1)} ot-sim coil2={rio_coil(v1)}",
    )

    def fuxa_view() -> dict[str, float | None]:
        return fuxa.get([sp0.tag, sp1.tag, v0.tag, v1.tag])

    # FUXA polls the PLC once a second.
    ok = wait_until(
        lambda: (
            lambda fx: (
                fx[sp0.tag] == 60 and fx[sp1.tag] == 60 and fx[v0.tag] == 1 and fx[v1.tag] == 1
            )
        )(fuxa_view()),
        10,
    )
    res.check(ok, "FUXA sees both setpoints and both valves open", str(fuxa_view()))

    # 3. Raise both setpoints: PV + 0.5 <= SP closes the valves.
    fuxa.set(sp0.tag, 75.0)
    plc.write_register(sp1.slave_pdu_address, 7500)
    ok = wait_until(
        lambda: coil(v0) == 0 and coil(v1) == 0 and rio_coil(v0) == 0 and rio_coil(v1) == 0, 15
    )
    res.check(
        ok,
        "setpoint 75.0 closes both valves",
        f"plc coil800={coil(v0)} coil801={coil(v1)} ot-sim coil1={rio_coil(v0)} coil2={rio_coil(v1)}",
    )

    # Restore the defaults for the next phase.
    fuxa.set(sp0.tag, 66.0)
    plc.write_register(sp1.slave_pdu_address, 6600)
    ok = wait_until(lambda: plc.ints("holding", qw, 2) == [6600, 6600], 10)
    res.check(ok, "setpoints restored", f"plc HR{qw},{qw + 1}={plc.ints('holding', qw, 2)}")


def fault(ctx: Ctx, res: Results) -> None:
    plc, fuxa = Modbus(ctx.plc), Fuxa(ctx)
    alarms: list[Row] = alarm_rows(ctx)
    hi: Row = ctx.rows["CEL.FV101.PT01.ALM_HIGH"]
    pt: Row = ctx.rows["CEL.FV101.PT01.PV"]
    ok: bool = wait_until(lambda: plc.ints("input", pt.slave_pdu_address)[0] == 2700, 60)
    res.check(
        ok,
        "fault federate drives FV-101 head pressure",
        f"plc %IW103 (IR{pt.slave_pdu_address})={plc.ints('input', pt.slave_pdu_address)} (27.00 psia)",
    )
    t0: float = time.monotonic()
    ok = wait_until(lambda: plc.ints("coil", hi.slave_pdu_address)[0] == 1, 120, 1.0)
    waited: float = time.monotonic() - t0
    bits: dict[str, int] = {r.tag: plc.ints("coil", r.slave_pdu_address)[0] for r in alarms}
    raised: list[str] = sorted(t for t, v in bits.items() if v)
    res.check(
        ok and raised == [hi.tag],
        "head pressure high alarm bit",
        f"coil {hi.slave_pdu_address} (%QX0.3) set after {waited:.0f} s; alarm bits set: {raised}",
    )
    ok = wait_until(lambda: bool(fuxa.get([hi.tag])[hi.tag]), 10)
    fx: dict[str, float | None] = fuxa.get([r.tag for r in alarms])
    res.check(
        ok and [t for t, v in fx.items() if v] == [hi.tag],
        "FUXA alarm tag",
        f"FUXA set: {[t for t, v in fx.items() if v]}",
    )


def layout_probe(ctx: Ctx, res: Results, mode: str) -> None:
    """The PLC is running a ZIP with a deliberately different buffer_mapping."""
    plc = Modbus(ctx.plc)
    line: str = last_segment_line(ctx)
    logs: str = current_logs(ctx)
    if mode == "clamp":
        # The v3 map: 1024 of everything. The runtime clamps each count to the
        # program's own image table (highest located index + 1).
        want: str = segment_line(ctx.layout)
        reduced: bool = (
            "qw_count 1024 -> 104, mw_count 1024 -> 8, qx_bits 8192 -> 808, ix_bits 8192 -> 808, iw_count 1024 -> 117"
            in logs
        )
        res.check(
            want in line and reduced,
            "v3 counts are clamped to the located variables",
            f"{line.split('] ', 2)[-1]}; warning lists every clamp: {reduced}",
        )
        res.check(
            plc.ints("holding", 104, 8) == DEFAULT_SP
            and plc.read("holding", 112) == 2
            and plc.read("holding", 1024) == 2,
            "after clamping %MW0 is still holding 104",
            f"HR104..111={plc.ints('holding', 104, 8)} HR112={plc.read('holding', 112)} HR1024={plc.read('holding', 1024)}",
        )
    elif mode == "shifted":
        # qw_count 10 (smaller than the table): no clamp, %MW0 follows %QW9.
        res.check(
            "%QW=0-9, %MW=10-17" in line,
            "segments are back to back by count",
            line.split("] ", 2)[-1],
        )
        got: list[int] = plc.ints("holding", 10, 8)
        past: list[int] | int = plc.read("holding", 18)
        res.check(
            got == DEFAULT_SP and past == 2,
            "qw_count=10 moves %MW0 to holding 10",
            f"HR10..17={got} HR18 -> exception {past}; HR100 -> exception {plc.read('holding', 100)}",
        )
    else:
        raise SystemExit(f"unknown probe mode {mode}")


def main(argv: list[str] | None = None) -> int:
    ap: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("phase", choices=["baseline", "control", "fault", "layout-probe"])
    ap.add_argument("--mode", default="clamp", help="layout-probe: clamp or shifted")
    ap.add_argument("--rio", required=True)
    ap.add_argument("--plc", required=True)
    ap.add_argument("--fuxa", required=True)
    ap.add_argument("--fuxa-user", default="admin")
    ap.add_argument("--fuxa-password", default="cm-ci-fuxa-range-only")
    ap.add_argument("--plc-container", required=True)
    ap.add_argument("--registers", type=Path, required=True)
    ap.add_argument("--project", type=Path, required=True)
    args: argparse.Namespace = ap.parse_args(argv)
    ctx: Ctx = load(args)
    res: Results = Results()
    print(f"== phase {args.phase}", flush=True)
    if args.phase == "baseline":
        baseline(ctx, res)
    elif args.phase == "control":
        control(ctx, res)
    elif args.phase == "fault":
        fault(ctx, res)
    else:
        layout_probe(ctx, res, args.mode)
    if res.failed:
        print(f"FAILED {args.phase}: {', '.join(res.failed)}", file=sys.stderr)
        return 1
    print(f"phase {args.phase}: all assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
