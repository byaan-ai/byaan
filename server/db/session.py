from __future__ import annotations

import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.utils.config_loader import is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]


def get_database_url() -> str:
    """
    Get database URL based on deployment mode.

    - Hosted mode (isHosted=true): Use PostgreSQL from DATABASE_URL env var
    - Local mode (isHosted=false): Use SQLite local file
    """
    if is_self_hosted():
        # Hosted mode: require DATABASE_URL for PostgreSQL
        url = os.getenv("DATABASE_URL")
        if not url:
            raise ValueError("DATABASE_URL environment variable is required in hosted mode")
        return url
    else:
        url = os.getenv("DATABASE_URL")
        if url:
            return url
        if getattr(sys, "frozen", False):
            from server.config.storage import DEFAULT_DATA_DIR

            return f"sqlite+aiosqlite:///{DEFAULT_DATA_DIR / 'app.db'}"
        return f"sqlite+aiosqlite:///{BASE_DIR / '.data' / 'app.db'}"


DATABASE_URL = get_database_url()


async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
)


async def ensure_database_schema() -> None:
    """
    Ensure database schema is up-to-date by running Alembic migrations.

    This replaces the old create_all() approach which couldn't handle
    breaking schema changes. Now we run proper migrations that can:
    - Create fresh databases from scratch
    - Migrate existing databases with breaking changes
    - Handle column additions, deletions, and modifications
    """
    # Run migrations in a thread pool to avoid blocking async code
    import asyncio

    from server.utils.migrations import run_migrations

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_migrations)


async def ensure_database_encoding() -> None:
    """
    Check PostgreSQL database encoding and log warning if not UTF-8.
    This is a non-fatal check - the application will continue regardless.
    """
    if not is_self_hosted():
        return

    try:
        from sqlalchemy import text

        async with AsyncSessionFactory() as session:
            result = await session.execute(
                text(
                    "SELECT current_database(), pg_encoding_to_char(encoding) "
                    "FROM pg_database WHERE datname = current_database()"
                )
            )
            row = result.fetchone()
            if row:
                db_name, encoding = row
                if encoding and encoding.upper() != "UTF8":
                    logger.warning(
                        f"PostgreSQL database '{db_name}' uses {encoding} encoding. "
                        f"UTF8 is recommended for full Unicode support. "
                        f"To fix: CREATE DATABASE {db_name} WITH ENCODING 'UTF8' "
                        f"LC_COLLATE='en_US.UTF-8' LC_CTYPE='en_US.UTF-8' TEMPLATE=template0;"
                    )
                else:
                    logger.info(f"PostgreSQL database encoding: {encoding}")
    except Exception as e:
        logger.warning(f"Could not check database encoding (non-fatal): {e}")


# SQLite needs this to enforce foreign key constraints (disabled by default)
# PostgreSQL enforces foreign keys by default, so gate on dialect not deployment mode
if async_engine.dialect.name == "sqlite":

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


AsyncSessionFactory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        yield session


@asynccontextmanager
async def get_async_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for use outside of FastAPI dependency injection (e.g., workers)."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
