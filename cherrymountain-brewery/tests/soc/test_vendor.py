"""The vendor path on the endpoints: TOTP, the Guacamole protocol, the EWS tool (DESIGN.md 13)."""

from __future__ import annotations

import base64
import hashlib
import struct
from pathlib import Path

import cm_plc_tool
import cm_vendor_session
import cm_vendor_sql
import pytest

from soc_paths import INJECTS, repo  # noqa: F401 (the fixtures are used by name)

RIDGELINE: Path = INJECTS / "guacamole/ridgeline.env"


def test_totp_matches_the_rfc_6238_vector() -> None:
    # RFC 6238 appendix B: the SHA-1 secret "12345678901234567890", time 59, 8 digits -> 94287082.
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert cm_vendor_session.totp(secret, 59, digits=8) == "94287082"
    assert cm_vendor_session.totp(secret, 59) == "287082"
    assert cm_vendor_session.totp(secret, 1111111109, digits=8) == "07081804"


def test_totp_changes_every_thirty_seconds() -> None:
    secret = cm_vendor_session.Vendor.from_file(RIDGELINE).VENDOR_TOTP_SECRET
    assert cm_vendor_session.totp(secret, 3000) == cm_vendor_session.totp(secret, 3029)
    assert cm_vendor_session.totp(secret, 3029) != cm_vendor_session.totp(secret, 3030)


def test_instruction_round_trip() -> None:
    text = cm_vendor_session.encode_instruction("mouse", "100", "120", "0")
    assert text == "5.mouse,3.100,3.120,1.0;"
    assert cm_vendor_session.decode_instructions(text) == [["mouse", "100", "120", "0"]]


def test_the_tunnel_id_is_an_instruction_with_an_empty_opcode() -> None:
    stream = "0.,36.52871880-4953-3c46-9b3a-a2ed9b9cbc89;4.sync,8.13276008,1.0;3.end,1.1;"
    parsed = cm_vendor_session.decode_instructions(stream)
    assert parsed[0] == ["", "52871880-4953-3c46-9b3a-a2ed9b9cbc89"]
    assert parsed[1] == ["sync", "13276008", "0"]
    assert parsed[2] == ["end", "1"]


def test_an_incomplete_tail_is_ignored() -> None:
    assert cm_vendor_session.decode_instructions("4.sync,8.1327") == []


def test_length_counts_characters_not_bytes() -> None:
    assert cm_vendor_session.decode_instructions("4.test,2.é!;") == [["test", "é!"]]


class FakeSocket:
    """Stands in for the TLS socket: what a client sends is what a server would read."""

    def __init__(self) -> None:
        self.sent: bytes = b""

    def sendall(self, data: bytes) -> None:
        self.sent += data


def test_client_frames_are_masked_and_decode_back() -> None:
    client = object.__new__(cm_vendor_session.WebSocket)
    client.sock = FakeSocket()  # type: ignore[assignment]
    client.buffer = b""
    message = encode = cm_vendor_session.encode_instruction("sync", "13276008").encode()
    client.send(0x1, message)
    frame = client.sock.sent  # type: ignore[attr-defined]
    assert frame[0] == 0x81  # FIN and text
    assert frame[1] & 0x80, "client frames must be masked"
    server = object.__new__(cm_vendor_session.WebSocket)
    server.buffer = frame
    assert server.receive() == (0x1, encode)


def test_a_long_frame_uses_the_extended_length() -> None:
    client = object.__new__(cm_vendor_session.WebSocket)
    client.sock = FakeSocket()  # type: ignore[assignment]
    client.buffer = b""
    client.send(0x1, b"x" * 70000)
    frame = client.sock.sent  # type: ignore[attr-defined]
    assert frame[1] & 0x7F == 127
    assert struct.unpack(">Q", frame[2:10])[0] == 70000


def test_vendor_credentials_agree_across_the_three_consumers() -> None:
    """ridgeline.env is injected into dmz-jump, ews, and vendor-ws; each reads what it needs from it."""
    sql_view = cm_vendor_sql.Vendor.from_file(RIDGELINE)
    laptop_view = cm_vendor_session.Vendor.from_file(RIDGELINE)
    assert sql_view.VENDOR_GUAC_USER == laptop_view.VENDOR_GUAC_USER
    assert sql_view.VENDOR_GUAC_PASSWORD == laptop_view.VENDOR_GUAC_PASSWORD
    assert sql_view.VENDOR_TOTP_SECRET == laptop_view.VENDOR_TOTP_SECRET
    hook = (INJECTS / "ews/70-ews-vendor.sh").read_text()
    assert "VENDOR_EWS_USER" in hook and "VENDOR_EWS_PASSWORD" in hook
    assert sql_view.VENDOR_EWS_USER == "ridgeline"  # the account the Wazuh rules 100400 to 100402 name


def test_the_password_hash_is_guacamoles() -> None:
    # Guacamole stores SHA-256 of the password followed by the upper-case hex of the salt.
    salt = bytes.fromhex("ab" * 32)
    expected = hashlib.sha256(b"pw" + salt.hex().upper().encode()).hexdigest()
    assert cm_vendor_sql.password_hash("pw", salt.hex()) == expected
    # The cherrymountain app writes the same value for cmadmin (apps/.../render.py).
    assert cm_vendor_sql.password_hash("pw", salt.hex()) == hashlib.sha256(("pw" + salt.hex().upper()).encode()).hexdigest()


def test_sql_rejects_unsafe_values(tmp_path: Path) -> None:
    bad = tmp_path / "bad.env"
    bad.write_text(RIDGELINE.read_text().replace("VENDOR_EWS_PASSWORD=", "VENDOR_EWS_PASSWORD=x';DROP TABLE guacamole_user;--"))
    with pytest.raises(ValueError):
        cm_vendor_sql.Vendor.from_file(bad)


def test_sql_names_the_connections_the_app_creates(repo: Path) -> None:
    rendered = cm_vendor_sql.sql(cm_vendor_sql.Vendor.from_file(RIDGELINE), "ab" * 32)
    for connection in ("ews-rdp", "ews-ssh"):
        assert f"connection_name = '{connection}'" in rendered
    assert "guac-totp-key-secret" in rendered and "guac-totp-key-confirmed" in rendered
    assert rendered.startswith("-- Rendered") and rendered.rstrip().endswith("COMMIT;")


def test_plc_tool_settings_come_from_the_file_or_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = tmp_path / "cm-ews"
    settings.write_text("OPENPLC_USER=admin\nOPENPLC_PASSWORD=secret-from-file\n")
    monkeypatch.delenv("OPENPLC_PASSWORD", raising=False)
    assert cm_plc_tool.Settings.load(settings).OPENPLC_PASSWORD == "secret-from-file"
    monkeypatch.setenv("OPENPLC_PASSWORD", "secret-from-env")
    assert cm_plc_tool.Settings.load(settings).OPENPLC_PASSWORD == "secret-from-env"
    monkeypatch.delenv("OPENPLC_PASSWORD")
    with pytest.raises(ValueError):
        cm_plc_tool.Settings.load(tmp_path / "missing")


def test_plc_tool_validates_the_target() -> None:
    assert cm_plc_tool.Plc(name="plc-cellar", host="10.20.10.12").base == "https://10.20.10.12:8443/api"
    with pytest.raises(ValueError):
        cm_plc_tool.Plc(name="plc-cellar", host="plc-cellar.example.com")


def test_plc_tool_log_line_matches_the_wazuh_decoder() -> None:
    sample = next(line for line in (INJECTS / "wazuh/samples.txt").read_text().splitlines() if "plc_upload" in line)
    assert "event=plc_upload plc=plc-cellar host=10.20.10.12 user=ridgeline" in sample
