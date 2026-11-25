"""Run a CherryMountain PLC program as OpenPLC would: the strucpp C++ of its ST.

``plc/common/build_zip.sh`` compiles a PLC's Structured Text with strucpp
(pinned) into C++ for the OpenPLC v4 runtime. This builds the same C++ with
``g++`` into a shared library behind a small C API and loads it with
:mod:`ctypes`, so a test sets located inputs, runs scans against a scan-cycle
time it controls (``TIME()``, which the TON timers read), and reads every
variable, nothing but the OpenPLC runtime's image-table binding and Modbus
plugins missing. What runs is the program as built, not a model of it.

It needs Node and a built strucpp checkout (the one ``build_zip.sh`` clones into
``~/.cache/cherrymountain/strucpp-<ref>``, or ``$STRUCPP``) and ``g++``;
:func:`toolchain_gap` says what is missing, and the tests skip without them.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

TOPOLOGY: Path = Path(__file__).resolve().parents[2]
PLCBUILD: Path = TOPOLOGY / "plc" / "common" / "plcbuild.py"
GEN_PROGRAM: Path = TOPOLOGY / "plc" / "common" / "gen_program.mjs"
#: The strucpp commit plc/common/build_zip.sh pins.
STRUCPP_REF: str = "fcccfbdf53704fad1f5e71dd2a05d440b9028b47"
INSTANCE: str = "INSTANCE0"

HARNESS_HEAD: str = """\
#include <cstdint>

#include "generated.hpp"

using namespace strucpp;

static Configuration_CONFIG0 g_config;

// Constants (VAR CONSTANT) are listed in the debug map too; writing one is a no-op.
template <class V> static void put(V &x, int64_t v) { x.set(static_cast<decltype(x.get())>(v)); }
template <class V> static void put(const V &, int64_t) {}

struct Slot {
    const char *name;
    int64_t (*get)();
    void (*set)(int64_t);
};

"""

HARNESS_TAIL: str = """
extern "C" {
int plc_count() { return static_cast<int>(sizeof(slots) / sizeof(slots[0])); }
const char *plc_name(int i) { return slots[i].name; }
int64_t plc_get(int i) { return slots[i].get(); }
void plc_set(int i, int64_t v) { slots[i].set(v); }
void plc_scan(int64_t time_ns) {
    __CURRENT_TIME_NS = time_ns;
    g_config.INSTANCE0.run();
}
}
"""


class Leaf(BaseModel):
    """One variable of strucpp's ``debug_map.json``."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    path: str
    type: str


class DebugMap(BaseModel):
    """The part of ``debug_map.json`` the engine uses."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    leaves: list[Leaf]


def strucpp_dir() -> Path:
    """Where ``build_zip.sh`` keeps the pinned strucpp checkout."""
    if os.environ.get("STRUCPP"):
        return Path(os.environ["STRUCPP"])
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return cache / "cherrymountain" / f"strucpp-{STRUCPP_REF[:12]}"


def toolchain_gap() -> str:
    """Why the ST cannot be compiled and run here, or ``""`` when it can."""
    missing = [tool for tool in ("node", "g++") if shutil.which(tool) is None]
    if missing:
        return f"{' and '.join(missing)} not on PATH"
    root = strucpp_dir()
    if not (root / "dist" / "node" / "index.js").is_file() or not (
        root / "libs" / "iec-standard-fb.stlib"
    ).is_file():
        return (
            f"no built strucpp checkout at {root} (plc/common/build_zip.sh clones and "
            "builds it; or set STRUCPP)"
        )
    return ""


def _run(cmd: list[str], what: str) -> None:
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    if done.returncode:
        raise RuntimeError(f"{what} failed:\n{done.stdout}\n{done.stderr}")


class CompiledPlc:
    """A PLC program built from its ST, driven scan by scan."""

    program: Path
    lib_path: Path
    names: list[str]
    index: dict[str, int]
    lib: ctypes.CDLL
    time_ns: int
    copies: int

    def __init__(self, lib_path: Path, names: list[str], program: Path) -> None:
        self.program = program
        self.lib_path = lib_path
        self.copies = 0
        self.names = names
        self.index = {n: i for i, n in enumerate(names)}
        self.lib = ctypes.CDLL(str(lib_path))
        self.lib.plc_get.restype = ctypes.c_int64
        self.lib.plc_get.argtypes = [ctypes.c_int]
        self.lib.plc_set.restype = None
        self.lib.plc_set.argtypes = [ctypes.c_int, ctypes.c_int64]
        self.lib.plc_scan.restype = None
        self.lib.plc_scan.argtypes = [ctypes.c_int64]
        self.time_ns = 0

    @classmethod
    def build(cls, plc: str, variant: str, work: Path) -> CompiledPlc:
        """Assemble (plcbuild.py), compile (strucpp), and link (g++) ``plc``."""
        gap = toolchain_gap()
        if gap:
            raise RuntimeError(gap)
        asm = work / "asm"
        gen = work / "gen"
        _run(
            ["python3", "-I", str(PLCBUILD), plc, "--variant", variant, "--out", str(asm)],
            "plcbuild.py",
        )
        program = asm / f"{plc}-full.st"
        md5 = _md5(program)
        _run(
            ["node", str(GEN_PROGRAM), str(strucpp_dir()), str(program), str(gen), md5],
            "strucpp",
        )
        debug = DebugMap.model_validate_json(
            (gen / "debug_map.json").read_text(encoding="utf-8")
        )
        names = [
            leaf.path.removeprefix(f"{INSTANCE}.")
            for leaf in debug.leaves
            if leaf.path.startswith(f"{INSTANCE}.")
        ]
        rows = "".join(
            f"    {{\"{n}\", []() -> int64_t {{ return "
            f"static_cast<int64_t>(g_config.{INSTANCE}.{n}.get()); }}, "
            f"[](int64_t v) {{ put(g_config.{INSTANCE}.{n}, v); }}}},\n"
            for n in names
        )
        harness = work / "harness.cpp"
        harness.write_text(
            HARNESS_HEAD + "static const Slot slots[] = {\n" + rows + "};\n" + HARNESS_TAIL,
            encoding="utf-8",
        )
        lib = work / "plc.so"
        _run(
            [
                "g++", "-std=c++17", "-O1", "-shared", "-fPIC", "-w",
                f"-I{gen}",
                f"-I{strucpp_dir() / 'src' / 'runtime' / 'include'}",
                str(gen / "generated.cpp"), str(harness), "-o", str(lib),
            ],
            "g++",
        )
        return cls(lib, names, program)

    def fresh(self) -> CompiledPlc:
        """A new PLC of the same program with its own memory (a cold start).

        The program's state is static in the library, and the loader shares one
        library per path, so each PLC gets its own copy of the file.
        """
        self.copies += 1
        copy = self.lib_path.with_name(f"plc-{self.copies}.so")
        shutil.copy(self.lib_path, copy)
        return CompiledPlc(copy, self.names, self.program)

    def set(self, name: str, value: int | bool) -> None:
        """Set a variable (a located input, or operator memory)."""
        self.lib.plc_set(self.index[name], int(value))

    def get(self, name: str) -> int:
        """A variable's value (booleans as 0 and 1, TIME in nanoseconds)."""
        return int(self.lib.plc_get(self.index[name]))

    def scan(self, time_ns: int) -> None:
        """Run the program once with ``TIME()`` at ``time_ns``."""
        self.time_ns = time_ns
        self.lib.plc_scan(time_ns)


def _md5(path: Path) -> str:
    import hashlib  # noqa: PLC0415 - one use

    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
