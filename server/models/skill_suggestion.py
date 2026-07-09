from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.custom_skill import CustomSkill
    from server.models.tenant import Tenant
    from server.models.user import User


class SkillSuggestion(Base):
    __tablename__ = "skill_suggestions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    skill_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("custom_skills.id", ondelete="SET NULL"), nullable=True, index=True
    )

    suggestion_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    patch: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    source: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    reviewed_by: Mapped[UUID | None] = mapped_column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_via: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reviewer_slack_user_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewer_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)

    slack_channel_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slack_message_ts: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp(), onupdate=datetime.now
    )

    tenant: Mapped[Tenant] = relationship("Tenant", foreign_keys=[tenant_id])
    skill: Mapped[CustomSkill | None] = relationship("CustomSkill", foreign_keys=[skill_id])
    reviewer: Mapped[User | None] = relationship("User", foreign_keys=[reviewed_by])
