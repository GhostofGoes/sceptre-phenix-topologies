"""The web console passwords of Malcolm and Wazuh come from the cherrymountain app
(DESIGN.md 14): the provisioning scripts take them from the environment their units
read, set them on the services, and keep the console path (the root-only files on the
VMs) working. No VM, Malcolm, or Wazuh runs here: the scripts run in a temporary
directory against stand-ins for the tools they call.

The Wazuh stand-in implements what ``wazuh-passwords-tool.sh`` 4.14 does with its
arguments, from the pinned file (sha256 ``2d085fe9...``, ``packages.wazuh.com/4.14``):

* ``-a`` generates a password for every user; ``-f FILE`` then replaces the ones the
  file names (``passwords_readFileUsers``, the ``changeall`` branch), and the tool
  prints ``The password for user <user> is <password>`` for each
  (``passwords_runSecurityAdmin``);
* a file is accepted only when it matches the pattern of ``passwords_readFileUsers``
  and its passwords satisfy ``passwords_checkPassword``.

Whether the real tool and a real indexer behave so is what the first boot shows.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from soc_paths import INJECTS

MALCOLM: Path = INJECTS / "malcolm"
WAZUH: Path = INJECTS / "wazuh"

# --------------------------------------------------------------------- Malcolm


def malcolm_script() -> str:
    return (MALCOLM / "cm-malcolm-provision").read_text()


def credentials_block() -> str:
    """The credentials step of cm-malcolm-provision, cut out of the script."""
    m = re.search(r"^password=\"\$\{MALCOLM_ADMIN_PASSWORD:-\}\"\n.*?^htpasswd_hash=.*?\n", malcolm_script(), re.MULTILINE | re.DOTALL)
    assert m is not None
    return m.group(0)


#: openssl and htpasswd as the step calls them: the password comes on stdin, and a
#: stand-in prints a value that holds it, so the test can tell which one was hashed.
OPENSSL: str = """#!/bin/sh
case "$1" in
  rand) printf '%s\\n' 'aB3/+=cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3wX4yZ5' ;;
  passwd) pw=$(cat); printf 'hash6:%s\\n' "$pw" ;;
  *) exit 1 ;;
esac
"""
HTPASSWD: str = """#!/bin/sh
pw=$(cat)
printf 'malcolm:hashB-%s\\n' "$pw"
"""


def run_credentials(tmp: Path, password: str | None) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("openssl", OPENSSL), ("htpasswd", HTPASSWD)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (tmp / "state").mkdir(exist_ok=True)
    script = (
        "set -euo pipefail\n"
        'log() { echo "cm-malcolm-provision: $*"; }\n'
        f"STATE={tmp / 'state'}\n"
        + credentials_block()
        + 'echo "password_hash=${password_hash}"\necho "htpasswd_hash=${htpasswd_hash}"\n'
    )
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    env.pop("MALCOLM_ADMIN_PASSWORD", None)
    if password is not None:
        env["MALCOLM_ADMIN_PASSWORD"] = password
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False, timeout=30)


def test_malcolm_uses_the_apps_password_and_keeps_the_console_copy(tmp_path: Path) -> None:
    app_password = "Kx9qT2mW7vLb4NcR8dEf1ZpA"
    proc = run_credentials(tmp_path, app_password)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Both hashes are of the app's password, and it is in no log line.
    assert f"password_hash=hash6:{app_password}" in proc.stdout
    assert f"htpasswd_hash=hashB-{app_password}" in proc.stdout
    log = "\n".join(line for line in proc.stdout.splitlines() if line.startswith("cm-malcolm-provision:"))
    assert app_password not in log
    assert "generated the admin password" not in log
    # The documented console path: /var/lib/cm-malcolm/admin-password, root only.
    copy = tmp_path / "state" / "admin-password"
    assert copy.read_text() == app_password + "\n"
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600


def test_malcolm_without_the_app_generates_a_password_as_before(tmp_path: Path) -> None:
    proc = run_credentials(tmp_path, None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # openssl rand -base64 24 | tr -d '/+=' | cut -c1-20
    generated = "aB3cD4eF5gH6iJ7kL8mN"
    assert (tmp_path / "state" / "admin-password").read_text() == generated + "\n"
    assert f"password_hash=hash6:{generated}" in proc.stdout
    assert "generated the admin password" in proc.stdout
    assert generated not in "\n".join(line for line in proc.stdout.splitlines() if "cm-malcolm-provision:" in line)


@pytest.mark.parametrize("password", ["Sh0rt.1", "A" * 73], ids=["too-short", "too-long-for-bcrypt"])
def test_malcolm_refuses_a_password_it_cannot_hash(tmp_path: Path, password: str) -> None:
    proc = run_credentials(tmp_path, password)
    assert proc.returncode == 1
    assert "must have 8 to 72 characters" in proc.stdout
    assert password not in proc.stdout + proc.stderr
    assert not (tmp_path / "state" / "admin-password").exists()


def test_malcolm_passes_the_password_to_auth_setup_in_a_form_a_dash_cannot_break() -> None:
    """argparse takes ``--opt -value`` for an option and then complains that the
    option has no argument; ``--opt=value`` takes any value."""
    script = malcolm_script()
    assert '--auth-arkime-password="$password"' in script
    assert not re.search(r'--auth-arkime-password\s+"', script)


def environment_files(unit: Path) -> list[str]:
    return re.findall(r"^EnvironmentFile=-?(\S+)$", unit.read_text(), re.MULTILINE)


def test_only_the_provisioning_unit_reads_the_malcolm_credentials() -> None:
    """The password file is for cm-malcolm-provision.service alone, and it is read
    after the topology's settings file, so it wins."""
    readers = {
        unit.name: environment_files(unit)
        for unit in MALCOLM.glob("*.service")
        if "/etc/default/cm-malcolm-credentials" in environment_files(unit)
    }
    assert readers == {"cm-malcolm-provision.service": ["/etc/default/cm-malcolm", "/etc/default/cm-malcolm-credentials"]}


# ---------------------------------------------------------------------- Wazuh


def wazuh_script() -> str:
    return (WAZUH / "cm-wazuh-provision").read_text()


def segment(text: str, start: str, end: str) -> str:
    """The text from the line that starts with ``start`` up to, not including, the
    line that starts with ``end``."""
    m = re.search(rf"^{re.escape(start)}.*?(?=^{re.escape(end)})", text, re.MULTILINE | re.DOTALL)
    assert m is not None, start
    return m.group(0)


def password_step_parts() -> tuple[str, str, str]:
    """The pieces of cm-wazuh-provision the password step is made of."""
    text = wazuh_script()
    helpers = segment(text, "log() {", "if [ -f \"${STATE}/provisioned\" ]")
    early = segment(text, "if passwords_changed; then", "# --- 1. certificates")
    step = segment(text, "# admin_login_ok:", "# The tool waits only 60 s")
    return helpers, early, step


#: The patterns of wazuh-passwords-tool.sh 4.14: passwords_checkPassword's greps, and
#: the file check of passwords_readFileUsers.
FAKE_TOOL: str = r"""#!/bin/bash
# A stand-in for wazuh-passwords-tool.sh 4.14 (see the test module).
printf '%s\n' "$*" >> "$FAKE_TOOL_ARGV"
file=""
api=""
all=""
while [ -n "${1:-}" ]; do
  case "$1" in
    -A) api=1; shift ;;
    -a) all=1; shift ;;
    -au|-ap) shift 2 ;;
    -f) file="$2"; shift 2 ;;
    *) echo "help"; exit 1 ;;
  esac
done
[ -n "$api" ] && [ -n "$all" ] || { echo "help"; exit 1; }
admin="admin-generated-AAAA.1"
if [ -n "$file" ]; then
  stat -c %a "$file" > "$FAKE_TOOL_FILE_MODE"
  ok=$(grep -Ev '^#|^\s*$' "$file" | grep -Pzc "\A(\s*(indexer_username|api_username|indexer_password|api_password):[ \t]+[\'\"]?[\w.*+?-]+[\'\"]?)+\Z")
  [ "$ok" -eq 1 ] || { echo "The password file does not have a correct format"; exit 1; }
  user=$(grep indexer_username: "$file" | awk '{ print $2 }' | sed -e "s/[\'\"]//g")
  pw=$(grep indexer_password: "$file" | awk '{ print $2 }' | sed -e "s/[\'\"]//g")
  if ! echo "$pw" | grep -q "[A-Z]" || ! echo "$pw" | grep -q "[a-z]" || ! echo "$pw" | grep -q "[0-9]" \
    || ! echo "$pw" | grep -q "[.*+?-]" || [ "${#pw}" -lt 8 ] || [ "${#pw}" -gt 64 ]; then
    echo "The password must have a length between 8 and 64 characters and contain at least one upper and lower case letter, a number and a symbol(.*+?-)."
    exit 1
  fi
  [ "$user" = admin ] && admin="$pw"
fi
echo "The password for user admin is ${admin}"
echo "The password for user kibanaserver is Kb-generated-BBBB.2"
echo "The password for Wazuh API user wazuh is Wz-generated-CCCC.3"
echo "The password for Wazuh API user wazuh-wui is Wui-generated-DDDD.4"
"""

#: curl as admin_login_ok calls it: the credentials come as a curl config on stdin.
FAKE_CURL: str = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_CURL_ARGV"
cat > "$FAKE_CURL_STDIN"
printf '%s' "${FAKE_CURL_CODE:-200}"
"""

POLICY_HELP: str = "does not meet the Wazuh password policy"


class Provision:
    """A stand-in wazuh VM directory and the password step of the script."""

    def __init__(self, tmp: Path) -> None:
        self.tmp: Path = tmp
        self.tools: Path = tmp / "tools"
        self.state: Path = tmp / "state"
        bin_dir = tmp / "bin"
        for directory in (self.tools, self.state, bin_dir):
            directory.mkdir()
        (self.tools / "wazuh-passwords-tool.sh").write_text(FAKE_TOOL)
        (bin_dir / "curl").write_text(FAKE_CURL)
        (bin_dir / "curl").chmod(0o755)
        self.env: dict[str, str] = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_TOOL_ARGV": str(tmp / "tool-argv"),
            "FAKE_TOOL_FILE_MODE": str(tmp / "tool-file-mode"),
            "FAKE_CURL_ARGV": str(tmp / "curl-argv"),
            "FAKE_CURL_STDIN": str(tmp / "curl-stdin"),
        }
        self.env.pop("WAZUH_ADMIN_PASSWORD", None)

    def run(self, password: str | None, changed: bool = False, **env: str) -> subprocess.CompletedProcess[str]:
        helpers, early, step = password_step_parts()
        if changed:
            (self.state / "passwords-changed").write_text("")
        script = "\n".join(
            [
                "set -euo pipefail",
                f"TOOLS={self.tools}",
                f"STATE={self.state}",
                "node_ip=10.40.10.11",
                # wait_for sleeps and reads $SECONDS: let a sleep move the clock.
                "sleep() { SECONDS=$((SECONDS + $1)); }",
                'PASSWORDS_MARKER="${STATE}/passwords-changed"',
                helpers,
                'passwords_changed() { [ -f "$PASSWORDS_MARKER" ]; }',
                early,
                step,
                'echo "reached the end"',
            ]
        )
        merged = {**self.env, **env}
        if password is not None:
            merged["WAZUH_ADMIN_PASSWORD"] = password
        return subprocess.run(["bash", "-c", script], env=merged, capture_output=True, text=True, check=False, timeout=60)

    def tool_calls(self) -> list[str]:
        path = self.tmp / "tool-argv"
        return path.read_text().splitlines() if path.exists() else []

    def passwords_txt(self) -> str:
        return (self.state / "passwords.txt").read_text()


@pytest.fixture
def provision(tmp_path: Path) -> Provision:
    return Provision(tmp_path)


APP_PASSWORD: str = "Wz7.kQ2mLx9-Tb4Rc8NdEf1Zp"


def test_the_admin_user_gets_the_apps_password(provision: Provision) -> None:
    proc = provision.run(APP_PASSWORD)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The tool ran once, with its usual flags and a file, and the password is on no command line.
    (call,) = provision.tool_calls()
    assert call.startswith("-A -a -au wazuh -ap wazuh -f ")
    assert call.endswith(str(provision.state / "admin-password.yml"))
    assert APP_PASSWORD not in call
    # The file was private when the tool read it, and it is gone now.
    assert (provision.tmp / "tool-file-mode").read_text().strip() == "600"
    assert not (provision.state / "admin-password.yml").exists()
    # The console path: passwords.txt has the app's password for admin, the tool's for the rest.
    text = provision.passwords_txt()
    assert f"The password for user admin is {APP_PASSWORD}\n" in text
    assert "admin-generated" not in text
    assert "The password for Wazuh API user wazuh-wui is Wui-generated-DDDD.4" in text
    assert (provision.state / "passwords-changed").exists()
    assert "reached the end" in proc.stdout
    # No log line shows it, and the check that the indexer takes it keeps it off curl's command line.
    assert APP_PASSWORD not in proc.stdout + proc.stderr
    curl_args = (provision.tmp / "curl-argv").read_text()
    assert APP_PASSWORD not in curl_args
    assert "-K -" in curl_args
    assert (provision.tmp / "curl-stdin").read_text() == f'user = "admin:{APP_PASSWORD}"\n'
    assert "the admin user signs in with the app's password" in proc.stdout


def test_without_the_apps_password_the_tool_runs_as_before(provision: Provision) -> None:
    proc = provision.run(None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert provision.tool_calls() == ["-A -a -au wazuh -ap wazuh"]
    assert "The password for user admin is admin-generated-AAAA.1" in provision.passwords_txt()
    assert (provision.state / "passwords-changed").exists()
    assert not (provision.tmp / "curl-argv").exists()
    assert not (provision.state / "admin-password.yml").exists()


def test_a_run_after_the_passwords_changed_leaves_them_alone(provision: Provision) -> None:
    (provision.state / "passwords.txt").write_text("The password for user admin is kept\n")
    proc = provision.run(APP_PASSWORD, changed=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert provision.tool_calls() == []
    assert (provision.state / "passwords.txt").read_text() == "The password for user admin is kept\n"
    assert "already changed" in proc.stdout


def test_a_login_check_that_fails_warns_and_does_not_fail_the_step(provision: Provision) -> None:
    proc = provision.run(APP_PASSWORD, FAKE_CURL_CODE="401")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WARNING: the indexer does not accept the app's admin password" in proc.stdout
    assert str(provision.state / "passwords.txt") in proc.stdout
    assert APP_PASSWORD not in proc.stdout + proc.stderr


#: Passwords the tool or the script's use of it would refuse or mishandle.
BAD_PASSWORDS: dict[str, str] = {
    "too-short": "Ab1.xyz",
    "too-long": "Ab1." + "x" * 61,
    "no-upper": "wz7.kq2mlx9-tb4rc8",
    "no-lower": "WZ7.KQ2MLX9-TB4RC8",
    "no-digit": "Wz.kQmLx-TbRcNdEf",
    "no-symbol": "Wz7kQ2mLx9Tb4Rc8NdEf",
    "underscore-is-no-symbol": "Wz7_kQ2mLx9_Tb4Rc8",
    "glob-star": "Wz7.kQ2*Lx9-Tb4",
    "glob-question": "Wz7.kQ2?Lx9-Tb4",
    "leading-dash": "-Wz7.kQ2mLx9Tb4",
    "space": "Wz7.kQ2 mLx9-Tb4",
    "quote": "Wz7.kQ2'mLx9-Tb4",
    "dollar": "Wz7.kQ2$mLx9-Tb4",
    "semicolon": "Wz7.kQ2;mLx9-Tb4",
    "percent": "Wz7.kQ2%mLx9-Tb4",
}


@pytest.mark.parametrize("password", list(BAD_PASSWORDS.values()), ids=list(BAD_PASSWORDS))
def test_a_password_the_tool_would_refuse_stops_the_script_before_any_change(provision: Provision, password: str) -> None:
    proc = provision.run(password)
    assert proc.returncode == 1
    assert POLICY_HELP in proc.stdout
    assert password not in proc.stdout + proc.stderr
    assert provision.tool_calls() == []
    assert not (provision.state / "passwords-changed").exists()
    assert "reached the end" not in proc.stdout


#: Passwords at the edges of the rule, each symbol the app uses, and the longest.
GOOD_PASSWORDS: dict[str, str] = {
    "app-style": APP_PASSWORD,
    "shortest-with-dot": "Aa1.aaaa",
    "plus": "Aa1+aaaa",
    "dash": "Aa1-aaaa",
    "longest": "Aa1." + "x" * 60,
    "underscore-and-dash": "aA1_Zz_zZ-yY",
}


@pytest.mark.parametrize("password", list(GOOD_PASSWORDS.values()), ids=list(GOOD_PASSWORDS))
def test_every_password_the_script_accepts_passes_the_tools_own_checks(provision: Provision, password: str) -> None:
    """The script's rule and the tool's are not the same text; what the script lets
    through must pass the tool's checks, which the stand-in runs on the file."""
    proc = provision.run(password)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"The password for user admin is {password}\n" in provision.passwords_txt()


def test_the_passwords_step_follows_the_dashboard_and_precedes_the_restart() -> None:
    """Unchanged ordering: the tool restarts the dashboard and edits its wazuh.yml,
    so the dashboard is up first; the tool's file is cleaned up before the marker."""
    text = wazuh_script()
    dashboard = text.index("systemctl enable --now wazuh-dashboard.service")
    tool = text.index('bash "${TOOLS}/wazuh-passwords-tool.sh"')
    cleanup = text.index('\n  rm -f "$password_file"\n')
    marker = text.index('touch "$PASSWORDS_MARKER"')
    restart = text.index("systemctl restart filebeat.service wazuh-manager.service wazuh-dashboard.service")
    assert dashboard < tool < cleanup < marker < restart


def test_the_wazuh_unit_reads_the_apps_file() -> None:
    unit = (WAZUH / "cm-wazuh-provision.service").read_text()
    assert re.findall(r"^EnvironmentFile=-?(\S+)$", unit, re.MULTILINE) == [
        "/etc/default/cm-wazuh",
        "/etc/default/cm-wazuh-manager",
    ]
