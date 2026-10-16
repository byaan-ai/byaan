from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.user import User


def generate_uuid() -> UUID:
    return uuid4()


SEMANTIC_MODEL_STATUSES = ("beta", "draft", "needs_review", "validation_failed", "archived")
SEMANTIC_READINESS_LEVELS = ("blocked", "partial")


class SemanticModel(Base):
    __tablename__ = "semantic_models"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    datasource_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    datasource_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    datasource_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="source")
    contract_version: Mapped[str] = mapped_column(String(80), nullable=False, default="semantic.model.v1beta")
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_resource_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_snapshot_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="beta", index=True)
    readiness: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    readiness_level: Mapped[str] = mapped_column(String(32), nullable=False, default="partial")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    draft_revision: Mapped[str] = mapped_column(String(64), nullable=False, default="draft-1")
    published_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    consumer_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    creator: Mapped[User | None] = relationship("User", foreign_keys=[created_by])
    versions: Mapped[list[SemanticModelVersion]] = relationship(
        "SemanticModelVersion", back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )
    audit_events: Mapped[list[SemanticModelAuditEvent]] = relationship(
        "SemanticModelAuditEvent", back_populates="model", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_semantic_models_tenant_slug"),
        CheckConstraint(f"status IN {SEMANTIC_MODEL_STATUSES}", name="semantic_models_status"),
        CheckConstraint(f"readiness_level IN {SEMANTIC_READINESS_LEVELS}", name="semantic_models_readiness_level"),
    )


class SemanticModelVersion(Base):
    __tablename__ = "semantic_model_versions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("semantic_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_snapshot_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    validation_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    model: Mapped[SemanticModel] = relationship("SemanticModel", back_populates="versions")
    creator: Mapped[User | None] = relationship("User")

    __table_args__ = (UniqueConstraint("model_id", "version_label", name="uq_semantic_model_versions_model_label"),)


class SemanticModelAuditEvent(Base):
    __tablename__ = "semantic_model_audit_events"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("semantic_models.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    model: Mapped[SemanticModel] = relationship("SemanticModel", back_populates="audit_events")
    actor: Mapped[User | None] = relationship("User")
