from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import TIMESTAMP, Boolean, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.tenant import Tenant


class SkillLoopSettings(Base):
    __tablename__ = "skill_loop_settings"

    tenant_id: Mapped[UUID] = mapped_column(GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    digest_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=17)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp(), onupdate=datetime.now
    )

    tenant: Mapped[Tenant] = relationship("Tenant", foreign_keys=[tenant_id])
