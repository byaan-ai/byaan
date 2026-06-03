from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import TIMESTAMP, Boolean, CheckConstraint, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from server.db.base import GUID, Base

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def generate_uuid() -> UUID:
    return uuid4()


ALLOWED_CONN_TYPES = ("pg", "mysql", "mongo", "sqlite", "mssql", "dynamodb", "databricks")


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[UUID] = mapped_column(
        GUID(), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connection_obj_encrypted: Mapped[str] = mapped_column("connection_obj", Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False), server_default=func.current_timestamp())

    schema_cache: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=False), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    __table_args__ = (CheckConstraint(f"type IN {ALLOWED_CONN_TYPES}", name="ck_connections_type_allowed"),)

    async def get_decrypted_connection_obj(self, session: AsyncSession) -> dict | None:
        if not self.connection_obj_encrypted:
            return None

        from server.services.crypto_service import CryptoService

        try:
            return await CryptoService.decrypt_config(self.connection_obj_encrypted, session)
        except Exception:
            try:
                return json.loads(self.connection_obj_encrypted)
            except Exception:
                return None

    async def set_encrypted_connection_obj(self, value: dict | None, session: AsyncSession) -> None:
        if value is None:
            self.connection_obj_encrypted = ""
        else:
            from server.services.crypto_service import CryptoService

            self.connection_obj_encrypted = await CryptoService.encrypt_config(value, session)
