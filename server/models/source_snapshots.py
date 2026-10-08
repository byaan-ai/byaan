from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.source_resources import SourceResource


def generate_uuid() -> UUID:
    return uuid4()


SOURCE_SNAPSHOT_STATUSES = ("pending", "captured", "parsed", "indexed", "failed")


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("source_resources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    raw_storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="captured", index=True)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    resource: Mapped[SourceResource] = relationship("SourceResource", back_populates="snapshots")

    __table_args__ = (CheckConstraint(f"status IN {SOURCE_SNAPSHOT_STATUSES}", name="source_snapshots_status"),)
