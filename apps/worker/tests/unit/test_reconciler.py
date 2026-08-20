from datetime import UTC, datetime, timedelta

from plimsoll_worker.reconciler import Decision, GeneratorRow, RunView, decide

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _view(
    status: str,
    generators: list[GeneratorRow],
    *,
    started_at: datetime | None = None,
    max_capacity_loss_percent: int = 10,
) -> RunView:
    return RunView(
        status=status,
        generators=generators,
        now=NOW,
        duration_seconds=60,
        started_at=started_at,
        max_capacity_loss_percent=max_capacity_loss_percent,
    )


def _generator(ordinal: int, status: str, users: int = 10, beat_age: int = 0) -> GeneratorRow:
    return GeneratorRow(
        ordinal=ordinal,
        status=status,
        assigned_users=users,
        external_ref=None if status == "PENDING" else f"container-{ordinal}",
        last_heartbeat=NOW - timedelta(seconds=beat_age),
    )


def test_a_queued_run_is_provisioned() -> None:
    view = _view("QUEUED", [_generator(0, "PENDING"), _generator(1, "PENDING")])
    assert decide(view) is Decision.PROVISION


def test_a_run_whose_generators_exist_is_not_provisioned_again() -> None:
    """At-least-once delivery means this message may be the second copy."""
    view = _view("ALLOCATING", [_generator(0, "PROVISIONED"), _generator(1, "PROVISIONED")])
    assert decide(view) is Decision.WAIT


def test_all_ready_starts_the_run() -> None:
    view = _view("STARTING", [_generator(0, "READY"), _generator(1, "READY")])
    assert decide(view) is Decision.START


def test_one_generator_short_of_ready_does_not_start() -> None:
    """A staggered start smears the ramp and quietly distorts the result."""
    view = _view("STARTING", [_generator(0, "READY"), _generator(1, "FETCHING")])
    assert decide(view) is Decision.WAIT


def test_all_terminal_finishes_the_run() -> None:
    view = _view("RUNNING", [_generator(0, "COMPLETED"), _generator(1, "COMPLETED")])
    assert decide(view) is Decision.FINISH


def test_a_silent_generator_is_lost() -> None:
    view = _view("RUNNING", [_generator(0, "RUNNING"), _generator(1, "RUNNING", beat_age=45)])
    assert decide(view) is Decision.MARK_LOST


def test_capacity_loss_below_the_threshold_continues_degraded() -> None:
    view = _view(
        "RUNNING",
        [_generator(0, "RUNNING", users=95), _generator(1, "LOST", users=5)],
    )
    assert decide(view) is Decision.CONTINUE_DEGRADED


def test_capacity_loss_at_the_threshold_fails_the_run() -> None:
    view = _view(
        "RUNNING",
        [_generator(0, "RUNNING", users=90), _generator(1, "LOST", users=10)],
    )
    assert decide(view) is Decision.FAIL


def test_a_stopping_run_waits_for_its_generators() -> None:
    view = _view("STOPPING", [_generator(0, "RUNNING"), _generator(1, "COMPLETED")])
    assert decide(view) is Decision.WAIT


def test_a_terminal_run_needs_nothing() -> None:
    for status in ("COMPLETED", "FAILED", "CANCELLED"):
        assert decide(_view(status, [_generator(0, "COMPLETED")])) is Decision.DONE


def test_a_run_abandoned_mid_provision_is_provisioned_again() -> None:
    """A worker that died between claiming the run and finishing it.

    The run reached ALLOCATING and some ordinal never got a container. Waiting
    is the one thing that cannot be right: nothing else moves an ALLOCATING run
    on, so it would sit there for the life of the deployment while its
    containers -- if any were made -- ran unattended.
    """
    view = _view("ALLOCATING", [_generator(0, "PENDING"), _generator(1, "PENDING")])
    assert decide(view) is Decision.PROVISION


def test_a_partly_provisioned_run_is_provisioned_again() -> None:
    """One ordinal has a container and the other does not: the gap is what
    provisioning is for, and it is safe to re-enter because an ordinal that
    already has one is skipped."""
    view = _view("ALLOCATING", [_generator(0, "PROVISIONED"), _generator(1, "PENDING")])
    assert decide(view) is Decision.PROVISION


def test_a_fully_provisioned_run_is_left_alone() -> None:
    """Already covered elsewhere, restated here so the two cases sit together:
    every ordinal has a container, so there is nothing to provision."""
    view = _view("ALLOCATING", [_generator(0, "PROVISIONED"), _generator(1, "PROVISIONED")])
    assert decide(view) is Decision.WAIT


def test_a_generator_already_running_still_starts_the_run() -> None:
    """After a recovery, a generator can be further along than the run.

    Its container survived, its agent was already told to start, and the run
    was put back to STARTING. Requiring exactly READY would leave the run there
    for ever waiting for a state that has already been passed.
    """
    view = _view("STARTING", [_generator(0, "READY"), _generator(1, "RUNNING")])
    assert decide(view) is Decision.START


def test_a_generator_still_fetching_does_not_start_the_run() -> None:
    """The staggered-start guard is unchanged: further along is fine, short of
    ready is not."""
    view = _view("STARTING", [_generator(0, "RUNNING"), _generator(1, "FETCHING")])
    assert decide(view) is Decision.WAIT
