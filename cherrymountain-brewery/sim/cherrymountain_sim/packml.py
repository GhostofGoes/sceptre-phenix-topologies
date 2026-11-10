"""PackML state machine (ANSI/ISA-TR88.00.02; DESIGN.md §6.3, §9.3).

The transition table is PackMLpy's (``packmlpy.statemachine.definitions``,
MIT), which follows the TR88.00.02 state model: commands (Start, Hold, ...)
move the machine from wait states, and acting states (Starting, Holding, ...)
end with a state-complete (SC). PackMLpy itself is an asyncio machine whose
acting states complete at once on entry, so it cannot hold a machine in
Execute or let an acting state take time; this module is a synchronous
equivalent with the same table, and the tests check the two agree.

State numbers are the PackTags ``StateCurrent`` values; command numbers are
``CntrlCmd`` values; modes are ``UnitModeCurrent`` (1 Production,
2 Maintenance, 3 Manual).
"""

from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, Field


class State(IntEnum):
    """PackTags StateCurrent."""

    UNDEFINED = 0
    CLEARING = 1
    STOPPED = 2
    STARTING = 3
    IDLE = 4
    SUSPENDED = 5
    EXECUTE = 6
    STOPPING = 7
    ABORTING = 8
    ABORTED = 9
    HOLDING = 10
    HELD = 11
    UNHOLDING = 12
    SUSPENDING = 13
    UNSUSPENDING = 14
    RESETTING = 15
    COMPLETING = 16
    COMPLETE = 17


class Command(IntEnum):
    """PackTags CntrlCmd."""

    UNDEFINED = 0
    RESET = 1
    START = 2
    STOP = 3
    HOLD = 4
    UNHOLD = 5
    SUSPEND = 6
    UNSUSPEND = 7
    ABORT = 8
    CLEAR = 9


class Mode(IntEnum):
    """PackTags UnitModeCurrent."""

    PRODUCTION = 1
    MAINTENANCE = 2
    MANUAL = 3


S = State
#: Commands accepted in each state (PackMLpy's ``transitions``).
COMMANDS: dict[tuple[State, Command], State] = {
    (S.IDLE, Command.START): S.STARTING,
    (S.COMPLETE, Command.RESET): S.RESETTING,
    (S.STOPPED, Command.RESET): S.RESETTING,
    (S.EXECUTE, Command.HOLD): S.HOLDING,
    (S.HELD, Command.UNHOLD): S.UNHOLDING,
    (S.EXECUTE, Command.SUSPEND): S.SUSPENDING,
    (S.SUSPENDED, Command.UNSUSPEND): S.UNSUSPENDING,
    (S.ABORTED, Command.CLEAR): S.CLEARING,
}
for _s in (
    S.IDLE,
    S.STARTING,
    S.EXECUTE,
    S.COMPLETING,
    S.COMPLETE,
    S.RESETTING,
    S.HOLDING,
    S.HELD,
    S.UNHOLDING,
    S.SUSPENDING,
    S.SUSPENDED,
    S.UNSUSPENDING,
):
    COMMANDS[(_s, Command.STOP)] = S.STOPPING
for _s in (*{s for s, _ in COMMANDS} | {S.CLEARING, S.STOPPING, S.STOPPED},):
    if _s not in (S.ABORTING, S.ABORTED):
        COMMANDS[(_s, Command.ABORT)] = S.ABORTING
#: State-complete of each acting state.
STATE_COMPLETE: dict[State, State] = {
    S.STARTING: S.EXECUTE,
    S.COMPLETING: S.COMPLETE,
    S.RESETTING: S.IDLE,
    S.HOLDING: S.HELD,
    S.UNHOLDING: S.EXECUTE,
    S.SUSPENDING: S.SUSPENDED,
    S.UNSUSPENDING: S.EXECUTE,
    S.STOPPING: S.STOPPED,
    S.ABORTING: S.ABORTED,
    S.CLEARING: S.STOPPED,
}
#: Execute completes (SC) only for batch machines; a filler runs until held.
EXECUTE_COMPLETE: State = S.COMPLETING
ACTING: frozenset[State] = frozenset(STATE_COMPLETE)


class IllegalTransition(ValueError):
    """A command the current state does not accept."""


class PackMLMachine(BaseModel):
    """One PackML unit/machine with time-in-state per mode (for OEE)."""

    name: str
    state: State = S.STOPPED
    mode: Mode = Mode.PRODUCTION
    acting_s: float = 2.0  # how long each acting state takes
    timer_s: float = 0.0
    state_time_s: dict[str, float] = Field(default_factory=dict)
    history: list[tuple[State, State]] = Field(default_factory=list)

    def can(self, cmd: Command) -> bool:
        """Whether ``cmd`` is legal in the current state."""
        return (self.state, cmd) in COMMANDS

    def command(self, cmd: Command) -> State:
        """Apply a command; raises :class:`IllegalTransition` when not allowed."""
        target = COMMANDS.get((self.state, cmd))
        if target is None:
            raise IllegalTransition(f"{self.name}: {cmd.name} in {self.state.name}")
        self._go(target)
        return target


    def set_mode(self, mode: Mode) -> None:
        """Mode changes are allowed only in Stopped, Idle, Aborted (TR88)."""
        if self.state not in (S.STOPPED, S.IDLE, S.ABORTED) and mode != self.mode:
            raise IllegalTransition(f"{self.name}: mode change in {self.state.name}")
        self.mode = mode

    def step(self, dt: float) -> None:
        """Advance time; acting states complete after ``acting_s``."""
        key = f"{self.mode.name}.{self.state.name}"
        self.state_time_s[key] = self.state_time_s.get(key, 0.0) + dt
        self.timer_s += dt
        if self.state in ACTING and self.timer_s >= self.acting_s:
            self._go(STATE_COMPLETE[self.state])

    def _go(self, target: State) -> None:
        self.history.append((self.state, target))
        self.state = target
        self.timer_s = 0.0


def legal(source: State, target: State) -> bool:
    """Whether ``source -> target`` is a TR88.00.02 transition."""
    return (
        any(s == source and t == target for (s, _), t in COMMANDS.items())
        or STATE_COMPLETE.get(source) == target
        or (source == S.EXECUTE and target == EXECUTE_COMPLETE)
    )
