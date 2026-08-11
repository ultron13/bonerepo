from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from plimsoll_api.db.base import Base


class Credential(Base):
    """Encrypted at rest and never returned by the API, only referenced by ID."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # GIT_SSH_KEY | GIT_TOKEN | GITHUB_APP | VARIABLE | KUBECONFIG
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # KMS / Vault key identifier.
    key_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScriptRepo(Base):
    __tablename__ = "script_repos"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    engine: Mapped[str] = mapped_column(String(50), nullable=False, server_default="jmeter")
    repo_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_ref: Mapped[str] = mapped_column(String(255), nullable=False, server_default="main")
    # e.g. perf/checkout.jmx
    plan_path: Mapped[str] = mapped_column(Text, nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("credentials.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="ACTIVE")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ScriptVersion(Base):
    """A commit SHA is immutable by construction, so the rule that every
    execution references an immutable version needs no enforcement machinery."""

    __tablename__ = "script_versions"
    __table_args__ = (UniqueConstraint("script_repo_id", "commit_sha", "plan_path"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    script_repo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("script_repos.id"), nullable=False
    )
    commit_sha: Mapped[str] = mapped_column(CHAR(40), nullable=False)
    plan_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Of the plan file at that commit.
    checksum: Mapped[str | None] = mapped_column(String(128), nullable=True)
    commit_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
