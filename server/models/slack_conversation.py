from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.notebook import Notebook
    from server.models.slack_workspace import SlackWorkspace


class SlackConversation(Base):
    __tablename__ = "slack_conversations"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    slack_workspace_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("slack_workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slack_channel_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    slack_thread_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notebook_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("notebooks.id", ondelete="SET NULL"), nullable=True
    )
    slack_user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    thread_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bot_owned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    auto_follow_muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    last_activity_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )

    workspace: Mapped[SlackWorkspace] = relationship("SlackWorkspace", foreign_keys=[slack_workspace_id])
    notebook: Mapped[Notebook | None] = relationship("Notebook", foreign_keys=[notebook_id])

    __table_args__ = (
        UniqueConstraint(
            "slack_workspace_id",
            "slack_channel_id",
            "slack_thread_ts",
            name="uq_slack_conversation_workspace_channel_thread",
        ),
    )
