"""The verdict, decided from merged data and nothing else."""

import pytest

from plimsoll_api.services.sla import Verdict, evaluate
from plimsoll_contracts.performance_tests import SlaMetric, SlaOperator, SlaRuleSpec, SlaSeverity
from plimsoll_contracts.results import RunMetricsResponse, TransactionSummary

RUN_ID = "11111111-1111-1111-1111-111111111111"


def _summary(**overrides: object) -> RunMetricsResponse:
    defaults = dict(
        transaction="Checkout",
        count=1000,
        error_count=10,
        error_rate=0.01,
        min=100,
        max=3000,
        mean=800.0,
        p50=750,
        p90=1500,
        p95=1800,
        p99=2500,
        throughput=50.0,
    )
    defaults.update(overrides)
    return RunMetricsResponse(
        run_id=RUN_ID,
        total_samples=1000,
        total_errors=10,
        transactions=[TransactionSummary(**defaults)],
    )


def _rule(**overrides: object) -> SlaRuleSpec:
    defaults = dict(
        name="p95 under 2s",
        metric=SlaMetric.P95,
        entity="Checkout",
        operator=SlaOperator.LT,
        threshold=2000.0,
        severity=SlaSeverity.ERROR,
    )
    defaults.update(overrides)
    return SlaRuleSpec(**defaults)


def test_a_rule_that_holds_passes() -> None:
    result = evaluate([_rule()], _summary(), degraded=False)
    assert result.outcome is Verdict.PASS
    assert result.rules[0].verdict is Verdict.PASS
    assert result.rules[0].actual == 1800


def test_a_rule_that_does_not_hold_fails() -> None:
    result = evaluate([_rule(threshold=1000.0)], _summary(), degraded=False)
    assert result.outcome is Verdict.FAIL
    assert result.rules[0].actual == 1800


@pytest.mark.parametrize(
    ("operator", "threshold", "holds"),
    [
        (SlaOperator.LT, 2000.0, True),
        (SlaOperator.LT, 1800.0, False),
        (SlaOperator.LTE, 1800.0, True),
        (SlaOperator.GT, 1000.0, True),
        (SlaOperator.GT, 1800.0, False),
        (SlaOperator.GTE, 1800.0, True),
    ],
)
def test_every_operator_means_what_it_says(
    operator: SlaOperator, threshold: float, holds: bool
) -> None:
    result = evaluate([_rule(operator=operator, threshold=threshold)], _summary(), degraded=False)
    assert (result.outcome is Verdict.PASS) is holds


def test_a_warning_rule_warns_rather_than_fails() -> None:
    result = evaluate(
        [_rule(threshold=1000.0, severity=SlaSeverity.WARNING)], _summary(), degraded=False
    )
    assert result.rules[0].verdict is Verdict.WARNING
    assert result.outcome is Verdict.WARNING


def test_the_run_takes_the_worst_verdict() -> None:
    """A passing rule beside a failing one is still a failing run."""
    result = evaluate(
        [
            _rule(name="fine", threshold=5000.0),
            _rule(name="warn", threshold=1000.0, severity=SlaSeverity.WARNING),
            _rule(name="bad", threshold=1000.0),
        ],
        _summary(),
        degraded=False,
    )
    assert result.outcome is Verdict.FAIL


def test_a_rule_naming_a_transaction_with_no_data_is_skipped() -> None:
    """Absent is not the same as satisfied. Reporting a pass here would say the
    threshold held when nothing was ever measured against it."""
    result = evaluate([_rule(entity="Nonexistent")], _summary(), degraded=False)
    assert result.rules[0].verdict is Verdict.SKIPPED
    assert result.rules[0].actual is None
    assert "no data" in result.rules[0].detail.lower()


def test_a_skipped_rule_does_not_make_a_run_fail() -> None:
    result = evaluate([_rule(entity="Nonexistent")], _summary(), degraded=False)
    assert result.outcome is Verdict.PASS


def test_a_disabled_rule_is_not_evaluated() -> None:
    result = evaluate([_rule(threshold=1.0, enabled=False)], _summary(), degraded=False)
    assert result.rules == []
    assert result.outcome is Verdict.PASS


def test_error_rate_is_read_from_the_transaction() -> None:
    result = evaluate(
        [_rule(metric=SlaMetric.ERROR_RATE, operator=SlaOperator.LT, threshold=0.05)],
        _summary(),
        degraded=False,
    )
    assert result.rules[0].actual == 0.01
    assert result.outcome is Verdict.PASS


def test_throughput_and_average_are_available_too() -> None:
    assert (
        evaluate(
            [_rule(metric=SlaMetric.THROUGHPUT, operator=SlaOperator.GT, threshold=10.0)],
            _summary(),
            degraded=False,
        )
        .rules[0]
        .actual
        == 50.0
    )
    assert (
        evaluate(
            [_rule(metric=SlaMetric.AVG, operator=SlaOperator.LT, threshold=1000.0)],
            _summary(),
            degraded=False,
        )
        .rules[0]
        .actual
        == 800.0
    )


def test_a_rule_without_an_entity_reads_the_whole_run() -> None:
    """A run-level error rate is total errors over total samples, not the mean
    of each transaction's rate -- transactions do not carry equal weight."""
    result = evaluate(
        [_rule(entity=None, metric=SlaMetric.ERROR_RATE, operator=SlaOperator.LT, threshold=0.05)],
        _summary(),
        degraded=False,
    )
    assert result.rules[0].actual == 0.01
    assert result.outcome is Verdict.PASS


def test_a_degraded_run_cannot_report_a_clean_pass() -> None:
    """It generated less load than planned, so its numbers describe a test that
    was never actually run."""
    result = evaluate([_rule()], _summary(), degraded=True)
    assert result.outcome is Verdict.WARNING
    assert "degraded" in result.detail.lower()


def test_a_degraded_run_that_also_fails_still_fails() -> None:
    result = evaluate([_rule(threshold=1000.0)], _summary(), degraded=True)
    assert result.outcome is Verdict.FAIL


def test_no_rules_is_a_pass_with_nothing_to_say() -> None:
    result = evaluate([], _summary(), degraded=False)
    assert result.outcome is Verdict.PASS
    assert result.rules == []
