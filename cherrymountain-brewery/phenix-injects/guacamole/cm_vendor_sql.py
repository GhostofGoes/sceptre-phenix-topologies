#!/usr/bin/env python3
"""Print the SQL that gives each vendor technician a named Guacamole account.

Installed on dmz-jump as /usr/local/lib/cm-guacamole/cm_vendor_sql.py and run by
cm-guacamole-start after PostgreSQL is healthy. The cherrymountain app's
50-cherrymountain.sql creates the `cmadmin` administrator and the `ews-rdp` and
`ews-ssh` connections; this adds one account per Ridgeline technician
(DESIGN.md 13), so every session is attributable to a person, not to a shared
vendor login. cm_guac_accounts.py, installed next to this script, writes the
SQL of each account: the account ``ridgeline-<name>``, its own TOTP key, and
its own copies of the two connections with that technician's own EWS account.

The shared account of earlier versions (``ridgeline-tech``) is deleted when it is
not on the roster. The script is idempotent, so cm-guacamole-start can run it at
every start. It reads ``ridgeline.env`` (``--env``): the roster is
``VENDOR_TECH_COUNT`` and ``VENDOR_TECH<n>_USER``, ``_GUAC_PASSWORD``,
``_TOTP_SECRET``, and ``_EWS_PASSWORD``; a file without a roster is one technician
from the ``VENDOR_*`` keys. Every value is validated before it is put into SQL.
Standard library plus pydantic.

Usage: cm_vendor_sql.py [--env /etc/cherrymountain/ridgeline.env] [--salt HEX]
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import ClassVar

# cm-guacamole-start runs this script with `python3 -I`, which leaves the
# script's directory out of the module path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cm_guac_accounts import (  # noqa: E402
    CONNECTIONS,  # noqa: F401 (the tests read it here)
    MAX_ACCOUNTS,
    Base32,
    Name,
    Password,
    count,
    password_hash,  # noqa: F401
    personal,  # noqa: F401
    read_env,
    report,
    roster_sql,
)
from cm_guac_accounts import Account as Technician  # noqa: E402
from pydantic import BaseModel, ConfigDict, ValidationError  # noqa: E402

#: The shared vendor account of earlier versions, removed unless on the roster.
LEGACY_USER: str = "ridgeline-tech"
#: Most technicians a roster may list.
MAX_TECHS: int = MAX_ACCOUNTS
SCRIPT: str = "cm_vendor_sql"


class Vendor(BaseModel):
    """The vendor persona, from ridgeline.env."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    VENDOR_GUAC_USER: Name
    VENDOR_GUAC_PASSWORD: Password
    VENDOR_TOTP_SECRET: Base32
    VENDOR_EWS_USER: Name
    VENDOR_EWS_PASSWORD: Password
    technicians: tuple[Technician, ...]

    @classmethod
    def from_values(cls, values: dict[str, str]) -> Vendor:
        rest: dict[str, str] = dict(values)
        count_text: str | None = rest.pop("VENDOR_TECH_COUNT", None)
        techs: list[dict[str, str]] = []
        if count_text is None:
            techs.append(
                {
                    "guac_user": rest.get("VENDOR_GUAC_USER", ""),
                    "guac_password": rest.get("VENDOR_GUAC_PASSWORD", ""),
                    "totp_secret": rest.get("VENDOR_TOTP_SECRET", ""),
                    "ews_user": rest.get("VENDOR_EWS_USER", ""),
                    "ews_password": rest.get("VENDOR_EWS_PASSWORD", ""),
                }
            )
        else:
            for n in range(1, count(count_text, "VENDOR_TECH_COUNT") + 1):
                user: str = rest.pop(f"VENDOR_TECH{n}_USER", "")
                techs.append(
                    {
                        "guac_user": user,
                        "guac_password": rest.pop(f"VENDOR_TECH{n}_GUAC_PASSWORD", ""),
                        "totp_secret": rest.pop(f"VENDOR_TECH{n}_TOTP_SECRET", ""),
                        "ews_user": user,
                        "ews_password": rest.pop(f"VENDOR_TECH{n}_EWS_PASSWORD", ""),
                    }
                )
        vendor: Vendor = cls.model_validate({**rest, "technicians": techs})
        first: Technician = vendor.technicians[0]
        if (first.guac_user, first.guac_password, first.totp_secret, first.ews_user, first.ews_password) != (
            vendor.VENDOR_GUAC_USER,
            vendor.VENDOR_GUAC_PASSWORD,
            vendor.VENDOR_TOTP_SECRET,
            vendor.VENDOR_EWS_USER,
            vendor.VENDOR_EWS_PASSWORD,
        ):
            raise ValueError("the VENDOR_* keys must be the first technician's")
        names: list[str] = [t.guac_user for t in vendor.technicians]
        if len(set(names)) != len(names):
            raise ValueError("a technician is listed twice")
        return vendor

    @classmethod
    def from_file(cls, path: Path) -> Vendor:
        return cls.from_values(read_env(path))


def sql(vendor: Vendor, salt_hex: str) -> str:
    before: tuple[str, ...] = ()
    if LEGACY_USER not in [t.guac_user for t in vendor.technicians]:
        before = (
            "-- The shared vendor login of earlier versions: named accounts replace it.",
            f"DELETE FROM guacamole_entity WHERE name = '{LEGACY_USER}' AND type = 'USER';",
        )
    return roster_sql(vendor.technicians, salt_hex, SCRIPT, before)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, default=Path("/etc/cherrymountain/ridgeline.env"))
    parser.add_argument("--salt", default=None, help="32 bytes of hex; random when left out")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        vendor: Vendor = Vendor.from_file(args.env)
    except (ValidationError, ValueError) as error:
        report(SCRIPT, args.env, error)
        return 2
    sys.stdout.write(sql(vendor, args.salt or secrets.token_hex(32)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
