"""The vendor path on the endpoints: TOTP, the Guacamole protocol, the EWS tool (DESIGN.md 13)."""

from __future__ import annotations

import base64
import hashlib
import json
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
    # Named accounts (DESIGN.md 13): the Wazuh rules 100400 to 100402 match the prefix.
    assert sql_view.VENDOR_EWS_USER == sql_view.VENDOR_GUAC_USER == "ridgeline-akim"
    assert "VENDOR_TECH_COUNT" in hook and "VENDOR_TECH${n}_USER" in hook


def test_every_technician_has_a_named_account_and_own_totp() -> None:
    """One Guacamole account, TOTP key, and EWS account per technician, never shared."""
    vendor = cm_vendor_sql.Vendor.from_file(RIDGELINE)
    techs = vendor.technicians
    assert len(techs) == 2
    assert all(t.guac_user.startswith("ridgeline-") and t.ews_user == t.guac_user for t in techs)
    assert len({t.totp_secret for t in techs}) == len(techs)
    assert len({t.guac_password for t in techs}) == len(techs)


def test_the_vendor_keys_must_be_the_first_technicians(tmp_path: Path) -> None:
    bad = tmp_path / "bad.env"
    bad.write_text(RIDGELINE.read_text().replace("VENDOR_GUAC_USER=ridgeline-akim", "VENDOR_GUAC_USER=ridgeline-jdiaz"))
    with pytest.raises(ValueError, match="first technician"):
        cm_vendor_sql.Vendor.from_file(bad)


def test_a_file_without_a_roster_is_one_technician(tmp_path: Path) -> None:
    lines = [line for line in RIDGELINE.read_text().splitlines() if not line.startswith("VENDOR_TECH")]
    old = tmp_path / "old.env"
    old.write_text("\n".join(lines) + "\n")
    vendor = cm_vendor_sql.Vendor.from_file(old)
    assert [t.guac_user for t in vendor.technicians] == [vendor.VENDOR_GUAC_USER]


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
    """Each technician gets copies of the app's two connections, holding that
    technician's EWS account; the app's own keep no vendor credentials, and the
    shared login of earlier versions is deleted."""
    vendor = cm_vendor_sql.Vendor.from_file(RIDGELINE)
    rendered = cm_vendor_sql.sql(vendor, "ab" * 32)
    for connection in ("ews-rdp", "ews-ssh"):
        assert f"connection_name = '{connection}'" in rendered
        for tech in vendor.technicians:
            assert f"c.connection_name = '{connection}-{tech.guac_user}'" in rendered
    assert "DELETE FROM guacamole_entity WHERE name = 'ridgeline-tech'" in rendered
    assert "parameter_name IN ('username', 'password')" in rendered
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


class FakeRuntime(cm_plc_tool.Runtime):
    """A runtime whose compilation-status replies come from a list."""

    POLL_S = 0.0
    STALE_GRACE_S = 3600.0

    def __init__(self, replies: list[str]) -> None:
        super().__init__(cm_plc_tool.Plc(name="plc-cellar", host="10.20.10.12"), None)  # type: ignore[arg-type]
        self.replies: list[str] = replies
        self.polls: int = 0

    def call(self, path: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> str:
        assert path == "compilation-status"
        self.polls += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def test_a_stale_success_right_after_upload_is_not_trusted() -> None:
    """compilation-status may still show the previous build's SUCCESS after an
    upload; the tool waits for this build's busy state before it believes one."""
    runtime = FakeRuntime(['{"status": "SUCCESS"}', '{"status": "SUCCESS"}', '{"status": "COMPILING"}', '{"status": "SUCCESS"}'])
    runtime.wait_compiled(timeout=10)
    assert runtime.polls == 4


def test_a_stale_failure_does_not_abort_and_a_real_one_does() -> None:
    runtime = FakeRuntime(['{"status": "FAILED"}', '{"status": "UNZIPPING"}', '{"status": "SUCCESS"}'])
    runtime.wait_compiled(timeout=10)
    failing = FakeRuntime(['{"status": "COMPILING"}', '{"status": "FAILED"}'])
    with pytest.raises(RuntimeError, match="failed to compile"):
        failing.wait_compiled(timeout=10)


def test_success_is_accepted_after_the_grace_without_a_busy_state() -> None:
    runtime = FakeRuntime(['{"status": "SUCCESS"}'])
    runtime.STALE_GRACE_S = 0.0
    runtime.wait_compiled(timeout=10)
    assert runtime.polls == 1


def test_json_report(capsys: pytest.CaptureFixture[str]) -> None:
    plc = cm_plc_tool.Plc(name="plc-cellar", host="10.20.10.12")
    cm_plc_tool.report(True, "status", plc, "STATUS:RUNNING")
    cm_plc_tool.report(True, "upload", plc, "", "TimeoutError")
    first, second = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert first == {"action": "status", "plc": "plc-cellar", "host": "10.20.10.12", "ok": True, "status": "STATUS:RUNNING", "error": ""}
    assert second["ok"] is False and second["error"] == "TimeoutError"


class ChunkedSocket:
    """recv() hands out the chunks in order; a None chunk is a socket timeout."""

    def __init__(self, chunks: list[bytes | None]) -> None:
        self.chunks: list[bytes | None] = chunks

    def recv(self, size: int) -> bytes:
        chunk = self.chunks.pop(0)
        if chunk is None:
            raise TimeoutError
        return chunk


def test_a_timeout_mid_frame_does_not_desync_the_reader() -> None:
    """The hold loop polls with a 5 s timeout. A timeout after the frame header has
    arrived must not drop the header: the next receive() resumes the same frame."""
    big = b"4.sync,8.12345678;" * 10  # 180 bytes: a 16-bit length field
    frame1 = bytes([0x81, 126]) + struct.pack(">H", len(big)) + big
    frame2 = bytes([0x81, 5]) + b"0.,1.;"[:5]
    ws = object.__new__(cm_vendor_session.WebSocket)
    ws.buffer = b""
    ws.sock = ChunkedSocket([frame1[:3], None, frame1[3:50], None, frame1[50:] + frame2])  # type: ignore[assignment]
    with pytest.raises(TimeoutError):
        ws.receive()
    with pytest.raises(TimeoutError):
        ws.receive()
    assert ws.receive() == (0x1, big)
    assert ws.receive() == (0x1, b"0.,1.")
