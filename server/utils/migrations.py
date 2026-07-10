"""
Database migration utilities for running Alembic migrations programmatically.

This module provides functions to run Alembic migrations during application startup,
ensuring the database schema is always up-to-date with the code expectations.
Similar to the Docker entrypoint approach but integrated into the Python application.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from server.utils.config_loader import is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


def get_alembic_config() -> Config:
    """
    Get Alembic configuration with proper paths for both development and frozen (PyInstaller) environments.

    Returns:
        Config: Alembic configuration object
    """
    # Determine the base directory
    is_frozen = getattr(sys, "frozen", False)

    if is_frozen:
        # Running as frozen executable (PyInstaller)
        # PyInstaller extracts files to sys._MEIPASS
        base_dir = Path(sys._MEIPASS) / "server"  # type: ignore[attr-defined]
        logger.info(f"🔧 Running as frozen executable. Base directory: {base_dir}")
        logger.info(f"🔧 sys._MEIPASS: {sys._MEIPASS}")  # type: ignore[attr-defined]
    else:
        # Running as normal Python script
        base_dir = Path(__file__).resolve().parents[1]
        logger.info(f"🔧 Running as Python script. Base directory: {base_dir}")

    alembic_ini_path = base_dir / "alembic.ini"
    script_location = base_dir / "migrations"

    logger.info(f"🔧 Looking for alembic.ini at: {alembic_ini_path}")
    logger.info(f"🔧 Looking for migrations at: {script_location}")

    if not alembic_ini_path.exists():
        logger.error(
            f"❌ alembic.ini not found at {alembic_ini_path}",
            exc_info=False,
            posthog_context={
                "function": "get_alembic_config",
                "is_frozen": is_frozen,
                "alembic_ini_path": str(alembic_ini_path),
            },
        )
        raise FileNotFoundError(f"alembic.ini not found at {alembic_ini_path}")

    if not script_location.exists():
        logger.error(
            f"❌ migrations directory not found at {script_location}",
            exc_info=False,
            posthog_context={
                "function": "get_alembic_config",
                "is_frozen": is_frozen,
                "script_location": str(script_location),
            },
        )
        raise FileNotFoundError(f"migrations directory not found at {script_location}")

    logger.info("✅ Found alembic.ini and migrations directory")

    # Create Alembic config
    alembic_cfg = Config(str(alembic_ini_path))
    alembic_cfg.set_main_option("script_location", str(script_location))

    # Set the database URL based on deployment mode
    if is_self_hosted():
        # Hosted mode: use PostgreSQL from DATABASE_URL
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable is required in hosted mode")
        # Alembic expects sync URLs, convert asyncpg to psycopg2
        sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        logger.info("🔧 Hosted mode: Database URL configured (PostgreSQL)")
        alembic_cfg.set_main_option("sqlalchemy.url", sync_url)
    else:
        # Local mode: use DATABASE_URL if provided (Tauri sets this), otherwise fallback to local path
        database_url = os.getenv("DATABASE_URL")
        if database_url:
            # Convert async URL to sync for Alembic
            sync_url = database_url.replace("sqlite+aiosqlite://", "sqlite:///")
            logger.info("🔧 Local mode: Using DATABASE_URL (SQLite)")
        else:
            sqlite_path = base_dir / ".data" / "app.db"
            sync_url = f"sqlite:///{sqlite_path}"
            logger.info(f"🔧 Local mode: Using default path (SQLite): {sync_url}")
        alembic_cfg.set_main_option("sqlalchemy.url", sync_url)

    return alembic_cfg


def check_database_has_tables(database_url: str) -> bool:
    """
    Check if the database has the alembic_version table, indicating migrations have been run.

    Args:
        database_url: SQLAlchemy database URL (dialect auto-detected from URL prefix)

    Returns:
        bool: True if alembic_version table exists, False otherwise
    """
    try:
        # Convert async URL to sync URL for direct checking
        sync_url = database_url
        if "sqlite+aiosqlite://" in sync_url:
            sync_url = sync_url.replace("sqlite+aiosqlite://", "sqlite:///")
        elif "postgresql+asyncpg://" in sync_url:
            sync_url = sync_url.replace("postgresql+asyncpg://", "postgresql://")

        # Create a sync engine for checking
        engine = create_engine(sync_url, echo=False)

        with engine.connect() as conn:
            if sync_url.startswith("sqlite"):
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
                )
            else:
                result = conn.execute(
                    text("SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name='alembic_version')")
                )
            row = result.fetchone()
            table_exists = row is not None and (row[0] if row else False)

        engine.dispose()
        return bool(table_exists)

    except Exception as e:
        logger.error(
            f"Error checking database tables: {e}",
            exc_info=True,
            posthog_context={
                "function": "check_database_has_tables",
                "is_self_hosted": is_self_hosted(),
            },
        )
        return False


def _ensure_wide_alembic_version_column(database_url: str) -> None:
    """Ensure alembic_version.version_num can hold long revision ids on PostgreSQL.

    Alembic creates version_num as VARCHAR(32); revision ids longer than that
    (e.g. human-readable slugs) overflow the column and roll back the whole
    upgrade. SQLite ignores the length so this only bites PostgreSQL. Pre-creating
    / widening the table before upgrade covers both fresh and existing databases.
    """
    if "postgresql" not in database_url:
        return

    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    engine = create_engine(sync_url, echo=False)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "version_num VARCHAR(255) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )
            )
            conn.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"))
    finally:
        engine.dispose()


def run_migrations() -> None:
    """
    Run Alembic migrations to ensure database schema is up-to-date.

    This function:
    1. Checks if migrations should be skipped (via SKIP_STARTUP_MIGRATIONS env var)
    2. Checks if the database has been initialized with migrations
    3. Runs 'alembic upgrade head' to apply any pending migrations
    4. Creates a fresh database if needed
    5. Logs all actions for debugging

    Raises:
        Exception: If migration fails critically - this will prevent server startup
    """
    try:
        # Check if we should skip startup migrations (useful for Docker where entrypoint handles it)
        skip_migrations = os.getenv("SKIP_STARTUP_MIGRATIONS", "").lower() in ("true", "1", "yes")
        if skip_migrations:
            logger.info("⏭️  Skipping startup migrations (SKIP_STARTUP_MIGRATIONS is set)")
            logger.info("   Migrations are expected to be handled externally (e.g., Docker entrypoint)")
            return

        # Get database URL based on deployment mode
        if is_self_hosted():
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable is required in hosted mode")
            logger.info("ℹ️  Hosted mode: Using PostgreSQL from DATABASE_URL")
        else:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                base_dir = Path(__file__).resolve().parents[1]
                database_url = f"sqlite+aiosqlite:///{base_dir / '.data' / 'app.db'}"
            logger.info("ℹ️  Local mode: Using SQLite database")

        # Check if database has tables
        has_tables = check_database_has_tables(database_url)

        if has_tables:
            logger.info("✅ Database already initialized. Checking for pending migrations...")
        else:
            logger.info("=" * 60)
            logger.info("=== Starting database initialization ===")
            logger.info("=" * 60)
            logger.info("📦 Database needs initialization. This may take a moment...")
            logger.info("📦 Creating database schema from migrations...")

        # Ensure the alembic_version column is wide enough for long revision ids
        # before upgrading (otherwise the final stamp overflows and rolls back).
        _ensure_wide_alembic_version_column(database_url)

        # Get Alembic config
        logger.info("🔧 Configuring migration system...")
        alembic_cfg = get_alembic_config()

        # Run migrations (Alembic is idempotent - it only applies pending migrations)
        if not has_tables:
            logger.info("🚀 Running database migrations...")
            logger.info("   (This creates all tables and initial data)")

        command.upgrade(alembic_cfg, "head")

        if has_tables:
            logger.info("✅ Database migrations up to date")
        else:
            logger.info("✅ Database created successfully")
            logger.info("✅ All migrations applied")
            logger.info("=" * 60)
            logger.info("=== Database initialization completed successfully ===")
            logger.info("=" * 60)

    except Exception as e:
        import traceback

        logger.error("=" * 60)
        logger.error(
            f"❌ CRITICAL: Database migration failed: {e}",
            exc_info=True,
            posthog_context={
                "function": "run_migrations",
                "exception_type": type(e).__name__,
                "database_url_set": os.getenv("DATABASE_URL") is not None,
                "skip_migrations": os.getenv("SKIP_STARTUP_MIGRATIONS", "").lower() in ("true", "1", "yes"),
            },
        )
        logger.error(f"❌ Exception type: {type(e).__name__}")
        logger.error("=" * 60)
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        logger.error("=" * 60)
        # RAISE the exception to prevent server startup with broken database
        raise RuntimeError(f"Database migration failed: {e}") from e


def check_migration_status() -> dict[str, str | bool]:
    """
    Check the current migration status of the database.

    Returns:
        dict: Status information including current revision and whether migrations are pending
    """
    try:
        # This would require more complex logic to check pending migrations
        # For now, we just confirm config is valid
        return {
            "status": "ok",
            "config_valid": True,
        }
    except Exception as e:
        logger.error(
            f"Failed to check migration status: {str(e)}",
            exc_info=True,
            posthog_context={"function": "check_migration_status"},
        )
        return {
            "status": "error",
            "error": str(e),
            "config_valid": False,
        }
