"""What a run measured, as the interface sees it.

Percentiles appear here as numbers, but they are derived on read from merged
sketches -- never stored, never averaged. See ADR-0004.
"""

from __future__ import annotations

import uuid

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
