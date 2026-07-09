from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.custom_skill import CustomSkill


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("custom_skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(20), nullable=False)
    suggestion_id: Mapped[UUID | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )

    skill: Mapped[CustomSkill] = relationship("CustomSkill", foreign_keys=[skill_id])

    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_id_version"),)
