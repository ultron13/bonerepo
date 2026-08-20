"""The executor interface.

ADR-0006 is explicit that this exists from day one but is validated with one
real implementation rather than designed against a hypothetical second. Keep it
that way: nothing here should be shaped by a k6 that does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Protocol


class Outcome(Enum):
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True)
class ExecutionContext:
    plan_path: Path
    working_directory: Path
    output_directory: Path
    threads: int
    ramp_up_seconds: int
    duration_seconds: int
    # Resolved values, held in memory only. They reach the engine as process
    # arguments inside the generator and are never written to disk.
    variables: dict[str, str] = field(default_factory=dict)


class Executor(Protocol):
    def command(self, context: ExecutionContext) -> list[str]: ...

    def artifacts(self, context: ExecutionContext) -> list[Path]: ...

    def interpret(self, exit_code: int) -> Outcome: ...
