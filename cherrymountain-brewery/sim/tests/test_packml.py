"""PackML: legal state transitions only (§17), and the packaging line."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from cherrymountain_sim.federate import HelicsConfig, build
from cherrymountain_sim.packaging import PackagingFederate
from cherrymountain_sim.packml import (
    COMMANDS,
    STATE_COMPLETE,
    Command,
    IllegalTransition,
    PackMLMachine,
    State,
    legal,
)

FULL: Path = (
    Path(__file__).resolve().parents[2] / "plant" / "generated" / "full" / "helics.json"
)


def test_table_matches_packmlpy() -> None:
    """The transition table equals PackMLpy's (TR88.00.02)."""
    defs = pytest.importorskip("packmlpy.statemachine.definitions")
    theirs = set()
    for t in defs.transitions:
        src, dst = State[t["source"].name.upper()], State[t["dest"].name.upper()]
        theirs.add((src, dst))
    ours = {(s, d) for (s, _), d in COMMANDS.items()} | set(STATE_COMPLETE.items())
    ours.add((State.EXECUTE, State.COMPLETING))
    assert ours == theirs


def test_illegal_commands_are_refused() -> None:
    """Start from Stopped, Unhold from Execute, Reset from Execute are illegal."""
    m = PackMLMachine(name="filler")
    with pytest.raises(IllegalTransition):
        m.command(Command.START)
    m.command(Command.RESET)
    m.step(5.0)
    assert m.state == State.IDLE
    m.command(Command.START)
    m.step(5.0)
    assert m.state == State.EXECUTE
    for cmd in (Command.UNHOLD, Command.RESET, Command.CLEAR, Command.UNSUSPEND):
        with pytest.raises(IllegalTransition):
            m.command(cmd)
    with pytest.raises(IllegalTransition):
        m.set_mode(m.mode.__class__.MAINTENANCE)  # not while executing


def test_random_commands_only_make_legal_transitions() -> None:
    """Fuzz: thousands of random commands; every recorded transition is legal."""
    rng = random.Random(1)
    m = PackMLMachine(name="labeler", acting_s=1.0)
    for _ in range(5000):
        cmd = Command(rng.randint(1, 9))
        if m.can(cmd):
            m.command(cmd)
        else:
            with pytest.raises(IllegalTransition):
                m.command(cmd)
        m.step(rng.choice([0.5, 1.0, 2.0]))
    assert len(m.history) > 1000
    assert all(legal(s, d) for s, d in m.history)


def test_packaging_shift_is_legal_and_produces() -> None:
    """A packaging shift: the line fills bottles, jams hold and clear, and
    every machine's transitions are legal (power loss included)."""
    fed = build(HelicsConfig.load(FULL), "packaging")
    assert isinstance(fed, PackagingFederate)
    fed.clock = fed.clock.model_copy(
        update={"start": fed.clock.start.replace(hour=5, minute=55)}
    )
    for i in range(3 * 3600):
        if i == 3600:
            fed.handle_message(
                json.dumps(
                    {
                        "event": {
                            "type": "jam",
                            "params": {"machine": "labeler", "duration": 120},
                        }
                    }
                )
            )
        if i == 5400:
            fed.apply_input("power/bus.pkg.energized", False)
        if i == 5460:
            fed.apply_input("power/bus.pkg.energized", True)
        fed.step(1.0)
    assert fed.bottles_total > 3000
    assert fed.output_value("beer.draw_gpm") > 0
    for m in fed.machines.values():
        assert all(legal(s, d) for s, d in m.pml.history), m.name
    labeler = fed.machines["labeler"].pml.history
    assert (State.EXECUTE, State.HOLDING) in labeler
    assert (State.ABORTED, State.CLEARING) in labeler
    oee = fed.oee()
    assert 0.5 < oee["oee"] <= 1.0
