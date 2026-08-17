from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.tenant import Tenant
    from server.models.user import User


class EvaluationSuite(Base):
    __tablename__ = "evaluation_suites"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_kinds_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    lifecycle: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    current_draft_version_id: Mapped[UUID | None] = mapped_column(
        GUID(),
        ForeignKey(
            "evaluation_suite_versions.id",
            name="fk_evaluation_suites_current_draft_version_id_versions",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    published_version_id: Mapped[UUID | None] = mapped_column(
        GUID(),
        ForeignKey(
            "evaluation_suite_versions.id",
            name="fk_evaluation_suites_published_version_id_versions",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", foreign_keys=[tenant_id])
    owner: Mapped[User | None] = relationship("User", foreign_keys=[owner_id])
    versions: Mapped[list[EvaluationSuiteVersion]] = relationship(
        "EvaluationSuiteVersion",
        back_populates="suite",
        foreign_keys="EvaluationSuiteVersion.suite_id",
    )
    current_draft_version: Mapped[EvaluationSuiteVersion | None] = relationship(
        "EvaluationSuiteVersion", foreign_keys=[current_draft_version_id]
    )
    published_version: Mapped[EvaluationSuiteVersion | None] = relationship(
        "EvaluationSuiteVersion", foreign_keys=[published_version_id]
    )

    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_evaluation_suites_tenant_slug"),)


class EvaluationSuiteVersion(Base):
    __tablename__ = "evaluation_suite_versions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    suite_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("evaluation_suites.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    gate_policy_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    created_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    suite: Mapped[EvaluationSuite] = relationship(
        "EvaluationSuite", back_populates="versions", foreign_keys=[suite_id]
    )
    creator: Mapped[User | None] = relationship("User", foreign_keys=[created_by])
    cases: Mapped[list[EvaluationCase]] = relationship(back_populates="suite_version", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("suite_id", "version_num", name="uq_evaluation_suite_versions_suite_version"),)


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    suite_version_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("evaluation_suite_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_kinds_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected_contract_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    provenance_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    suite_version: Mapped[EvaluationSuiteVersion] = relationship(back_populates="cases")

    __table_args__ = (UniqueConstraint("suite_version_id", "case_key", name="uq_evaluation_cases_version_key"),)


class EvaluationTargetSnapshot(Base):
    __tablename__ = "evaluation_target_snapshots"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_kind: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    contract_version: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    pin_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    blockers_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    suite_version_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("evaluation_suite_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_snapshot_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("evaluation_target_snapshots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False, default="human")
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    baseline_run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_runs.id"), nullable=True)
    candidate_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_holder: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    preflight_blockers_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    suite_version: Mapped[EvaluationSuiteVersion] = relationship("EvaluationSuiteVersion", foreign_keys=[suite_version_id])
    target_snapshot: Mapped[EvaluationTargetSnapshot] = relationship("EvaluationTargetSnapshot", foreign_keys=[target_snapshot_id])

    __table_args__ = (UniqueConstraint("suite_version_id", "idempotency_key", name="uq_evaluation_runs_suite_idempotency"),)


class EvaluationCaseRun(Base):
    __tablename__ = "evaluation_case_runs"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("evaluation_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    input_digest: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    output_digest: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class EvaluationAssessment(Base):
    __tablename__ = "evaluation_assessments"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    case_run_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("evaluation_case_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    score: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hard_fail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class EvaluationOverride(Base):
    __tablename__ = "evaluation_overrides"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("evaluation_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    override_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class EvaluationArtifact(Base):
    __tablename__ = "evaluation_artifacts"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    case_run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_case_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    artifact_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class AdvisorChangeSet(Base):
    __tablename__ = "advisor_change_sets"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    suite_version_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_suite_versions.id", ondelete="SET NULL"), nullable=True)
    target_ref: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    base_version_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    base_etag: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    verification_run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_runs.id"), nullable=True)
    regression_run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_runs.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class AdvisorSuggestion(Base):
    __tablename__ = "advisor_suggestions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    change_set_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("advisor_change_sets.id", ondelete="CASCADE"), nullable=False, index=True)
    suggestion_type: Mapped[str] = mapped_column(String(80), nullable=False)
    patch_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    affected_case_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class PromotionDecision(Base):
    __tablename__ = "promotion_decisions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    change_set_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("advisor_change_sets.id", ondelete="SET NULL"), nullable=True)
    verification_run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_runs.id"), nullable=True)
    regression_run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_runs.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audit_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class EvaluationAuditEvent(Base):
    __tablename__ = "evaluation_audit_events"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    suite_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_suites.id", ondelete="SET NULL"), nullable=True, index=True)
    suite_version_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_suite_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    run_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("evaluation_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
