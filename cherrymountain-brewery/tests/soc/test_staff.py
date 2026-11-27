"""The accounts of the company's own staff on a site's jump host (docs/multisite/PLANS.md 4.6)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cm_guac_accounts
import cm_staff_sql
import cm_vendor_sql
import pytest

from soc_paths import INJECTS

#: The roster of `ep`, as plantgen writes it for the site's two nodes.
ROSTER: Path = INJECTS / "fleet/ep/guacamole/staff.env"
#: The hand-written file that a site's copy comes from. It lists nobody.
TEMPLATE: Path = INJECTS / "guacamole/staff.env"
SALT: str = "ab" * 32


def staff() -> tuple[cm_guac_accounts.Account, ...]:
    return cm_staff_sql.roster(cm_guac_accounts.read_env(ROSTER))


def test_every_person_has_a_named_account_and_an_own_totp_key() -> None:
    """One Guacamole account, TOTP key, and EWS account for each person, never shared."""
    people = staff()
    assert [person.guac_user for person in people] == ["corp-controls", "ep-maint"]
    assert all(person.ews_user == person.guac_user for person in people)
    assert len({person.totp_secret for person in people}) == len(people)
    assert len({person.guac_password for person in people}) == len(people)
    assert len({person.ews_password for person in people}) == len(people)


def test_no_staff_account_looks_like_a_vendor_account() -> None:
    """The Wazuh rules 100400 to 100402 match the vendor's prefix (DESIGN.md 13)."""
    vendor = cm_vendor_sql.Vendor.from_file(INJECTS / "guacamole/ridgeline.env")
    theirs = {tech.guac_user for tech in vendor.technicians}
    for person in staff():
        assert not person.guac_user.startswith("ridgeline-")
        assert person.guac_user not in theirs


def test_the_sql_gives_each_person_own_copies_of_the_apps_connections() -> None:
    """The copies take every parameter of the app's connection, recording
    included, and hold the person's own EWS account."""
    rendered = cm_staff_sql.sql(staff(), SALT)
    assert rendered.startswith("-- Rendered by cm_staff_sql.py.")
    assert rendered.rstrip().endswith("COMMIT;")
    for person in staff():
        user = person.guac_user
        assert f"VALUES ('{user}', 'USER') ON CONFLICT DO NOTHING;" in rendered
        for connection in cm_guac_accounts.CONNECTIONS:
            assert f"c.connection_name = '{connection}-{user}'" in rendered
            assert f"RAISE EXCEPTION 'cm_staff_sql: {user} has no permission on connection {connection}-{user}';" in rendered
        assert f"SELECT connection_id, 'username', '{user}' FROM guacamole_connection" in rendered
        assert f"'guac-totp-key-secret', '{person.totp_secret}'" in rendered
    assert "WHERE p.parameter_name NOT IN ('username', 'password')" in rendered
    assert "guac-totp-key-confirmed" in rendered
    # The app's own connections keep no credentials, and no account is deleted:
    # the shared login that the vendor's script removes was never on this host.
    assert "DELETE FROM guacamole_connection_parameter WHERE parameter_name IN ('username', 'password')" in rendered
    assert "DELETE FROM guacamole_entity" not in rendered
    assert "ridgeline" not in rendered


def test_the_sql_is_the_vendor_scripts_for_the_same_account() -> None:
    """Both rosters go through one library, so a person of the staff gets what a
    technician gets."""
    person = staff()[0]
    ours = cm_guac_accounts.account_sql(person, SALT, "x")
    assert ours == cm_guac_accounts.account_sql(cm_vendor_sql.Technician(**person.model_dump()), SALT, "x")
    assert cm_vendor_sql.password_hash is cm_guac_accounts.password_hash


def test_the_same_account_twice_is_refused() -> None:
    values = cm_guac_accounts.read_env(ROSTER)
    values["STAFF2_USER"] = values["STAFF1_USER"]
    with pytest.raises(ValueError, match="listed twice"):
        cm_staff_sql.roster(values)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("STAFF1_USER", "x';DROP TABLE guacamole_user;--"),
        ("STAFF1_GUAC_PASSWORD", "pass word"),
        ("STAFF2_EWS_PASSWORD", "x';--"),
        ("STAFF2_TOTP_SECRET", "not-base32"),
    ],
)
def test_the_sql_rejects_unsafe_values(key: str, value: str) -> None:
    values = cm_guac_accounts.read_env(ROSTER)
    values[key] = value
    with pytest.raises(ValueError):
        cm_staff_sql.roster(values)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"STAFF_COUNT": "0"}, "STAFF_COUNT must be 1 to 8"),
        ({"STAFF_COUNT": "9"}, "STAFF_COUNT must be 1 to 8"),
        ({"STAFF_COUNT": "1"}, "does not use the keys"),
        ({"VENDOR_GUAC_USER": "ridgeline-akim"}, "does not use the keys"),
    ],
)
def test_a_roster_that_does_not_fit_its_count_is_refused(change: dict[str, str], message: str) -> None:
    values = cm_guac_accounts.read_env(ROSTER) | change
    with pytest.raises(ValueError, match=message):
        cm_staff_sql.roster(values)


def test_the_hand_written_file_lists_nobody_and_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    """No node injects the template. If one did, the jump host would not start with it."""
    assert cm_guac_accounts.read_env(TEMPLATE) == {"STAFF_COUNT": "0"}
    assert cm_staff_sql.main(["--env", str(TEMPLATE)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "STAFF_COUNT must be 1 to 8" in captured.err


def test_an_error_names_the_key_and_never_the_value(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "staff.env"
    bad.write_text(ROSTER.read_text().replace("STAFF1_GUAC_PASSWORD=", "STAFF1_GUAC_PASSWORD=hunter2 !"))
    assert cm_staff_sql.main(["--env", str(bad)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "guac_password" in captured.err and "hunter2" not in captured.err


def test_the_script_runs_as_the_start_script_runs_it() -> None:
    """cm-guacamole-start runs `python3 -I`, which leaves the script's directory
    out of the module path. The script finds its library next to itself."""
    done = subprocess.run(  # noqa: S603 (a fixed command on files of this repository)
        [sys.executable, "-I", str(INJECTS / "guacamole/cm_staff_sql.py"), "--env", str(ROSTER), "--salt", SALT],
        capture_output=True,
        text=True,
        check=True,
        cwd="/",
    )
    assert done.stdout == cm_staff_sql.sql(staff(), SALT)
    start = (INJECTS / "guacamole/cm-guacamole-start").read_text()
    assert 'python3 -I "${LIB}/cm_staff_sql.py" --env "$STAFF"' in start
    assert 'STAFF="${ETC}/staff.env"' in start
    # A jump host without a roster starts as before.
    assert 'if [ -f "$STAFF" ]; then' in start


def test_the_workstation_hook_reads_the_same_roster() -> None:
    """71-ews-staff.sh gives each person a local account in the engineers' group,
    which may read the PLCs' credential, so cm_plc_tool.py logs the person."""
    hook = (INJECTS / "ews/71-ews-staff.sh").read_text()
    assert "SETTINGS=/etc/cherrymountain/staff.env" in hook
    for key in ("STAFF_COUNT", "STAFF${n}_USER", "STAFF${n}_EWS_PASSWORD"):
        assert key in hook
    assert 'usermod --append --groups "$ENGINEERS" -- "$user"' in hook
    assert 'chgrp -- "$ENGINEERS" "$PLC_CREDENTIAL"' in hook and 'chmod 0640 "$PLC_CREDENTIAL"' in hook
    assert "ENGINEERS=cm-engineers" in hook and "PLC_CREDENTIAL=/etc/default/cm-ews" in hook
    assert "GUAC_PASSWORD" not in hook and "TOTP" not in hook
