from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base


def generate_uuid() -> UUID:
    return uuid4()


class SharingGrant(Base):
    __tablename__ = "sharing_grants"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    object_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    object_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    object_version_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    object_version_digest: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    mode: Mapped[str] = mapped_column(String(60), nullable=False, default="immutable_version", index=True)
    channel: Mapped[str] = mapped_column(String(60), nullable=False, default="public_link", index=True)
    audience: Mapped[str] = mapped_column(String(60), nullable=False, default="link_holder", index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active", index=True)
    created_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    secrets: Mapped[list[SharingSecret]] = relationship(
        "SharingSecret", back_populates="grant", cascade="all, delete-orphan"
    )
    viewer_sessions: Mapped[list[SharingViewerSession]] = relationship(
        "SharingViewerSession", back_populates="grant", cascade="all, delete-orphan"
    )


class SharingSecret(Base):
    __tablename__ = "sharing_secrets"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("sharing_grants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    secret_type: Mapped[str] = mapped_column(String(40), nullable=False, default="password", index=True)
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    salt: Mapped[str] = mapped_column(String(128), nullable=False)
    verifier_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="active", index=True)
    created_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    grant: Mapped[SharingGrant] = relationship("SharingGrant", back_populates="secrets")

    __table_args__ = (
        UniqueConstraint("grant_id", "secret_type", "status", name="uq_sharing_secrets_grant_type_status"),
    )


class SharingViewerSession(Base):
    __tablename__ = "sharing_viewer_sessions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("sharing_grants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    object_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    object_id: Mapped[UUID] = mapped_column(GUID(), nullable=False, index=True)
    object_version_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    token_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    token_digest: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    viewer_user_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    viewer_principal_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    issued_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    grant: Mapped[SharingGrant] = relationship("SharingGrant", back_populates="viewer_sessions")


class SharingAuditEvent(Base):
    __tablename__ = "sharing_audit_events"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grant_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("sharing_grants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    viewer_session_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("sharing_viewer_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    object_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    object_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    object_version_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())


class SharingCompatibilityLink(Base):
    __tablename__ = "sharing_compatibility_links"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("sharing_grants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legacy_surface: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    legacy_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    __table_args__ = (UniqueConstraint("legacy_surface", "legacy_id", name="uq_sharing_compatibility_links_legacy"),)
