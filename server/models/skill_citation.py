from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.custom_skill import CustomSkill
    from server.models.github_repository import GitHubRepository


class SkillCitation(Base):
    __tablename__ = "skill_citations"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("custom_skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    repo_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("github_repositories.id", ondelete="CASCADE"), nullable=False
    )

    path: Mapped[str] = mapped_column(String(500), nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blob_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    snippet_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    claim_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="valid", server_default="valid")

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp(), onupdate=datetime.now
    )

    skill: Mapped[CustomSkill] = relationship("CustomSkill", foreign_keys=[skill_id])
    repository: Mapped[GitHubRepository] = relationship("GitHubRepository", foreign_keys=[repo_id])

    __table_args__ = (Index("ix_skill_citations_repo_id_path", "repo_id", "path"),)
