"""SLA evaluation: rules in, verdict out.

Pure, which is what lets it be tested exhaustively without a run. It reads only
the merged summary -- a rule evaluated against one generator's view is a rule
evaluated against a fraction of the load, and would pass or fail for reasons
that have nothing to do with the system under test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from plimsoll_contracts.performance_tests import (
    SlaMetric,
    SlaOperator,
    SlaRuleSpec,
    SlaSeverity,
)
from plimsoll_contracts.results import RunMetricsResponse, TransactionSummary


class Verdict(IntEnum):
    """Ordered by severity, and an IntEnum because that ordering is used: the
    run takes the worst of its rules, and `max` is how it says so."""

    PASS = 0
    SKIPPED = 1
    WARNING = 2
    FAIL = 3


@dataclass(frozen=True)
class RuleOutcome:
    name: str
    metric: str
    entity: str | None
    operator: str
    threshold: float
    actual: float | None
    verdict: Verdict
    detail: str


@dataclass(frozen=True)
class SlaResult:
    outcome: Verdict
    detail: str
    rules: list[RuleOutcome] = field(default_factory=list)


_COMPARISONS = {
    SlaOperator.LT: lambda actual, threshold: actual < threshold,
    SlaOperator.LTE: lambda actual, threshold: actual <= threshold,
    SlaOperator.GT: lambda actual, threshold: actual > threshold,
    SlaOperator.GTE: lambda actual, threshold: actual >= threshold,
}


def _from_transaction(metric: SlaMetric, item: TransactionSummary) -> float | None:
    return {
        SlaMetric.P50: float(item.p50),
        SlaMetric.P90: float(item.p90),
        SlaMetric.P95: float(item.p95),
        SlaMetric.P99: float(item.p99),
        SlaMetric.AVG: item.mean,
        SlaMetric.ERROR_RATE: item.error_rate,
        SlaMetric.THROUGHPUT: item.throughput,
    }.get(metric)


def _run_level(metric: SlaMetric, summary: RunMetricsResponse) -> float | None:
    """Whole-run values, weighted by what actually happened.

    An error rate is total errors over total samples, never the mean of each
    transaction's rate: transactions do not carry equal weight, and averaging
    rates would let a rare transaction outvote a common one.
    """
    if metric is SlaMetric.ERROR_RATE:
        if not summary.total_samples:
            return None
        return summary.total_errors / summary.total_samples
    if metric is SlaMetric.THROUGHPUT:
        return sum(item.throughput for item in summary.transactions) or None
    # A run-wide percentile would need the run's merged sketch rather than a
    # summary, so a percentile rule has to name the transaction it means.
    return None


def _actual(rule: SlaRuleSpec, summary: RunMetricsResponse) -> tuple[float | None, str]:
    if rule.entity is None:
        value = _run_level(rule.metric, summary)
        if value is None:
            return None, f"No run-level data for {rule.metric}; name a transaction."
        return value, ""

    match = next((item for item in summary.transactions if item.transaction == rule.entity), None)
    if match is None:
        return None, f"The transaction {rule.entity!r} produced no data in this run."
    value = _from_transaction(rule.metric, match)
    if value is None:
        return None, f"No data for {rule.metric} on {rule.entity!r}."
    return value, ""


def evaluate(rules: list[SlaRuleSpec], summary: RunMetricsResponse, *, degraded: bool) -> SlaResult:
    outcomes: list[RuleOutcome] = []
    for rule in rules:
        if not rule.enabled:
            continue

        actual, reason = _actual(rule, summary)
        if actual is None:
            # Absent is not satisfied. Reporting a pass would say the threshold
            # held when nothing was ever measured against it.
            outcomes.append(
                RuleOutcome(
                    name=rule.name,
                    metric=str(rule.metric),
                    entity=rule.entity,
                    operator=str(rule.operator),
                    threshold=rule.threshold,
                    actual=None,
                    verdict=Verdict.SKIPPED,
                    detail=reason,
                )
            )
            continue

        held = _COMPARISONS[rule.operator](actual, rule.threshold)
        verdict = (
            Verdict.PASS
            if held
            else (Verdict.WARNING if rule.severity is SlaSeverity.WARNING else Verdict.FAIL)
        )
        outcomes.append(
            RuleOutcome(
                name=rule.name,
                metric=str(rule.metric),
                entity=rule.entity,
                operator=str(rule.operator),
                threshold=rule.threshold,
                actual=actual,
                verdict=verdict,
                detail=("" if held else f"{actual} is not {rule.operator} {rule.threshold}"),
            )
        )

    outcome = max((item.verdict for item in outcomes), default=Verdict.PASS)
    # SKIPPED is worse than PASS for ordering the list, but it is not itself a
    # failing run: nothing was measured, so nothing was violated.
    if outcome is Verdict.SKIPPED:
        outcome = Verdict.PASS
    detail = ""

    if degraded:
        # The run generated less load than planned, so its numbers describe a
        # test that was never actually run. It cannot be a clean pass.
        detail = "The run lost capacity and its results are degraded."
        outcome = max(outcome, Verdict.WARNING)

    return SlaResult(outcome=outcome, detail=detail, rules=outcomes)
