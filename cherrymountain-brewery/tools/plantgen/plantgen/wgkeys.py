"""Deterministic, range-only WireGuard key pairs (DESIGN.md §13).

plantgen output is committed and ``--check`` regenerates it, so the keys
cannot be random. Each private key is derived from a public seed string and
the host name (SHA-256, clamped the way ``wg genkey`` clamps), and the public
key is the X25519 base-point multiple (RFC 7748). Anyone can recompute these
keys from plant.yaml: they are throwaway secrets for an isolated range and
must never be used anywhere else.

The X25519 ladder is the RFC 7748 §5 reference algorithm in pure Python, so
plantgen needs no crypto dependency. The tests check it against the RFC
vectors.
"""

from __future__ import annotations

import base64
import hashlib
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

_P: int = 2**255 - 19
_A24: int = 121665
_BASE_POINT: bytes = (9).to_bytes(32, "little")


def _clamp(k: bytes) -> int:
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def x25519(scalar: bytes, u: bytes) -> bytes:
    """RFC 7748 X25519(k, u) with the Montgomery ladder."""
    if len(scalar) != 32 or len(u) != 32:
        raise ValueError("X25519 takes 32-byte inputs")
    k = _clamp(scalar)
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in reversed(range(255)):
        bit = (k >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


class KeyPair(BaseModel):
    """A WireGuard key pair, base64 as ``wg`` prints it."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    private_key: str
    public_key: str


def public_key(private_b64: str) -> str:
    """The public key of a base64 private key (``wg pubkey``)."""
    priv = base64.b64decode(private_b64)
    return base64.b64encode(x25519(priv, _BASE_POINT)).decode()


def derive(seed: str, host: str) -> KeyPair:
    """The range key pair of ``host`` under ``seed``."""
    raw = bytearray(hashlib.sha256(f"{seed}/{host}".encode()).digest())
    raw[0] &= 248
    raw[31] &= 127
    raw[31] |= 64
    priv = base64.b64encode(bytes(raw)).decode()
    return KeyPair(private_key=priv, public_key=public_key(priv))
