"""The JMeter invocation, as ADR-0006 fixes it.

Pure string building, and worth testing precisely: a wrong property name does
not fail, it silently runs the plan's own defaults instead of the workload the
operator asked for.
"""

from pathlib import Path

from plimsoll_executor.base import ExecutionContext, Outcome
from plimsoll_executor.jmeter import JMeterExecutor

CONTEXT = ExecutionContext(
    plan_path=Path("/work/perf/checkout.jmx"),
    working_directory=Path("/work/perf"),
    output_directory=Path("/out"),
    threads=250,
    ramp_up_seconds=60,
    duration_seconds=600,
    variables={"API_HOST": "demo-target", "API_TOKEN": "secret-value"},
)


def test_the_command_is_headless_and_names_the_plan() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert command[0].endswith("jmeter")
    assert "-n" in command
    assert "-t" in command
    assert command[command.index("-t") + 1] == "/work/perf/checkout.jmx"


def test_the_workload_travels_as_properties() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert "-Jthreads=250" in command
    assert "-Jrampup=60" in command
    assert "-Jduration=600" in command


def test_variables_travel_as_properties_too() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert "-JAPI_HOST=demo-target" in command


def test_the_jtl_is_written_where_the_agent_expects_it() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert command[command.index("-l") + 1] == "/out/results.jtl"


def test_the_plan_is_never_rewritten() -> None:
    """No flag here edits the plan; a plan that runs on the platform and a plan
    that runs on a tester's laptop must be the same bytes."""
    command = JMeterExecutor().command(CONTEXT)
    assert not any(flag in command for flag in ("-J-", "--forceDeleteResultFile"))
    assert command.count("-t") == 1


def test_the_artifacts_are_the_jtl_and_the_log() -> None:
    names = [path.name for path in JMeterExecutor().artifacts(CONTEXT)]
    assert names == ["results.jtl", "jmeter.log"]


def test_a_clean_exit_is_a_completed_run() -> None:
    assert JMeterExecutor().interpret(0) is Outcome.COMPLETED


def test_a_non_zero_exit_is_a_failed_generator() -> None:
    """Sampler errors inside a healthy run are results. This is JMeter itself
    failing -- a broken plan, a missing plugin, a bad property."""
    assert JMeterExecutor().interpret(1) is Outcome.FAILED
    assert JMeterExecutor().interpret(255) is Outcome.FAILED
