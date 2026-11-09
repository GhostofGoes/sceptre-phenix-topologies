"""Range-only credentials for the services the app provisions.

A credential set in the app metadata wins. Every other one is generated the
first time the app needs it and kept in ``<experiment dir>/cherrymountain/
secrets.json`` (mode 0600), so a restart of the experiment, which re-renders
every injection, gives the VMs the same passwords their services already use.
Values never reach a log line or an exception message.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Final

from pydantic import BaseModel, SecretStr

from cherrymountain_apps.cherrymountain.metadata import Secrets

#: Characters of entropy for generated values (token_urlsafe bytes).
TOKEN_BYTES: Final[int] = 24


class RangeSecrets(BaseModel):
    """Every credential the app renders, resolved."""

    openplc_password: SecretStr
    fuxa_password: SecretStr
    fuxa_jwt_secret: SecretStr
    influx_password: SecretStr
    influx_token: SecretStr
    grafana_password: SecretStr
    guacamole_password: SecretStr
    wazuh_enrollment: SecretStr


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
            stored[name] = secrets.token_urlsafe(TOKEN_BYTES)
            values[name] = SecretStr(stored[name])
            changed = True
    if changed:
        _write_private(store, json.dumps(stored, indent=2, sort_keys=True) + "\n")
    return RangeSecrets.model_validate(values)


def write_private(path: Path, text: str) -> Path:
    """Write a rendered file that holds a credential, readable by its owner only."""
    _write_private(path, text)
    return path
