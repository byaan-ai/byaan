from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.source_resources import SourceResource
    from server.models.user import User


def generate_uuid() -> UUID:
    return uuid4()


SOURCE_CONNECTION_PROVIDERS = (
    "local_files",
    "web",
    "feishu",
    "sql_databases",
    "volcengine_tos",
    "databricks",
)
SOURCE_CONNECTION_AUTH_MODES = (
    "oauth",
    "access_key",
    "connection_string",
    "none",
)
SOURCE_CONNECTION_STATUSES = (
    "beta",
    "pending",
    "connected",
    "authorization_required",
    "reauthorization_required",
    "failed",
    "disconnected",
)


class SourceConnection(Base):
    __tablename__ = "source_connections"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    auth_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    external_account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="beta", index=True)
    capabilities_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    token_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True, index=True)
    created_by: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    creator: Mapped[User | None] = relationship("User", foreign_keys=[created_by])
    resources: Mapped[list[SourceResource]] = relationship(
        "SourceResource", back_populates="source_connection", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(f"provider IN {SOURCE_CONNECTION_PROVIDERS}", name="source_connections_provider"),
        CheckConstraint(f"auth_mode IN {SOURCE_CONNECTION_AUTH_MODES}", name="source_connections_auth_mode"),
        CheckConstraint(f"status IN {SOURCE_CONNECTION_STATUSES}", name="source_connections_status"),
        UniqueConstraint(
            "tenant_id",
            "provider",
            "created_by",
            "external_account_id",
            name="uq_source_connections_account",
        ),
    )
