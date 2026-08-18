"""What to do next, given what the control plane wants and where we are.

Pure, and therefore the part that is actually tested. Everything around it is
sockets and subprocesses.
"""

from __future__ import annotations

from enum import Enum, auto

from plimsoll_contracts.agent import AgentState, Command

TERMINAL = {AgentState.COMPLETED, AgentState.FAILED}


class Action(Enum):
    HOLD = auto()
    RUN = auto()
    WIND_DOWN = auto()
    FINISH = auto()
    ABANDON = auto()


def next_action(command: Command, state: AgentState) -> Action:
    if state in TERMINAL:
        return Action.HOLD
    if command is Command.CANCEL:
        return Action.ABANDON
    if command is Command.STOP:
        # Nothing has started, so there is nothing to wind down.
        return Action.WIND_DOWN if state is AgentState.RUNNING else Action.FINISH
    if command is Command.START and state is AgentState.READY:
        return Action.RUN
    return Action.HOLD
