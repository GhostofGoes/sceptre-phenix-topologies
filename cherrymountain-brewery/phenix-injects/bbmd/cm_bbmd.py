#!/usr/bin/env python3
"""A BACnet/IP BBMD for ot-svc (OPS) and bas-bbmd (BAS).

The two BBMDs carry FUXA's broadcast Who-Is across fw-ot (DESIGN.md sections
8.1, 10.3, and 10.4). Each one lists both BBMDs in its BDT. The entries have no
mask, so the sending BBMD unicasts the Forwarded-NPDU to its peer, which
rebroadcasts it on its own subnet. That is the mode that crosses a router that
does not forward directed broadcasts.

A BACpypes3 node hears broadcasts only on its subnet's directed broadcast
address. FUXA sends to 255.255.255.255, so the OPS BBMD listens on a /0 prefix
(``10.20.30.13/0:47808``), which makes its broadcast address 255.255.255.255.

Settings come from the environment (``/etc/default/cm-bbmd``)::

    CM_BBMD_NAME=bbmd-ops
    CM_BBMD_INSTANCE=9001
    CM_BBMD_ADDRESS=10.20.30.13/0:47808
    CM_BBMD_BDT=10.20.30.13:47808,10.30.10.5:47808

Requires bacpypes3 (the image installs 0.0.110 under /opt/bacnet). Based on
the bbmd spike's ``bbmd.py``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import sys
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

DEFAULT_PORT: int = 47808
_ADDRESS: re.Pattern[str] = re.compile(
    r"^(?P<ip>\d+\.\d+\.\d+\.\d+)(?:/(?P<prefix>\d+))?(?::(?P<port>\d+))?$"
)


def _split(entry: str) -> tuple[ipaddress.IPv4Address, int | None, int]:
    """Split ``ip[/prefix][:port]`` into the address, prefix, and port."""
    match: re.Match[str] | None = _ADDRESS.match(entry)
    if match is None:
        raise ValueError(f"not an ip[/prefix][:port] address: {entry!r}")
    prefix: int | None = int(match["prefix"]) if match["prefix"] else None
    if prefix is not None and not 0 <= prefix <= 32:
        raise ValueError(f"prefix out of range in {entry!r}")
    port: int = int(match["port"]) if match["port"] else DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range in {entry!r}")
    return ipaddress.IPv4Address(match["ip"]), prefix, port


class BbmdConfig(BaseModel):
    """The BBMD's device name, instance, listen address, and BDT."""

    name: str = Field(min_length=1)
    instance: int = Field(ge=0, le=4194302)
    #: ``ip/prefix:port`` this BBMD listens on.
    address: str
    #: ``ip:port`` of every BBMD, this one included.
    bdt: list[str] = Field(min_length=2)

    @field_validator("address")
    @classmethod
    def _address(cls, value: str) -> str:
        _split(value)
        return value

    @field_validator("bdt")
    @classmethod
    def _bdt(cls, value: list[str]) -> list[str]:
        for entry in value:
            _split(entry)
        return value

    @model_validator(mode="after")
    def _lists_itself(self) -> BbmdConfig:
        own: ipaddress.IPv4Address = _split(self.address)[0]
        if own not in {_split(e)[0] for e in self.bdt}:
            raise ValueError(f"the BDT must list this BBMD ({own})")
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BbmdConfig:
        try:
            return cls(
                name=env["CM_BBMD_NAME"],
                instance=int(env["CM_BBMD_INSTANCE"]),
                address=env["CM_BBMD_ADDRESS"],
                bdt=[e.strip() for e in env["CM_BBMD_BDT"].split(",") if e.strip()],
            )
        except KeyError as exc:
            raise ValueError(f"missing setting {exc.args[0]}") from exc

    def bacpypes_args(self) -> list[str]:
        """The BACpypes3 ``SimpleArgumentParser`` arguments for this BBMD."""
        return [
            "--name",
            self.name,
            "--instance",
            str(self.instance),
            "--address",
            self.address,
            "--bbmd",
            *self.bdt,
        ]


def block_readline() -> None:
    """Make ``import readline`` fail, unless readline is already loaded.

    BACpypes3's console module imports readline for its history file. When
    readline loads, ncurses calls ``setfsuid()``, which cm-bbmd.service filters
    (``SystemCallFilter=~@privileged``), so the kernel kills the BBMD with
    SIGSYS before it binds its port. The BBMD has no console, and BACpypes3
    runs without readline when the import fails.
    """
    sys.modules.setdefault("readline", None)  # type: ignore[arg-type]


async def serve(config: BbmdConfig) -> None:
    """Run the BBMD until the process is stopped."""
    block_readline()
    # Imported here so the settings can be checked without BACpypes3.
    from bacpypes3.app import Application  # noqa: PLC0415
    from bacpypes3.argparse import SimpleArgumentParser  # noqa: PLC0415

    args = SimpleArgumentParser().parse_args(config.bacpypes_args())
    Application.from_args(args)
    await asyncio.Event().wait()


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """Read the settings and run the BBMD; ``--check`` only validates them."""
    args: list[str] = list(sys.argv[1:] if argv is None else argv)
    try:
        config: BbmdConfig = BbmdConfig.from_env(os.environ if env is None else env)
    except (ValueError, ValidationError) as exc:
        print(f"cm-bbmd: bad settings: {exc}", file=sys.stderr)
        return 2
    if "--check" in args:
        print(" ".join(config.bacpypes_args()))
        return 0
    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
