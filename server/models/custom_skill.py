from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.github_repository import GitHubRepository
    from server.models.tenant import Tenant
    from server.models.user import User


class CustomSkill(Base):
    __tablename__ = "custom_skills"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)

    github_repo_id: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("github_repositories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    github_analysis_type: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)

    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="user")
    skill_type: Mapped[str] = mapped_column(String(30), nullable=False, default="general", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    api_base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_type: Mapped[str | None] = mapped_column(String(20), nullable=True, default="rest")
    api_auth_type: Mapped[str | None] = mapped_column(String(20), nullable=True, default="bearer")
    api_auth_header: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_domain: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp(), onupdate=datetime.now
    )

    creator: Mapped[User] = relationship("User", foreign_keys=[created_by])
    tenant: Mapped[Tenant] = relationship("Tenant", foreign_keys=[tenant_id])
    github_repository: Mapped[GitHubRepository | None] = relationship("GitHubRepository", foreign_keys=[github_repo_id])

    @property
    def can_execute_api(self) -> bool:
        return bool(self.api_base_url and self.api_credentials_encrypted)
