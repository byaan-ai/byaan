from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.llm_connections import LLMConnection
    from server.models.tenant import Tenant
    from server.models.user import User


class SlackWorkspace(Base):
    __tablename__ = "slack_workspaces"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_team_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    slack_team_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bot_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    bot_user_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    signing_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    default_llm_connection_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("llm_connections.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reviewers_channel_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    installed_by: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp(), onupdate=datetime.now
    )

    tenant: Mapped[Tenant] = relationship("Tenant", foreign_keys=[tenant_id])
    default_llm_connection: Mapped[LLMConnection | None] = relationship(
        "LLMConnection", foreign_keys=[default_llm_connection_id]
    )
    installed_by_user: Mapped[User | None] = relationship("User", foreign_keys=[installed_by])
