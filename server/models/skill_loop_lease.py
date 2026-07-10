from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import Base


class SkillLoopLease(Base):
    """Single-row cooperative lease so only one worker process runs each skill-loop tick."""

    __tablename__ = "skill_loop_lease"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    holder: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
