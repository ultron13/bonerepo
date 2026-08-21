import pathlib

import pytest

from plimsoll_api.plans.jmx import PlanParseError, PlanSummary, PlanTarget, parse_plan

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "plans"


def _summary() -> PlanSummary:
    return parse_plan((FIXTURES / "checkout.jmx").read_text())


def test_thread_groups_are_named() -> None:
    assert _summary().thread_groups == ["Shoppers"]


def test_transaction_controllers_are_named() -> None:
    assert _summary().transaction_controllers == ["Checkout journey"]


def test_timers_are_named() -> None:
    assert _summary().timers == ["Think time"]


def test_data_files_are_found() -> None:
    assert _summary().data_files == ["data/users.csv"]


def test_variables_exclude_jmeter_functions() -> None:
    # ${__P(threads,10)} is a JMeter function, not a Plimsoll variable.
    assert _summary().variables == ["API_HOST", "API_TOKEN"]


def test_targets_come_from_samplers_and_defaults() -> None:
    targets = _summary().targets
    assert PlanTarget(scheme="http", host="demo-target", port=8080) in targets
    # The Checkout sampler names a domain but no port, so it inherits 8080 from
    # HTTP Request Defaults -- which is what JMeter itself would do.
    assert PlanTarget(scheme="http", host="${API_HOST}", port=8080) in targets


def test_a_sampler_without_a_domain_inherits_the_defaults() -> None:
    """The Browse sampler names no domain, so it targets demo-target:8080."""
    hosts = [target.host for target in _summary().targets]
    assert hosts.count("demo-target") == 1, "the inherited target is reported once, not per sampler"


def test_an_external_entity_is_not_resolved() -> None:
    with pytest.raises(PlanParseError):
        parse_plan((FIXTURES / "entity.jmx").read_text())


def test_a_malformed_document_is_an_error() -> None:
    with pytest.raises(PlanParseError):
        parse_plan("<jmeterTestPlan>")
