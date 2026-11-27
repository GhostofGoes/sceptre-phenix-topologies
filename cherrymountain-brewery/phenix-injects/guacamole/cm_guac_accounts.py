#!/usr/bin/env python3
"""The SQL of a named Guacamole account. The library of the two roster scripts.

Installed on a jump host as /usr/local/lib/cm-guacamole/cm_guac_accounts.py, next
to the scripts that import it:

* cm_vendor_sql.py: the vendor's technicians, from ridgeline.env (DESIGN.md 13).
* cm_staff_sql.py: the company's own staff at a site's jump host, from staff.env
  (docs/multisite/PLANS.md 4.6).

Both give a person the same three things, so every session names a person and
not a shared login:

* an account, with Guacamole's salted password hash (SHA-256 of the password and
  the upper-case hex salt, exactly as the app writes it),
* its own TOTP key, already enrolled (``guac-totp-key-secret`` and
  ``guac-totp-key-confirmed``), so a scripted session needs no QR code, and
* its own copies of the app's two connections, ``ews-rdp-<account>`` and
  ``ews-ssh-<account>`` (every parameter of the app's connection, recording
  included), holding that person's own EWS account, with READ permission for
  that person only. The app's connections keep no credentials.

The SQL can be applied any number of times. Every value is validated before it
is put into SQL. Standard library plus pydantic.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

Name = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
#: Passwords go into SQL string literals, so only a safe alphabet is accepted.
Password = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{8,128}$")]
Base32 = Annotated[str, StringConstraints(pattern=r"^[A-Z2-7]{16,64}$")]

#: The connections the app creates; each account gets a copy of each.
CONNECTIONS: tuple[str, ...] = ("ews-rdp", "ews-ssh")
#: Most accounts a roster may list.
MAX_ACCOUNTS: int = 8


class Account(BaseModel):
    """One person: a Guacamole account and the EWS account it opens."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    guac_user: Name
    guac_password: Password
    totp_secret: Base32
    ews_user: Name
    ews_password: Password


def read_env(path: Path) -> dict[str, str]:
    """The KEY=value lines of a roster file."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


def count(text: str | None, key: str) -> int:
    """The number of accounts that a roster's count key gives."""
    if text is None or not text.isdigit() or not 1 <= int(text) <= MAX_ACCOUNTS:
        raise ValueError(f"{key} must be 1 to {MAX_ACCOUNTS}")
    return int(text)


def report(script: str, path: Path, error: ValidationError | ValueError) -> None:
    """Say where a roster is wrong and what is wrong, never the input: it may be a password."""
    if isinstance(error, ValidationError):
        for err in error.errors():
            where: str = ".".join(str(part) for part in err["loc"]) or "(file)"
            print(f"{script}: {path}: {where}: {err['type']}", file=sys.stderr)
    else:
        print(f"{script}: {path}: {error}", file=sys.stderr)


def password_hash(password: str, salt_hex: str) -> str:
    """Guacamole stores SHA-256(password + upper-case hex of the salt)."""
    return hashlib.sha256((password + salt_hex.upper()).encode()).hexdigest()


def personal(connection: str, user: str) -> str:
    """The account's own copy of an app connection."""
    return f"{connection}-{user}"


def account_sql(account: Account, salt_hex: str, script: str) -> list[str]:
    """The SQL of one account. `script` names the caller in the error of the last check."""
    user: str = account.guac_user
    digest: str = password_hash(account.guac_password, salt_hex)
    where_user: str = f"e.name = '{user}' AND e.type = 'USER'"
    lines: list[str] = [
        f"-- {user}",
        f"INSERT INTO guacamole_entity (name, type) VALUES ('{user}', 'USER') ON CONFLICT DO NOTHING;",
        "INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)",
        f"  SELECT e.entity_id, decode('{digest}', 'hex'), decode('{salt_hex}', 'hex'), now()",
        f"  FROM guacamole_entity e WHERE {where_user}",
        "  ON CONFLICT (entity_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, password_salt = EXCLUDED.password_salt;",
    ]
    for attribute, value in (("guac-totp-key-secret", account.totp_secret), ("guac-totp-key-confirmed", "true")):
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
        for parameter, value in (("username", account.ews_user), ("password", account.ews_password)):
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
            f"    RAISE EXCEPTION '{script}: {user} has no permission on connection {mine}';",
            "  END IF;",
            "END $$;",
        ]
    return lines


def roster_sql(accounts: tuple[Account, ...], salt_hex: str, script: str, before: tuple[str, ...] = ()) -> str:
    """One transaction for a roster: `before`, the app's connections without credentials, then each account."""
    names: str = ", ".join(f"'{c}'" for c in CONNECTIONS)
    lines: list[str] = [f"-- Rendered by {script}.py. Do not edit.", "BEGIN;", *before]
    lines += [
        "-- The app's connections keep no credentials of a person.",
        "DELETE FROM guacamole_connection_parameter WHERE parameter_name IN ('username', 'password')",
        f"  AND connection_id IN (SELECT connection_id FROM guacamole_connection WHERE connection_name IN ({names}) AND parent_id IS NULL);",
    ]
    for account in accounts:
        # A salt of its own per account, derived from the run's salt.
        lines += account_sql(account, hashlib.sha256((salt_hex + account.guac_user).encode()).hexdigest(), script)
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"
