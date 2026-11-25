"""The repository files the app reads: plant.yaml, the plantgen output, the
program ZIPs, the FUXA project builder, and the shared injects.

The app reuses the repository's own generators instead of copying their
logic:

* ``plantgen`` (tools/plantgen) loads and expands plant.yaml, so the hosts the
  app checks the topology against are the ones every generated file used.
* ``fuxa/build_project.py`` builds the FUXA project from the plantgen tag list.
* The OpenPLC and FUXA provisioning scripts and units under phenix-injects/
  are injected as they are.

Both generators are imported from the topology directory the app reads
(``sourceDir``), not installed into the image, so the code always matches the
plant.yaml and generated files next to it. A plantgen or builder change then
needs no image rebuild.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import json
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, Final

from pydantic import BaseModel, ConfigDict

#: The first bytes of a Git LFS pointer file.
LFS_POINTER: Final[bytes] = b"version https://git-lfs"


class SourceError(ValueError):
    """A repository input is missing or unusable. Messages name the file,
    relative to the topology directory, never its contents."""


@contextlib.contextmanager
def no_bytecode() -> Iterator[None]:
    """Import without writing ``__pycache__``. The app runs as root in the
    phenix container and imports plantgen and the FUXA builder from the
    topology directory, which is the user's Git checkout on the host: bytecode
    written there would be root-owned files the user cannot remove."""
    before: bool = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = before


class DebugLeaf(BaseModel):
    """One leaf of a strucpp ``debug_map.json``: where a variable lives in the
    runtime's debug arrays (``arr``/``elem`` in the OPC UA plugin config)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore", populate_by_name=True
    )

    array_idx: int
    elem_idx: int
    path: str
    type: str
    size: int

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DebugLeaf:
        return cls(
            array_idx=data["arrayIdx"],
            elem_idx=data["elemIdx"],
            path=data["path"],
            type=data["type"],
            size=data["size"],
        )


class DebugMap(BaseModel):
    """A strucpp debug map (format version 2, strucpp fcccfbdf)."""

    version: int
    md5: str
    leaves: list[DebugLeaf]

    @classmethod
    def parse(cls, data: dict[str, Any]) -> DebugMap:
        return cls(
            version=data["version"],
            md5=data["md5"],
            leaves=[DebugLeaf.from_json(leaf) for leaf in data["leaves"]],
        )

    def by_variable(self) -> dict[str, DebugLeaf]:
        """Leaves keyed by variable name, the last path component."""
        return {leaf.path.rsplit(".", 1)[-1]: leaf for leaf in self.leaves}


class Sources:
    """Paths and loaders for one topology directory and variant."""

    def __init__(self, root: Path, variant: str) -> None:
        self.root: Path = root
        self.variant: str = variant
        self.plant_yaml: Path = root / "plant" / "plant.yaml"
        self.generated: Path = root / "plant" / "generated" / variant
        self.injects: Path = root / "phenix-injects"
        self._plant: Any = None
        self._plantgen_render: ModuleType | None = None

    def rel(self, path: Path) -> str:
        """``path`` relative to the topology directory, for messages."""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def require(self, path: Path) -> Path:
        if not path.is_file():
            raise SourceError(f"missing {self.rel(path)} in the topology directory")
        return path

    # ------------------------------------------------------------ plantgen

    def plantgen_path(self) -> contextlib.AbstractContextManager[None]:
        """``tools/plantgen`` on ``sys.path`` for the duration (plantgen modules)."""
        return self._plantgen_path()

    @contextlib.contextmanager
    def _plantgen_path(self) -> Iterator[None]:
        path: str = str(self.root / "tools" / "plantgen")
        added: bool = path not in sys.path
        if added:
            sys.path.insert(0, path)
        try:
            with no_bytecode():
                yield
        finally:
            if added:
                sys.path.remove(path)

    def plant(self) -> Any:
        """plant.yaml expanded for the variant, by plantgen (``plantgen.model.Plant``)."""
        if self._plant is None:
            self.require(self.plant_yaml)
            with self._plantgen_path():
                try:
                    model: Any = importlib.import_module("plantgen.model")
                except ImportError as ex:
                    raise SourceError(
                        "cannot import plantgen from tools/plantgen in the topology directory"
                    ) from ex
                try:
                    self._plant = model.load(self.plant_yaml, self.variant)
                except ValueError as ex:
                    # PlantError names the plant.yaml key that is wrong.
                    raise SourceError(
                        f"plant/plant.yaml, variant {self.variant}: {ex}"
                    ) from ex
        return self._plant

    def st_name(self, tag: str) -> str:
        """The ST variable name plantgen gives a tag (CEL.FV101.TT01.PV -> CEL_FV101_TT01_PV)."""
        if self._plantgen_render is None:
            with self._plantgen_path():
                self._plantgen_render = importlib.import_module("plantgen.render")
        return str(self._plantgen_render.st_name(tag))

    # ------------------------------------------------------------ generated files

    def generated_json(self, *parts: str) -> Any:
        path: Path = self.require(self.generated.joinpath(*parts))
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as ex:
            raise SourceError(f"{self.rel(path)} is not valid JSON") from ex

    def generated_file(self, *parts: str) -> Path:
        return self.require(self.generated.joinpath(*parts))

    def plc_conf_dir(self, program: str) -> Path:
        return self.generated / "openplc" / program

    def program_zip(self, program: str) -> Path:
        """The program ZIP: ``phenix-injects/openplc/<program>.zip`` for ci
        (``plc/plc-cellar/build_zip.sh``), ``phenix-injects/openplc/<variant>/
        <program>.zip`` for full and lite (``plc/common/build_zip.sh``)."""
        sub: Path = Path("openplc") if self.variant == "ci" else Path("openplc") / self.variant
        path: Path = self.require(self.injects / sub / f"{program}.zip")
        with path.open("rb") as fh:
            if fh.read(len(LFS_POINTER)) == LFS_POINTER:
                raise SourceError(
                    f"{self.rel(path)} is a Git LFS pointer; run `git lfs pull` in the topology directory"
                )
        if not zipfile.is_zipfile(path):
            raise SourceError(f"{self.rel(path)} is not a ZIP file")
        return path

    def debug_map(self, program: str, explicit: str | None = None) -> DebugMap:
        """The compiled program's debug map: an explicit path, else
        ``debug_map.json`` in the program ZIP, else ``plc/<program>/debug_map.json``."""
        data: Any = None
        where: str = ""
        if explicit is not None:
            path: Path = self.require(self.root / explicit)
            data, where = json.loads(path.read_text(encoding="utf-8")), explicit
        else:
            with zipfile.ZipFile(self.program_zip(program)) as zf:
                if "debug_map.json" in zf.namelist():
                    data = json.loads(zf.read("debug_map.json"))
                    where = f"{self.rel(self.program_zip(program))}:debug_map.json"
            if data is None:
                path = self.root / "plc" / program / "debug_map.json"
                if not path.is_file():
                    raise SourceError(
                        f"no debug map for {program}: the program ZIP has no debug_map.json "
                        f"and plc/{program}/debug_map.json is missing (the OPC UA config "
                        "addresses variables by their debug-array index)"
                    )
                data, where = (
                    json.loads(path.read_text(encoding="utf-8")),
                    self.rel(path),
                )
        try:
            return DebugMap.parse(data)
        except (KeyError, TypeError) as ex:
            raise SourceError(f"{where} is not a strucpp debug map") from ex

    def zip_conf_drift(self, program: str, conf_dir: Path) -> list[str]:
        """Plugin configs in the program ZIP that differ from ``conf_dir``."""
        drift: list[str] = []
        with zipfile.ZipFile(self.program_zip(program)) as zf:
            names: set[str] = set(zf.namelist())
            for path in sorted(conf_dir.glob("*.json")):
                entry: str = f"conf/{path.name}"
                if entry in names and json.loads(zf.read(entry)) != json.loads(
                    path.read_text(encoding="utf-8")
                ):
                    drift.append(path.name)
        return drift

    def zip_md5(self, program: str) -> str | None:
        """``PROGRAM_MD5`` from the ZIP's defines.h, if present."""
        with zipfile.ZipFile(self.program_zip(program)) as zf:
            if "defines.h" not in zf.namelist():
                return None
            text: str = zf.read("defines.h").decode(errors="replace")
        marker: str = '#define PROGRAM_MD5 "'
        if marker not in text:
            return None
        return text.split(marker, 1)[1].split('"', 1)[0]

    # ------------------------------------------------------------ FUXA

    def fuxa_project(self, relpath: str | None, host: str = "scada-fuxa") -> dict[str, Any]:
        """A committed project file, or the one fuxa/build_project.py builds for
        ``host`` (scada-fuxa or bas-fuxa; ci has scada-fuxa only)."""
        if relpath is not None:
            path: Path = self.require(self.root / relpath)
            try:
                project: Any = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as ex:
                raise SourceError(f"{relpath} is not valid JSON") from ex
            if not isinstance(project, dict):
                raise SourceError(f"{relpath} is not a FUXA project object")
            return project

        builder_path: Path = self.require(self.root / "fuxa" / "build_project.py")
        spec = importlib.util.spec_from_file_location(
            "cm_fuxa_build_project", builder_path
        )
        if spec is None or spec.loader is None:
            raise SourceError("cannot load fuxa/build_project.py")
        builder: ModuleType = importlib.util.module_from_spec(spec)
        # Registered while it runs: pydantic resolves the builder's postponed
        # annotations through sys.modules.
        sys.modules[spec.name] = builder
        try:
            # The builder imports plantgen from tools/plantgen by itself.
            with no_bytecode():
                spec.loader.exec_module(builder)
                text: str = builder.render(builder.build_project(self.variant, host))
        except SystemExit as ex:
            # The builder reports a bad input with SystemExit(message).
            raise SourceError(
                f"fuxa/build_project.py failed for variant {self.variant}: {ex}"
            ) from None
        except Exception as ex:
            raise SourceError(
                f"fuxa/build_project.py failed for variant {self.variant} "
                f"({type(ex).__name__}); it may not handle this variant's tag list yet"
            ) from None
        finally:
            sys.modules.pop(spec.name, None)
        result: Any = json.loads(text)
        if not isinstance(result, dict):
            raise SourceError("fuxa/build_project.py did not return a project object")
        return result

    # ------------------------------------------------------------ shared injects

    def inject_file(self, *parts: str) -> Path:
        """A file under phenix-injects/, reused as it is."""
        return self.require(self.injects.joinpath(*parts))
