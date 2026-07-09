from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import JSON, TIMESTAMP, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from server.models.notebooks import Notebook
    from server.models.tenant import Tenant


class ConversationEvaluation(Base):
    __tablename__ = "conversation_evaluations"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notebook_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger: Mapped[str] = mapped_column(String(10), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(15), nullable=True)
    findings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), default=datetime.now, server_default=func.current_timestamp()
    )

    tenant: Mapped[Tenant] = relationship("Tenant", foreign_keys=[tenant_id])
    notebook: Mapped[Notebook] = relationship("Notebook", foreign_keys=[notebook_id])
