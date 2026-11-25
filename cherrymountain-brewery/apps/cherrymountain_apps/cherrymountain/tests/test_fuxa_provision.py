"""cm-fuxa-provision against a fake of the FUXA REST endpoints it calls, and the
operator account's SoH check against the same fake (DESIGN.md 12.2).

The fake (``fuxa_fake.py``) follows the server at the cm-fuxa pin; what these
tests prove is the script's logic: the account it makes, that a second run and a
project re-import leave it alone, that it repairs an account in the wrong group,
and that no password reaches a command line. Whether the real FUXA behaves as the
fake does is for the first boot to show.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from cherrymountain_apps.cherrymountain import soh
from cherrymountain_apps.cherrymountain.tests.conftest import ROOT
from cherrymountain_apps.cherrymountain.tests.fuxa_fake import (
    SEED_PASSWORD,
    FakeFuxa,
    User,
)

SCRIPT: Path = ROOT / "phenix-injects" / "fuxa" / "cm-fuxa-provision.sh"
ADMIN_PASSWORD: str = "admin-range-secret-AAA"
OPERATOR_PASSWORD: str = "operator-range-secret-BBB"

#: A wrapper that records the arguments of the tools the script feeds a secret
#: to, then runs the real one.
SPY: str = """#!/bin/sh
printf '%s\\n' "$*" >> "$CM_ARGV_LOG"
exec "$CM_REAL_{tool}" "$@"
"""


@pytest.fixture
def fuxa() -> Iterator[FakeFuxa]:
    fake: FakeFuxa = FakeFuxa()
    fake.start()
    yield fake
    fake.stop()


def project_file(tmp_path: Path, tags: int = 1) -> Path:
    path: Path = tmp_path / "project.json"
    path.write_text(
        json.dumps(
            {
                "devices": {
                    "d1": {
                        "name": "plc-cellar",
                        "tags": {f"t{i}": {"name": f"T{i}"} for i in range(tags)},
                    }
                }
            }
        )
    )
    return path


class Run:
    """The script on one fake, with a spy on curl and python3."""

    def __init__(self, tmp_path: Path, fuxa: FakeFuxa) -> None:
        self.tmp: Path = tmp_path
        self.fuxa: FakeFuxa = fuxa
        self.argv_log: Path = tmp_path / "argv.log"
        self.argv_log.write_text("")
        bin_dir: Path = tmp_path / "bin"
        bin_dir.mkdir()
        (tmp_path / "tmp").mkdir()
        self.env: dict[str, str] = {
            **os.environ,
            "TMPDIR": str(tmp_path / "tmp"),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "CM_ARGV_LOG": str(self.argv_log),
            "FUXA_URL": fuxa.url,
            "FUXA_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "FUXA_OPERATOR_PASSWORD": OPERATOR_PASSWORD,
            "FUXA_PROJECT": str(project_file(tmp_path)),
            "FUXA_EXPECT_DEVICES": "plc-cellar",
        }
        for tool in ("curl", "python3"):
            real: str | None = shutil.which(tool)
            assert real is not None, tool
            self.env[f"CM_REAL_{tool}"] = real
            wrapper: Path = bin_dir / tool
            wrapper.write_text(SPY.replace("{tool}", tool))
            wrapper.chmod(0o755)
        # The script waits two seconds before it reads a posted project back.
        (bin_dir / "sleep").write_text("#!/bin/sh\nexec /bin/sleep 0.02\n")
        (bin_dir / "sleep").chmod(0o755)

    def __call__(self, **env: str | None) -> subprocess.CompletedProcess[str]:
        merged: dict[str, str] = dict(self.env)
        for key, value in env.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=merged,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )


@pytest.fixture
def run(tmp_path: Path, fuxa: FakeFuxa) -> Run:
    return Run(tmp_path, fuxa)


def test_first_boot_sets_the_admin_and_adds_the_operator(
    run: Run, fuxa: FakeFuxa
) -> None:
    proc = run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert fuxa.users["admin"].password == ADMIN_PASSWORD
    assert fuxa.users["admin"].groups == -1
    operator = fuxa.users["operator"]
    assert operator.password == OPERATOR_PASSWORD
    # FUXA's Operator group (client _models/user.ts UserGroups), not -1 or 255.
    assert operator.groups == 2
    assert operator.fullname == "Operator"
    assert operator.info is not None
    assert json.loads(operator.info) == {}
    assert fuxa.project is not None
    assert "can sign in and cannot use the users API" in proc.stdout


def test_a_second_run_changes_nothing(run: Run, fuxa: FakeFuxa) -> None:
    """A reboot runs the script again on the same users database."""
    assert run().returncode == 0
    posts: tuple[int, int] = (fuxa.user_posts, fuxa.project_posts)
    assert posts == (2, 1), (
        "one post for the admin password, one for the operator, one project"
    )
    again = run()
    assert again.returncode == 0, again.stdout + again.stderr
    assert (fuxa.user_posts, fuxa.project_posts) == posts
    assert "already in place" in again.stdout
    assert "already imported" in again.stdout


def test_a_project_reimport_leaves_the_operator_alone(
    run: Run, fuxa: FakeFuxa, tmp_path: Path
) -> None:
    assert run().returncode == 0
    users_before: int = fuxa.user_posts
    changed = run(FUXA_PROJECT=str(project_file(tmp_path, tags=3)))
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert fuxa.project_posts == 2
    assert fuxa.user_posts == users_before
    assert fuxa.users["operator"].groups == 2
    assert fuxa.users["operator"].password == OPERATOR_PASSWORD


@pytest.mark.parametrize(
    "stale",
    [
        pytest.param(User("an-old-password", 2), id="old-password"),
        pytest.param(User(OPERATOR_PASSWORD, -1), id="administrator-group"),
        pytest.param(User(OPERATOR_PASSWORD, 255), id="other-administrator-group"),
        pytest.param(User(OPERATOR_PASSWORD, 0), id="no-group"),
        pytest.param(User(OPERATOR_PASSWORD, 1), id="viewer-group"),
    ],
)
def test_an_operator_in_the_wrong_state_is_repaired(
    run: Run, fuxa: FakeFuxa, stale: User
) -> None:
    fuxa.users["operator"] = stale
    proc = run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert fuxa.users["operator"].password == OPERATOR_PASSWORD
    assert fuxa.users["operator"].groups == 2


def test_a_rerun_after_an_admin_password_change_still_signs_in(
    run: Run, fuxa: FakeFuxa
) -> None:
    """The seeded password is gone on a re-run: the script signs in with the
    range password and still checks the operator."""
    assert run().returncode == 0
    assert fuxa.users["admin"].password == ADMIN_PASSWORD
    assert SEED_PASSWORD not in (
        fuxa.users["admin"].password,
        fuxa.users["operator"].password,
    )
    assert run().returncode == 0


def test_without_an_operator_password_no_account_is_made(
    run: Run, fuxa: FakeFuxa
) -> None:
    """The ci slice runs the script with the admin password only."""
    proc = run(FUXA_OPERATOR_PASSWORD=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "operator" not in fuxa.users
    assert "no operator account" in proc.stdout
    assert fuxa.project is not None


def test_the_script_fails_when_the_operator_would_be_an_administrator(
    run: Run, fuxa: FakeFuxa
) -> None:
    """A server that lets the account use the users API is a failure the unit
    reports, not a state the script accepts."""
    fuxa.always_admin.add("operator")
    proc = run()
    assert proc.returncode == 1
    assert "must not be an administrator" in proc.stdout
    assert fuxa.project is None


def test_a_users_api_error_fails_the_script_with_the_status(
    run: Run, fuxa: FakeFuxa
) -> None:
    assert run().returncode == 0
    fuxa.users["operator"].password = "changed-elsewhere"
    fuxa.refuse_users = True
    proc = run()
    assert proc.returncode == 1
    assert "FAIL: users API HTTP 500" in proc.stdout


def test_no_password_reaches_a_command_line(run: Run) -> None:
    proc = run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv: str = run.argv_log.read_text()
    assert argv, "the spy saw no call"
    for secret in (ADMIN_PASSWORD, OPERATOR_PASSWORD, SEED_PASSWORD):
        assert secret not in argv
        assert secret not in proc.stdout + proc.stderr


def test_the_operator_body_does_not_outlive_the_script(
    run: Run, tmp_path: Path
) -> None:
    """The body with the password is a file in the script's own temp directory,
    which the script removes."""
    assert run().returncode == 0
    assert list((tmp_path / "tmp").iterdir()) == []


# --------------------------------------------------------------- SoH check


def soh_script(run: Run, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """The fuxa-operator-account test of the SoH profile, with the settings file
    it sources moved into the test directory."""
    env_file: Path = tmp_path / "cm-fuxa"
    env_file.write_text(
        f"FUXA_URL={run.fuxa.url}\nFUXA_OPERATOR_PASSWORD={run.env['FUXA_OPERATOR_PASSWORD']}\n"
    )
    script: str = soh.FUXA_OPERATOR.format(port=1881).replace(
        "/etc/default/cm-fuxa", str(env_file)
    )
    return subprocess.run(
        ["bash", "-c", script],
        env=run.env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_soh_check_passes_on_a_provisioned_operator(run: Run, tmp_path: Path) -> None:
    assert run().returncode == 0
    proc = soh_script(run, tmp_path)
    assert proc.stdout.strip() == "operator-ok", proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("stale", "expected"),
    [
        (User("another", 2), "operator-no-login"),
        (User(OPERATOR_PASSWORD, 4), "operator-wrong-group 4"),
        (User(OPERATOR_PASSWORD, -1), "operator-wrong-group -1"),
    ],
)
def test_soh_check_reports_the_state(
    run: Run, tmp_path: Path, stale: User, expected: str
) -> None:
    assert run().returncode == 0
    run.fuxa.users["operator"] = stale
    assert soh_script(run, tmp_path).stdout.strip() == expected


def test_soh_check_catches_an_account_that_can_use_the_users_api(
    run: Run, tmp_path: Path
) -> None:
    assert run().returncode == 0
    run.fuxa.always_admin.add("operator")
    assert soh_script(run, tmp_path).stdout.strip() == "operator-is-admin 200"


def test_provisioning_script_is_executable_and_keeps_its_shebang() -> None:
    assert SCRIPT.read_text().startswith("#!/usr/bin/env bash\n")
    assert stat.S_IMODE(SCRIPT.stat().st_mode) & 0o111
