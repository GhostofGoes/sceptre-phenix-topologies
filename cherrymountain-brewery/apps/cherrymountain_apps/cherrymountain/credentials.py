"""Range-only credentials for the services the app provisions.

A credential set in the app metadata wins. Every other one is generated the
first time the app needs it and kept in ``<experiment dir>/cherrymountain/
secrets.json`` (mode 0600), so a restart of the experiment, which re-renders
every injection, gives the VMs the same passwords their services already use.
Values never reach a log line or an exception message.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import string
from collections.abc import Callable
from pathlib import Path
from typing import Final

from pydantic import BaseModel, SecretStr, field_validator

from cherrymountain_apps.cherrymountain.metadata import (
    WAZUH_PASSWORD_SYMBOLS,
    Secrets,
    malcolm_password_problem,
    wazuh_password_problem,
)

#: Characters of entropy for generated values (token_urlsafe bytes).
TOKEN_BYTES: Final[int] = 24
#: The characters of a console password (credentials.console_password).
ALNUM_CHARS: Final[str] = string.ascii_letters + string.digits


class RangeSecrets(BaseModel):
    """Every credential the app renders, resolved."""

    openplc_password: SecretStr
    fuxa_password: SecretStr
    fuxa_jwt_secret: SecretStr
    fuxa_operator_password: SecretStr
    influx_password: SecretStr
    influx_token: SecretStr
    grafana_password: SecretStr
    guacamole_password: SecretStr
    wazuh_enrollment: SecretStr
    malcolm_password: SecretStr
    wazuh_password: SecretStr
    vendor_guac_password: SecretStr
    vendor_totp_secret: SecretStr
    vendor_ews_password: SecretStr

    @field_validator("wazuh_password")
    @classmethod
    def _wazuh_policy(cls, value: SecretStr) -> SecretStr:
        # A stored value too: a hand-edited secrets.json must not reach the VM.
        if wazuh_password_problem(value.get_secret_value()):
            raise ValueError(
                "wazuh_password in secrets.json does not meet the Wazuh password "
                "policy; delete it to regenerate"
            )
        return value

    @field_validator("malcolm_password")
    @classmethod
    def _malcolm_policy(cls, value: SecretStr) -> SecretStr:
        if malcolm_password_problem(value.get_secret_value()):
            raise ValueError(
                "malcolm_password in secrets.json is not usable as the Malcolm "
                "password; delete it to regenerate"
            )
        return value


def totp_secret() -> str:
    """An RFC 6238 key: 20 random bytes in Base32, as Guacamole stores it."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def console_password(length: int = 24, symbols: str = "") -> str:
    """A password for a web console that a person types in. It is made of
    letters and digits, with at least one lower case letter, one upper case
    letter, and one digit, and one of ``symbols`` when any are given; a symbol is
    never first or last (a leading ``-`` would pass for an option in a command
    line, and a trailing ``.`` is lost when the password is copied from text)."""
    middle: list[str] = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
    ]
    if symbols:
        middle.append(secrets.choice(symbols))
    middle += [
        secrets.choice(ALNUM_CHARS + symbols) for _ in range(length - 2 - len(middle))
    ]
    secrets.SystemRandom().shuffle(middle)
    return secrets.choice(ALNUM_CHARS) + "".join(middle) + secrets.choice(ALNUM_CHARS)


def malcolm_password() -> str:
    return console_password()


def wazuh_password() -> str:
    """Within what the Wazuh passwords tool accepts (metadata.WAZUH_PASSWORD_*)."""
    return console_password(symbols=WAZUH_PASSWORD_SYMBOLS)


#: Generators for values that are not plain tokens.
GENERATORS: Final[dict[str, Callable[[], str]]] = {
    "vendor_totp_secret": totp_secret,
    "malcolm_password": malcolm_password,
    "wazuh_password": wazuh_password,
}


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd: int = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    path.chmod(0o600)


def resolve(configured: Secrets, store: Path) -> RangeSecrets:
    """The configured values, else the stored ones, else new ones (stored)."""
    stored: dict[str, str] = {}
    if store.is_file():
        try:
            loaded: object = json.loads(store.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError(
                f"{store.name} in the experiment directory is not valid JSON; delete it to regenerate"
            ) from None
        if isinstance(loaded, dict):
            stored = {k: v for k, v in loaded.items() if isinstance(v, str)}

    values: dict[str, SecretStr] = {}
    changed: bool = False
    for name in RangeSecrets.model_fields:
        given: SecretStr | None = getattr(configured, name)
        if given is not None:
            values[name] = given
        elif name in stored:
            values[name] = SecretStr(stored[name])
        else:
            make: Callable[[], str] = GENERATORS.get(
                name, lambda: secrets.token_urlsafe(TOKEN_BYTES)
            )
            stored[name] = make()
            values[name] = SecretStr(stored[name])
            changed = True
    if changed:
        _write_private(store, json.dumps(stored, indent=2, sort_keys=True) + "\n")
    return RangeSecrets.model_validate(values)


def write_private(path: Path, text: str) -> Path:
    """Write a rendered file that holds a credential, readable by its owner only."""
    _write_private(path, text)
    return path


def host_keys(store: Path, hosts: list[str], make: Callable[[], str]) -> dict[str, str]:
    """Per-host secrets (the WireGuard private keys), kept in ``store`` (0600)
    so a restart of the experiment renders the same ones."""
    stored: dict[str, str] = {}
    if store.is_file():
        try:
            loaded: object = json.loads(store.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise ValueError(
                f"{store.name} in the experiment directory is not valid JSON; delete it to regenerate"
            ) from None
        if isinstance(loaded, dict):
            stored = {k: v for k, v in loaded.items() if isinstance(v, str)}
    missing: list[str] = [h for h in hosts if h not in stored]
    for host in missing:
        stored[host] = make()
    if missing:
        _write_private(store, json.dumps(stored, indent=2, sort_keys=True) + "\n")
    return {h: stored[h] for h in hosts}
