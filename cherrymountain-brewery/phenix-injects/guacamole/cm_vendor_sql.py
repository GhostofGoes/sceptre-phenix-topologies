#!/usr/bin/env python3
"""Print the SQL that gives each vendor technician a named Guacamole account.

Installed on dmz-jump as /usr/local/lib/cm-guacamole/cm_vendor_sql.py and run by
cm-guacamole-start after PostgreSQL is healthy. The cherrymountain app's
50-cherrymountain.sql creates the `cmadmin` administrator and the `ews-rdp` and
`ews-ssh` connections; this adds one account per Ridgeline technician
(DESIGN.md 13), so every session is attributable to a person, not to a shared
vendor login:

* the account ``ridgeline-<name>``, with Guacamole's salted password hash (SHA-256
  of the password and the upper-case hex salt, exactly as the app writes it),
* its own TOTP key, already enrolled (``guac-totp-key-secret`` and
  ``guac-totp-key-confirmed``), so a scripted session needs no QR code, and
* its own copies of the two connections, ``ews-rdp-<account>`` and
  ``ews-ssh-<account>`` (every parameter of the app's connection, recording
  included), holding that technician's own EWS account, with READ permission for
  that technician only. The app's connections keep no vendor credentials.

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
import hashlib
import secrets
import sys
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
#: Passwords go into SQL string literals, so only a safe alphabet is accepted.
Password = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{8,128}$")]
Base32 = Annotated[str, StringConstraints(pattern=r"^[A-Z2-7]{16,64}$")]

#: The connections the app creates; each technician gets a copy of each.
CONNECTIONS: tuple[str, ...] = ("ews-rdp", "ews-ssh")
#: The shared vendor account of earlier versions, removed unless on the roster.
LEGACY_USER: str = "ridgeline-tech"
#: Most technicians a roster may list.
MAX_TECHS: int = 8


class Technician(BaseModel):
    """One technician: a Guacamole account and the EWS account it opens."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    guac_user: Name
    guac_password: Password
    totp_secret: Base32
    ews_user: Name
    ews_password: Password


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
            if not count_text.isdigit() or not 1 <= int(count_text) <= MAX_TECHS:
                raise ValueError(f"VENDOR_TECH_COUNT must be 1 to {MAX_TECHS}")
            for n in range(1, int(count_text) + 1):
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
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                values[key] = value
        return cls.from_values(values)


def password_hash(password: str, salt_hex: str) -> str:
    """Guacamole stores SHA-256(password + upper-case hex of the salt)."""
    return hashlib.sha256((password + salt_hex.upper()).encode()).hexdigest()


def personal(connection: str, user: str) -> str:
    """The technician's own copy of an app connection."""
    return f"{connection}-{user}"


def tech_sql(tech: Technician, salt_hex: str) -> list[str]:
    user: str = tech.guac_user
    digest: str = password_hash(tech.guac_password, salt_hex)
    where_user: str = f"e.name = '{user}' AND e.type = 'USER'"
    lines: list[str] = [
        f"-- {user}",
        f"INSERT INTO guacamole_entity (name, type) VALUES ('{user}', 'USER') ON CONFLICT DO NOTHING;",
        "INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)",
        f"  SELECT e.entity_id, decode('{digest}', 'hex'), decode('{salt_hex}', 'hex'), now()",
        f"  FROM guacamole_entity e WHERE {where_user}",
        "  ON CONFLICT (entity_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, password_salt = EXCLUDED.password_salt;",
    ]
    for attribute, value in (("guac-totp-key-secret", tech.totp_secret), ("guac-totp-key-confirmed", "true")):
        lines += [
            "INSERT INTO guacamole_user_attribute (user_id, attribute_name, attribute_value)",
            f"  SELECT u.user_id, '{attribute}', '{value}'",
            f"  FROM guacamole_user u JOIN guacamole_entity e ON e.entity_id = u.entity_id WHERE {where_user}",
            "  ON CONFLICT (user_id, attribute_name) DO UPDATE SET attribute_value = EXCLUDED.attribute_value;",
        ]
    for base in CONNECTIONS:
        mine: str = personal(base, user)
        # A root-level connection's unique key includes a NULL parent, which
        # ON CONFLICT cannot see, so the copy is guarded by NOT EXISTS.
        lines += [
            "INSERT INTO guacamole_connection (connection_name, protocol)",
            f"  SELECT '{mine}', b.protocol FROM guacamole_connection b",
            f"  WHERE b.connection_name = '{base}' AND b.parent_id IS NULL",
            f"  AND NOT EXISTS (SELECT 1 FROM guacamole_connection c WHERE c.connection_name = '{mine}' AND c.parent_id IS NULL);",
            "INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)",
            "  SELECT n.connection_id, p.parameter_name, p.parameter_value FROM guacamole_connection_parameter p",
            f"  JOIN guacamole_connection b ON b.connection_id = p.connection_id AND b.connection_name = '{base}' AND b.parent_id IS NULL",
            f"  JOIN guacamole_connection n ON n.connection_name = '{mine}' AND n.parent_id IS NULL",
            "  WHERE p.parameter_name NOT IN ('username', 'password')",
            "  ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;",
        ]
        for parameter, value in (("username", tech.ews_user), ("password", tech.ews_password)):
            lines += [
                "INSERT INTO guacamole_connection_parameter (connection_id, parameter_name, parameter_value)",
                f"  SELECT connection_id, '{parameter}', '{value}' FROM guacamole_connection",
                f"  WHERE connection_name = '{mine}' AND parent_id IS NULL",
                "  ON CONFLICT (connection_id, parameter_name) DO UPDATE SET parameter_value = EXCLUDED.parameter_value;",
            ]
        lines += [
            "INSERT INTO guacamole_connection_permission (entity_id, connection_id, permission)",
            "  SELECT e.entity_id, c.connection_id, 'READ' FROM guacamole_entity e, guacamole_connection c",
            f"  WHERE {where_user} AND c.connection_name = '{mine}' AND c.parent_id IS NULL",
            "  ON CONFLICT DO NOTHING;",
            # A missing app connection makes the copy above add no rows without an
            # error; fail the transaction instead, so the start script does not PASS.
            "DO $$ BEGIN",
            "  IF NOT EXISTS (SELECT 1 FROM guacamole_connection_permission p",
            "    JOIN guacamole_entity e ON e.entity_id = p.entity_id",
            "    JOIN guacamole_connection c ON c.connection_id = p.connection_id",
            f"    WHERE {where_user} AND c.connection_name = '{mine}' AND c.parent_id IS NULL) THEN",
            f"    RAISE EXCEPTION 'cm_vendor_sql: {user} has no permission on connection {mine}';",
            "  END IF;",
            "END $$;",
        ]
    return lines


def sql(vendor: Vendor, salt_hex: str) -> str:
    users: list[str] = [t.guac_user for t in vendor.technicians]
    lines: list[str] = ["-- Rendered by cm_vendor_sql.py (DESIGN.md 13). Do not edit.", "BEGIN;"]
    if LEGACY_USER not in users:
        lines += [
            "-- The shared vendor login of earlier versions: named accounts replace it.",
            f"DELETE FROM guacamole_entity WHERE name = '{LEGACY_USER}' AND type = 'USER';",
        ]
    names: str = ", ".join(f"'{c}'" for c in CONNECTIONS)
    lines += [
        "-- The app's connections keep no vendor credentials.",
        "DELETE FROM guacamole_connection_parameter WHERE parameter_name IN ('username', 'password')",
        f"  AND connection_id IN (SELECT connection_id FROM guacamole_connection WHERE connection_name IN ({names}) AND parent_id IS NULL);",
    ]
    for tech in vendor.technicians:
        # A salt of its own per account, derived from the run's salt.
        lines += tech_sql(tech, hashlib.sha256((salt_hex + tech.guac_user).encode()).hexdigest())
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, default=Path("/etc/cherrymountain/ridgeline.env"))
    parser.add_argument("--salt", default=None, help="32 bytes of hex; random when left out")
    args: argparse.Namespace = parser.parse_args(argv)
    try:
        vendor: Vendor = Vendor.from_file(args.env)
    except ValidationError as error:
        # Report where and what, never the input: it may be a password.
        for err in error.errors():
            where: str = ".".join(str(part) for part in err["loc"]) or "(file)"
            print(f"cm_vendor_sql: {args.env}: {where}: {err['type']}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"cm_vendor_sql: {args.env}: {error}", file=sys.stderr)
        return 2
    salt: str = args.salt or secrets.token_hex(32)
    sys.stdout.write(sql(vendor, salt))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
