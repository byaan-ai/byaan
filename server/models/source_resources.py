from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.connections import Connection
    from server.models.source_connections import SourceConnection
    from server.models.source_snapshots import SourceSnapshot
    from server.models.user import User


def generate_uuid() -> UUID:
    return uuid4()


SOURCE_RESOURCE_TYPES = (
    "file",
    "pdf",
    "web",
    "feishu_doc",
    "feishu_wiki",
    "feishu_sheet",
    "feishu_base",
    "tos_bucket",
    "tos_prefix",
    "tos_object",
    "database_catalog",
    "database_schema",
    "database_table",
    "databricks_catalog",
    "databricks_schema",
    "databricks_table",
)
SOURCE_SYNC_MODES = ("manual", "scheduled")
SOURCE_RESOURCE_STATUSES = (
    "pending",
    "beta",
    "syncing",
    "understanding",
    "authorization_required",
    "reauthorization_required",
    "blocked",
    "source_unavailable",
    "permission_lost",
    "needs_confirmation",
    "ready",
    "failed",
)


class SourceResource(Base):
    __tablename__ = "source_resources"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_connection_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("source_connections.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    owner_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    visibility: Mapped[str] = mapped_column(String(30), nullable=False, default="workspace")
    sync_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    sync_config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="beta", index=True)
    latest_snapshot_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    connection: Mapped[Connection | None] = relationship("Connection", foreign_keys=[connection_id])
    source_connection: Mapped[SourceConnection | None] = relationship(
        "SourceConnection", foreign_keys=[source_connection_id], back_populates="resources"
    )
    owner: Mapped[User | None] = relationship("User", foreign_keys=[owner_id])
    snapshots: Mapped[list[SourceSnapshot]] = relationship(
        "SourceSnapshot", back_populates="resource", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(f"resource_type IN {SOURCE_RESOURCE_TYPES}", name="source_resources_resource_type"),
        CheckConstraint(f"sync_mode IN {SOURCE_SYNC_MODES}", name="source_resources_sync_mode"),
        CheckConstraint(f"status IN {SOURCE_RESOURCE_STATUSES}", name="source_resources_status"),
    )
