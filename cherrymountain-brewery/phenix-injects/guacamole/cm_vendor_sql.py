#!/usr/bin/env python3
"""Print the SQL that gives the vendor technician a Guacamole account.

Installed on dmz-jump as /usr/local/lib/cm-guacamole/cm_vendor_sql.py and run by
cm-guacamole-start after PostgreSQL is healthy. The cherrymountain app's
50-cherrymountain.sql creates the `cmadmin` administrator and the `ews-rdp` and
`ews-ssh` connections; this adds the account that Ridgeline's technician uses
(DESIGN.md 13):

* the account, with Guacamole's salted password hash (SHA-256 of the password and
  the upper-case hex salt, exactly as the app writes it),
* a TOTP key that is already enrolled (``guac-totp-key-secret`` and
  ``guac-totp-key-confirmed``), so a scripted session needs no QR code, and
* READ permission on the two connections, with the EWS account's credentials stored
  as connection parameters, so a session opens straight into the EWS.

The script is idempotent, so cm-guacamole-start can run it at every start. It reads
``ridgeline.env`` (``--env``) and validates every value before it is put into SQL.
Standard library plus pydantic.

Usage: cm_vendor_sql.py [--env /etc/cherrymountain/ridgeline.env] [--salt HEX]
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sys
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, StringConstraints

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
#: Passwords go into SQL string literals, so only a safe alphabet is accepted.
Password = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{8,128}$")]
Base32 = Annotated[str, StringConstraints(pattern=r"^[A-Z2-7]{16,64}$")]

#: The connections the app creates, and the parameters of each that hold the EWS login.
CONNECTIONS: tuple[str, ...] = ("ews-rdp", "ews-ssh")


class Vendor(BaseModel):
    """The vendor persona, from ridgeline.env."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    VENDOR_GUAC_USER: Name
    VENDOR_GUAC_PASSWORD: Password
    VENDOR_TOTP_SECRET: Base32
    VENDOR_EWS_USER: Name
    VENDOR_EWS_PASSWORD: Password

    @classmethod
    def from_file(cls, path: Path) -> Vendor:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                values[key] = value
        return cls.model_validate(values)


def password_hash(password: str, salt_hex: str) -> str:
    """Guacamole stores SHA-256(password + upper-case hex of the salt)."""
    return hashlib.sha256((password + salt_hex.upper()).encode()).hexdigest()


def sql(vendor: Vendor, salt_hex: str) -> str:
    user: str = vendor.VENDOR_GUAC_USER
    digest: str = password_hash(vendor.VENDOR_GUAC_PASSWORD, salt_hex)
    where_user: str = f"e.name = '{user}' AND e.type = 'USER'"
    lines: list[str] = [
        "-- Rendered by cm_vendor_sql.py (DESIGN.md 13). Do not edit.",
        "BEGIN;",
        f"INSERT INTO guacamole_entity (name, type) VALUES ('{user}', 'USER') ON CONFLICT DO NOTHING;",
        "INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)",
        f"  SELECT e.entity_id, decode('{digest}', 'hex'), decode('{salt_hex}', 'hex'), now()",
        f"  FROM guacamole_entity e WHERE {where_user}",
        "  ON CONFLICT (entity_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, password_salt = EXCLUDED.password_salt;",
    ]
    for attribute, value in (("guac-totp-key-secret", vendor.VENDOR_TOTP_SECRET), ("guac-totp-key-confirmed", "true")):
        lines += [
            "INSERT INTO guacamole_user_attribute (user_id, attribute_name, attribute_value)",
            f"  SELECT u.user_id, '{attribute}', '{value}'",
            f"  FROM guacamole_user u JOIN guacamole_entity e ON e.entity_id = u.entity_id WHERE {where_user}",
            "  ON CONFLICT (user_id, attribute_name) DO UPDATE SET attribute_value = EXCLUDED.attribute_value;",
        ]
    for connection in CONNECTIONS:
        lines += [
            "INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)",
            f"  SELECT e.entity_id, c.connection_id, 'READ' FROM guacamole_entity e, guacamole_connection c",
            f"  WHERE {where_user} AND c.connection_name = '{connection}' AND c.parent_id IS NULL",
            "  ON CONFLICT DO NOTHING;",
        ]
        for parameter, value in (("username", vendor.VENDOR_EWS_USER), ("password", vendor.VENDOR_EWS_PASSWORD)):
            lines += [
                "INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)",
                f"  SELECT connection_id, '{parameter}', '{value}' FROM guacamole_connection",
                f"  WHERE connection_name = '{connection}' AND parent_id IS NULL",
                "  ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;",
            ]
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, default=Path("/etc/cherrymountain/ridgeline.env"))
    parser.add_argument("--salt", default=None, help="32 bytes of hex; random when left out")
    args: argparse.Namespace = parser.parse_args(argv)
    vendor: Vendor = Vendor.from_file(args.env)
    salt: str = args.salt or secrets.token_hex(32)
    sys.stdout.write(sql(vendor, salt))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
