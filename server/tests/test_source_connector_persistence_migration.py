from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, ForeignKey, MetaData, String, Table, create_engine, inspect

from server.db.base import Base
from server.migrations.versions import add_source_connector_foundation
from server.models.source_connections import SourceConnection
from server.models.source_resources import SourceResource
from server.models.source_snapshots import SourceSnapshot


def _create_minimal_source_schema(engine) -> None:
    metadata = MetaData()
    Table("users", metadata, Column("id", String(36), primary_key=True))
    Table(
        "tenants",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    )
    Table("connections", metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)


def test_source_persistence_models_are_registered() -> None:
    assert SourceConnection.__tablename__ in Base.metadata.tables
    assert SourceResource.__tablename__ in Base.metadata.tables
    assert SourceSnapshot.__tablename__ in Base.metadata.tables


def test_source_connector_migration_upgrade_and_downgrade_sqlite() -> None:
    engine = create_engine("sqlite:///:memory:")
    _create_minimal_source_schema(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = add_source_connector_foundation.op
        add_source_connector_foundation.op = operations
        try:
            add_source_connector_foundation.upgrade()
            inspector = inspect(connection)
            assert "source_connections" in inspector.get_table_names()
            assert "source_resources" in inspector.get_table_names()
            assert "source_snapshots" in inspector.get_table_names()

            connection_columns = {column["name"] for column in inspector.get_columns("source_connections")}
            assert {"encrypted_credentials", "capabilities_json", "status"}.issubset(connection_columns)
            resource_columns = {column["name"] for column in inspector.get_columns("source_resources")}
            assert {"source_connection_id", "latest_snapshot_id", "sync_config_json"}.issubset(resource_columns)

            add_source_connector_foundation.downgrade()
            inspector = inspect(connection)
            assert "source_connections" not in inspector.get_table_names()
            assert "source_resources" not in inspector.get_table_names()
            assert "source_snapshots" not in inspector.get_table_names()
        finally:
            add_source_connector_foundation.op = original_op
