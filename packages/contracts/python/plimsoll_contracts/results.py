"""What a run measured, as the interface sees it.

Percentiles appear here as numbers, but they are derived on read from merged
sketches -- never stored, never averaged. See ADR-0004.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TransactionSummary(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    transaction: str
    count: int
    error_count: int = Field(serialization_alias="errorCount")
    error_rate: float = Field(serialization_alias="errorRate")
    min: int
    max: int
    mean: float
    p50: int
    p90: int
    p95: int
    p99: int
    # Samples per second over the window span this transaction was observed in.
    throughput: float


class RunMetricsResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    run_id: uuid.UUID = Field(serialization_alias="runId")
    total_samples: int = Field(serialization_alias="totalSamples")
    total_errors: int = Field(serialization_alias="totalErrors")
    transactions: list[TransactionSummary]


class ErrorGroup(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    fingerprint: str
    error_code: str | None = Field(serialization_alias="errorCode")
    message: str | None
    transaction: str | None
    count: int
    first_seen: datetime = Field(serialization_alias="firstSeen")
    last_seen: datetime = Field(serialization_alias="lastSeen")
    # One readable occurrence: a count with no example is a number an operator
    # cannot act on.
    sample: str | None = None


class RunErrorsResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    run_id: uuid.UUID = Field(serialization_alias="runId")
    total: int
    items: list[ErrorGroup]
