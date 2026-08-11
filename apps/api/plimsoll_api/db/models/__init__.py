"""Every model, re-exported so Alembic's autogenerate sees the whole metadata.

`performance_metrics` has no ORM model: it is written by the metrics worker in
slice 4 and defined in raw SQL in the migration, because it is a TimescaleDB
hypertable rather than an ordinary table.
"""

from __future__ import annotations

from plimsoll_api.db.models.identity import Organization, User
from plimsoll_api.db.models.platform import (
    ApiKey,
    AuditLog,
    IdempotencyKey,
    RefreshTokenFamily,
    TargetPolicy,
    WebhookSubscription,
)
from plimsoll_api.db.models.projects import Project, ProjectMember, ProjectRunCounter
from plimsoll_api.db.models.runs import (
    Baseline,
    GeneratorPool,
    RunError,
    RunGenerator,
    TestRun,
)
from plimsoll_api.db.models.scripts import Credential, ScriptRepo, ScriptVersion
from plimsoll_api.db.models.tests import PerformanceTest, PerformanceTestPlan, SlaRule

__all__ = [
    "ApiKey",
    "AuditLog",
    "Baseline",
    "Credential",
    "GeneratorPool",
    "IdempotencyKey",
    "Organization",
    "PerformanceTest",
    "PerformanceTestPlan",
    "Project",
    "ProjectMember",
    "ProjectRunCounter",
    "RefreshTokenFamily",
    "RunError",
    "RunGenerator",
    "ScriptRepo",
    "ScriptVersion",
    "SlaRule",
    "TargetPolicy",
    "TestRun",
    "User",
    "WebhookSubscription",
]
