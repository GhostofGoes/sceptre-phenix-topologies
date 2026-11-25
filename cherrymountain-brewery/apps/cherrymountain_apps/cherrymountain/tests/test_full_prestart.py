"""A whole pre-start render of the full variant: every host of the 43-node
topology, from the committed plantgen output, program ZIPs, and FUXA builder."""

from __future__ import annotations

import stat
from pathlib import Path

from cherrymountain_apps.cherrymountain import render
from cherrymountain_apps.cherrymountain.render import Context, Injection
from cherrymountain_apps.cherrymountain.tests.test_full import ctx, full  # noqa: F401 - fixtures

#: Files that are private in the experiment directory but must be readable in
#: the guest, with the reason.
READABLE_IN_GUEST: dict[str, str] = {
    # PostgreSQL's entrypoint runs its init files as the postgres user.
    "/opt/cherrymountain/guacamole/initdb/50-cherrymountain.sql": "postgres init file",
}


def test_full_pre_start_renders_every_host(ctx: Context) -> None:  # noqa: F811
    plan: dict[str, list[Injection]] = render.render_all(ctx)
    # Every app host with a renderer, and every MGMT member, gets files.
    for host in ("sim-ctl", "sim-process", "sim-power", "sim-building", "plc-brewhouse",
                 "plc-cellar", "plc-packaging", "plc-utilities", "plc-power", "scada-fuxa",
                 "bas-fuxa", "historian", "ews", "hmi-brewhouse", "dmz-jump", "wazuh",
                 "rio-cellar", "bas-vent"):
        assert plan.get(host), host
    for host, injects in plan.items():
        dsts: list[str] = [i.dst for i in injects]
        assert len(dsts) == len(set(dsts)), (host, "two injections to one destination")
        for inject in injects:
            assert inject.src.is_file(), (host, inject.dst)


def test_full_files_with_a_credential_are_root_only_in_the_guest(ctx: Context) -> None:  # noqa: F811
    """A file the app writes 0600 (it holds a credential) is injected 0600,
    unless a service that is not root has to read it in the guest."""
    wrong: list[str] = []
    for host, injects in render.render_all(ctx).items():
        for inject in injects:
            private: bool = stat.S_IMODE(Path(inject.src).stat().st_mode) == 0o600
            if private and inject.permissions != "0600" and inject.dst not in READABLE_IN_GUEST:
                wrong.append(f"{host}: {inject.dst} ({inject.permissions})")
    assert not wrong, "credential files injected readable by others: " + ", ".join(wrong)
