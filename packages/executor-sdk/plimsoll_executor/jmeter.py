"""Apache JMeter, driven headless, one process per generator."""

from __future__ import annotations

import os
from pathlib import Path

from plimsoll_executor.base import ExecutionContext, Executor, Outcome

JMETER_BINARY = os.environ.get("PLIMSOLL_JMETER_BINARY", "/opt/jmeter/bin/jmeter")
RESULTS_NAME = "results.jtl"
LOG_NAME = "jmeter.log"


class JMeterExecutor(Executor):
    def command(self, context: ExecutionContext) -> list[str]:
        command = [
            JMETER_BINARY,
            "-n",
            "-t",
            str(context.plan_path),
            "-l",
            str(context.output_directory / RESULTS_NAME),
            "-j",
            str(context.output_directory / LOG_NAME),
            f"-Jthreads={context.threads}",
            f"-Jrampup={context.ramp_up_seconds}",
            f"-Jduration={context.duration_seconds}",
        ]
        # Variables become properties for the same reason the workload does:
        # the plan reads ${NAME}, and rewriting the plan to bake a value in
        # would make the file unreproducible anywhere else.
        command += [f"-J{name}={value}" for name, value in sorted(context.variables.items())]
        return command

    def artifacts(self, context: ExecutionContext) -> list[Path]:
        return [
            context.output_directory / RESULTS_NAME,
            context.output_directory / LOG_NAME,
        ]

    def interpret(self, exit_code: int) -> Outcome:
        return Outcome.COMPLETED if exit_code == 0 else Outcome.FAILED
