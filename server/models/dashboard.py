from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.notebooks import Notebook
    from server.models.user import User


def generate_uuid() -> UUID:
    return uuid4()


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notebook_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False)
    version_num: Mapped[int] = mapped_column(Integer, nullable=False)
    html_content: Mapped[str] = mapped_column(Text, nullable=False)
    asset_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("dashboard_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    manifest_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="legacy_unstructured", index=True)
    created_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pinned_model_versions_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    pinned_source_snapshots_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    validation_result_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    renderer_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    migration_state: Mapped[str] = mapped_column(String(40), nullable=False, default="legacy_unstructured", index=True)
    is_published_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    notebook: Mapped[Notebook] = relationship("Notebook", back_populates="dashboards")
    asset: Mapped[DashboardAsset | None] = relationship(
        "DashboardAsset",
        back_populates="versions",
        foreign_keys=[asset_id],
    )
    creator: Mapped[User | None] = relationship("User", foreign_keys=[created_by])


class DashboardAsset(Base):
    __tablename__ = "dashboard_assets"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notebook_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("notebooks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    lifecycle: Mapped[str] = mapped_column(String(40), nullable=False, default="legacy_unstructured", index=True)
    current_draft_version_id: Mapped[UUID | None] = mapped_column(
        GUID(),
        ForeignKey(
            "dashboards.id",
            name="fk_dashboard_assets_current_draft_version_id_dashboards",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    published_version_id: Mapped[UUID | None] = mapped_column(
        GUID(),
        ForeignKey(
            "dashboards.id",
            name="fk_dashboard_assets_published_version_id_dashboards",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    access_policy_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    freshness_policy_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    consumer_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    health_summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    etag: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    notebook: Mapped[Notebook | None] = relationship("Notebook", foreign_keys=[notebook_id])
    owner: Mapped[User | None] = relationship("User", foreign_keys=[owner_id])
    versions: Mapped[list[Dashboard]] = relationship(
        "Dashboard",
        back_populates="asset",
        foreign_keys="Dashboard.asset_id",
    )
    current_draft_version: Mapped[Dashboard | None] = relationship("Dashboard", foreign_keys=[current_draft_version_id])
    published_version: Mapped[Dashboard | None] = relationship("Dashboard", foreign_keys=[published_version_id])

    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_dashboard_assets_tenant_slug"),)


class DashboardRun(Base):
    __tablename__ = "dashboard_runs"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("dashboard_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mode: Mapped[str] = mapped_column(String(40), nullable=False, default="live", index=True)
    normalized_filters_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    filter_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pinned_versions_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    execution_plan_digest: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    overall_freshness: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    result_manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    warnings_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    errors_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    asset: Mapped[DashboardAsset] = relationship("DashboardAsset", foreign_keys=[asset_id])
    version: Mapped[Dashboard] = relationship("Dashboard", foreign_keys=[version_id])

    __table_args__ = (UniqueConstraint("asset_id", "idempotency_key", name="uq_dashboard_runs_asset_idempotency"),)


class DashboardAuditEvent(Base):
    __tablename__ = "dashboard_audit_events"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("dashboard_assets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    version_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("dashboards.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("dashboard_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    before_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    after_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    asset: Mapped[DashboardAsset | None] = relationship("DashboardAsset", foreign_keys=[asset_id])
    version: Mapped[Dashboard | None] = relationship("Dashboard", foreign_keys=[version_id])
    run: Mapped[DashboardRun | None] = relationship("DashboardRun", foreign_keys=[run_id])
