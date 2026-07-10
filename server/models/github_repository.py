from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, Text, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.custom_skill import CustomSkill
    from server.models.tenant import Tenant
    from server.models.user import User


class GitHubRepository(Base):
    __tablename__ = "github_repositories"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    source: Mapped[str] = mapped_column(String(20), nullable=False, default="github", server_default="github")
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), nullable=False, default="main")
    tracked_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)
    skill_sync_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_analyzed_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    language_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="user", server_default="user")

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp(), onupdate=datetime.now
    )

    tenant: Mapped[Tenant] = relationship("Tenant", foreign_keys=[tenant_id])
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    skills: Mapped[list[CustomSkill]] = relationship(
        "CustomSkill", foreign_keys="CustomSkill.github_repo_id", viewonly=True
    )

    __table_args__ = (UniqueConstraint("tenant_id", "repo_full_name", name="uq_github_repositories_tenant_repo"),)

    @property
    def effective_branch(self) -> str:
        return self.tracked_branch or self.default_branch
