"""The Malcolm and Wazuh web passwords (DESIGN.md 14): generated per experiment
like the other secrets, and within what the services take.

The Wazuh rule is the one of ``wazuh-passwords-tool.sh`` 4.14 (the cm-wazuh pin):
8 to 64 characters with an upper case letter, a lower case letter, a digit, and
one of ``.*+?-`` (``passwords_checkPassword``); the tool reads a password from a
file only when it is made of ``\\w.*+?-`` (``passwords_readFileUsers``) and puts it
unquoted into an ``eval``, so the app leaves out ``*`` and ``?``. The generated
values were also run through the tool's own two functions (see the commit).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cherrymountain_apps.cherrymountain import credentials
from cherrymountain_apps.cherrymountain.metadata import (
    Secrets,
    malcolm_password_problem,
    wazuh_password_problem,
)
from cherrymountain_apps.cherrymountain.tests.conftest import RunApp, app_block

#: The two upstream rules, written out independently of the app's code.
UPSTREAM_FILE_FORMAT: re.Pattern[str] = re.compile(r"[\w.*+?-]+")


def upstream_policy_ok(value: str) -> bool:
    return (
        8 <= len(value) <= 64
        and re.search("[A-Z]", value) is not None
        and re.search("[a-z]", value) is not None
        and re.search("[0-9]", value) is not None
        and re.search(r"[.*+?-]", value) is not None
        and UPSTREAM_FILE_FORMAT.fullmatch(value) is not None
    )


def test_generated_wazuh_passwords_meet_the_tools_policy() -> None:
    seen: set[str] = set()
    for _ in range(500):
        value = credentials.wazuh_password()
        seen.add(value)
        assert upstream_policy_ok(value), "a generated password breaks the tool's rule"
        assert wazuh_password_problem(value) is None
        # Safe where the tool uses it: no glob, no leading dash, no symbol at an end.
        assert not set(value) & set("*?")
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*[A-Za-z0-9]", value)
        assert len(value) == 24
    assert len(seen) == 500


def test_generated_malcolm_passwords_are_plain_and_never_start_with_a_dash() -> None:
    for _ in range(500):
        value = credentials.malcolm_password()
        assert malcolm_password_problem(value) is None
        assert re.fullmatch(r"[A-Za-z0-9]{24}", value)
        assert re.search("[a-z]", value)
        assert re.search("[A-Z]", value)
        assert re.search("[0-9]", value)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("Wz7.kQ2mLx9-Tb4Rc8NdEf1Z", True),
        ("Aa1.aaaa", True),
        ("Aa1+aaaa", True),
        ("Aa1-aaaa", True),
        ("Aa1.aaa", False),
        ("Aa1." + "x" * 61, False),
        ("aa1.aaaa", False),
        ("AA1.AAAA", False),
        ("Aaa.aaaa", False),
        ("Aa1aaaaa", False),
        ("Aa1_aaaa", False),
        ("Aa1*aaaa", False),
        ("Aa1?aaaa", False),
        ("-Aa1.aaaa", False),
        ("Aa1.aaa~", False),
        ("Aa1.aaa%", False),
        ("Aa1.aaa a", False),
    ],
)
def test_the_wazuh_policy_is_the_tools_cut_to_what_is_safe(
    value: str, ok: bool
) -> None:
    assert (wazuh_password_problem(value) is None) is ok
    if ok:
        assert upstream_policy_ok(value)


def test_policy_messages_never_hold_the_value() -> None:
    for value in ("short", "has space 1A.", "-leading1A.", "nodigitsHere."):
        for problem in (wazuh_password_problem(value), malcolm_password_problem(value)):
            assert problem is None or value not in problem


def test_metadata_refuses_a_wazuh_password_the_tool_would_refuse() -> None:
    assert (
        Secrets.model_validate({"wazuhPassword": "Wz7.kQ2mLx9-Tb4Rc8"}).wazuh_password
        is not None
    )
    for bad in (
        "Short1.",
        "alllowercase1.x",
        "NoSymbol123456",
        "Has~tilde1A.xx",
        "Star*1Aaaaaaaa",
    ):
        with pytest.raises(ValidationError) as ex:
            Secrets.model_validate({"wazuhPassword": bad})
        (error,) = ex.value.errors()
        assert error["loc"] == ("wazuhPassword",)
        assert "Wazuh" in error["msg"] or "secret may use only" in error["msg"]
        assert bad not in error["msg"]


def test_metadata_refuses_a_malcolm_password_it_cannot_hash() -> None:
    assert (
        Secrets.model_validate({"malcolmPassword": "Okay~pass:1,x"}).malcolm_password
        is not None
    )
    for bad in ("Short1", "-leading1A.xxxx", "A" * 73, "has space 123456"):
        with pytest.raises(ValidationError) as ex:
            Secrets.model_validate({"malcolmPassword": bad})
        (error,) = ex.value.errors()
        assert error["loc"] == ("malcolmPassword",)
        assert bad not in error["msg"]


@pytest.mark.parametrize("key", ["wazuhPassword", "malcolmPassword"])
def test_the_app_reports_a_bad_password_without_echoing_it(
    run_app: RunApp, experiment: dict[str, Any], key: str
) -> None:
    """The app's own report names the field and the rule, never the value."""
    from phenix_apps.common.error import AppError
    from phenix_apps.common.logger import logger

    app_block(experiment)["metadata"]["secrets"] = {key: "Tiny1."}
    lines: list[str] = []
    sink: int = logger.add(
        lambda m: lines.append(str(m)), level="DEBUG", format="{message}"
    )
    try:
        with pytest.raises(AppError):
            run_app("configure", exp=experiment)
    finally:
        logger.remove(sink)
    text = "\n".join(lines)
    assert f"metadata.secrets.{key}" in text
    assert "Tiny1." not in text


def test_both_are_generated_once_and_kept(
    run_app: RunApp, experiment: dict[str, Any]
) -> None:
    first = run_app("pre-start")
    path: Path = first.out_dir / "secrets.json"
    stored = json.loads(path.read_text())
    assert wazuh_password_problem(stored["wazuh_password"]) is None
    assert malcolm_password_problem(stored["malcolm_password"]) is None
    assert path.stat().st_mode & 0o777 == 0o600
    again = json.loads(
        (run_app("pre-start", exp=experiment).out_dir / "secrets.json").read_text()
    )
    assert again == stored


def test_an_older_secrets_file_gains_both_and_keeps_the_rest(
    run_app: RunApp, experiment: dict[str, Any]
) -> None:
    path: Path = run_app("pre-start").out_dir / "secrets.json"
    old = json.loads(path.read_text())
    del old["malcolm_password"], old["wazuh_password"]
    path.write_text(json.dumps(old))
    again = json.loads(
        (run_app("pre-start", exp=experiment).out_dir / "secrets.json").read_text()
    )
    assert {
        k: v
        for k, v in again.items()
        if k not in ("malcolm_password", "wazuh_password")
    } == old
    assert again["malcolm_password"]
    assert wazuh_password_problem(again["wazuh_password"]) is None


def test_metadata_wins_and_is_not_stored(
    run_app: RunApp, experiment: dict[str, Any]
) -> None:
    app_block(experiment)["metadata"]["secrets"] = {
        "wazuhPassword": "Range.wazuh-pw1",
        "malcolmPassword": "range-malcolm-pw1",
    }
    app = run_app("pre-start", exp=experiment)
    stored = json.loads((app.out_dir / "secrets.json").read_text())
    assert "wazuh_password" not in stored
    assert "malcolm_password" not in stored
    assert app.config is not None
    resolved = credentials.resolve(app.config.secrets, app.out_dir / "secrets.json")
    assert resolved.wazuh_password.get_secret_value() == "Range.wazuh-pw1"
    assert resolved.malcolm_password.get_secret_value() == "range-malcolm-pw1"


@pytest.mark.parametrize("key", ["wazuh_password", "malcolm_password"])
def test_a_stored_value_that_breaks_the_rule_is_refused_without_showing_it(
    run_app: RunApp, experiment: dict[str, Any], key: str
) -> None:
    """A hand-edited secrets.json must not put a password the service cannot take
    on a VM."""
    path: Path = run_app("pre-start").out_dir / "secrets.json"
    stored = json.loads(path.read_text())
    stored[key] = "bad value " if key == "wazuh_password" else "tiny"
    path.write_text(json.dumps(stored))
    with pytest.raises(ValidationError) as ex:
        run_app("pre-start", exp=experiment)
    assert stored[key] not in str(ex.value)
    assert f"{key} in secrets.json" in str(ex.value)


def test_the_shell_rule_of_the_wazuh_script_agrees_with_the_apps(
    tmp_path: Path,
) -> None:
    """cm-wazuh-provision checks the password again on the VM (admin_password_ok);
    its decision and the app's must be the same on every case above and on the
    generated passwords."""
    script = (
        Path(__file__).resolve().parents[4]
        / "phenix-injects"
        / "wazuh"
        / "cm-wazuh-provision"
    ).read_text()
    match = re.search(
        r"^admin_password_ok\(\) \{\n.*?^\}\n", script, re.MULTILINE | re.DOTALL
    )
    assert match is not None
    cases = [credentials.wazuh_password() for _ in range(50)] + [
        "Aa1.aaaa",
        "Aa1.aaa",
        "aa1.aaaa",
        "Aa1_aaaa",
        "Aa1*aaaa",
        "-Aa1.aaaa",
        "Aa1.aaa~",
        "Aa1" + "x" * 61 + ".",
    ]
    harness = (
        match.group(0)
        + 'while IFS= read -r pw; do if admin_password_ok "$pw"; then echo ok; else echo no; fi; done\n'
    )
    proc = subprocess.run(
        ["bash", "-c", harness],
        input="\n".join(cases) + "\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert proc.stdout.split() == [
        "ok" if wazuh_password_problem(c) is None else "no" for c in cases
    ]
