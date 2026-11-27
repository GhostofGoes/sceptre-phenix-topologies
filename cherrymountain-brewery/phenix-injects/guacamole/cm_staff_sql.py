#!/usr/bin/env python3
"""Print the SQL that gives each of the company's staff a named Guacamole account.

Installed on the jump host of a site with staff accounts (plant/sites.yaml,
`staff`) as /usr/local/lib/cm-guacamole/cm_staff_sql.py, and run by
cm-guacamole-start after PostgreSQL is healthy. The jump host is the company's
own, with no account of the vendor (docs/multisite/PLANS.md 4.6). Each person
on the roster gets what a vendor technician gets at the head office:
cm_guac_accounts.py, installed next to this script, writes the account, its
TOTP key, and its own copies of the two connections to the workstation, with
recorded sessions.

The script is idempotent, so cm-guacamole-start can run it at every start. It
reads ``staff.env`` (``--env``): ``STAFF_COUNT``, and for each person
``STAFF<n>_USER``, ``_GUAC_PASSWORD``, ``_TOTP_SECRET``, and ``_EWS_PASSWORD``.
The account on the workstation has the same name (71-ews-staff.sh creates it).
Every value is validated before it is put into SQL. Standard library plus
pydantic.

Usage: cm_staff_sql.py [--env /etc/cherrymountain/staff.env] [--salt HEX]
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

# cm-guacamole-start runs this script with `python3 -I`, which leaves the
# script's directory out of the module path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cm_guac_accounts import Account, count, read_env, report, roster_sql  # noqa: E402
from pydantic import ValidationError  # noqa: E402

SCRIPT: str = "cm_staff_sql"


def roster(values: dict[str, str]) -> tuple[Account, ...]:
    """The accounts of a staff.env. A key that the roster does not use is an error."""
    rest: dict[str, str] = dict(values)
    staff: list[Account] = []
    for n in range(1, count(rest.pop("STAFF_COUNT", None), "STAFF_COUNT") + 1):
        user: str = rest.pop(f"STAFF{n}_USER", "")
        staff.append(
            Account(
                guac_user=user,
                guac_password=rest.pop(f"STAFF{n}_GUAC_PASSWORD", ""),
                totp_secret=rest.pop(f"STAFF{n}_TOTP_SECRET", ""),
                ews_user=user,
                ews_password=rest.pop(f"STAFF{n}_EWS_PASSWORD", ""),
            )
        )
    if rest:
        raise ValueError(f"the roster does not use the keys {sorted(rest)}")
    names: list[str] = [account.guac_user for account in staff]
    if len(set(names)) != len(names):
        raise ValueError("an account is listed twice")
    return tuple(staff)


def sql(staff: tuple[Account, ...], salt_hex: str) -> str:
    return roster_sql(staff, salt_hex, SCRIPT)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, default=Path("/etc/cherrymountain/staff.env"))
    parser.add_argument("--salt", default=None, help="32 bytes of hex; random when left out")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        staff: tuple[Account, ...] = roster(read_env(args.env))
    except (ValidationError, ValueError) as error:
        report(SCRIPT, args.env, error)
        return 2
    sys.stdout.write(sql(staff, args.salt or secrets.token_hex(32)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
