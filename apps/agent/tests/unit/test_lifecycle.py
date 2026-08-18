from plimsoll_agent.lifecycle import Action, next_action
from plimsoll_contracts.agent import AgentState, Command


def test_it_waits_until_told_to_start() -> None:
    assert next_action(Command.WAIT, AgentState.READY) is Action.HOLD


def test_it_starts_when_commanded() -> None:
    assert next_action(Command.START, AgentState.READY) is Action.RUN


def test_it_does_not_start_twice() -> None:
    """A repeated START -- a push and then a heartbeat carrying the same state
    -- must not launch a second execution."""
    assert next_action(Command.START, AgentState.RUNNING) is Action.HOLD


def test_a_stop_while_running_winds_down() -> None:
    assert next_action(Command.STOP, AgentState.RUNNING) is Action.WIND_DOWN


def test_a_stop_before_running_finishes_immediately() -> None:
    assert next_action(Command.STOP, AgentState.READY) is Action.FINISH


def test_a_cancel_abandons_from_anywhere() -> None:
    for state in (AgentState.FETCHING, AgentState.READY, AgentState.RUNNING):
        assert next_action(Command.CANCEL, state) is Action.ABANDON


def test_nothing_follows_a_terminal_state() -> None:
    assert next_action(Command.START, AgentState.COMPLETED) is Action.HOLD
