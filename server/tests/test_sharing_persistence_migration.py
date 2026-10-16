from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, ForeignKey, MetaData, String, Table, create_engine, inspect

from server.db.base import Base
from server.migrations.versions import add_canonical_sharing_model
from server.models.sharing import (
    SharingAuditEvent,
    SharingCompatibilityLink,
    SharingGrant,
    SharingSecret,
    SharingViewerSession,
)


def _create_minimal_legacy_schema(engine) -> None:
    metadata = MetaData()
    Table("users", metadata, Column("id", String(36), primary_key=True))
    Table(
        "tenants",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    )
    Table("folder_notebooks", metadata, Column("id", String(36), primary_key=True))
    Table("folder_dashboards", metadata, Column("id", String(36), primary_key=True))
    metadata.create_all(engine)


def test_canonical_sharing_models_are_registered() -> None:
    for model in (
        SharingGrant,
        SharingSecret,
        SharingViewerSession,
        SharingAuditEvent,
        SharingCompatibilityLink,
    ):
        assert model.__tablename__ in Base.metadata.tables


def test_canonical_sharing_migration_upgrade_and_downgrade_sqlite() -> None:
    engine = create_engine("sqlite:///:memory:")
    _create_minimal_legacy_schema(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = add_canonical_sharing_model.op
        add_canonical_sharing_model.op = operations
        try:
            add_canonical_sharing_model.upgrade()
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            assert {
                "sharing_grants",
                "sharing_secrets",
                "sharing_viewer_sessions",
                "sharing_audit_events",
                "sharing_compatibility_links",
            }.issubset(table_names)
            assert "folder_notebooks" in table_names
            assert "folder_dashboards" in table_names

            grant_columns = {column["name"] for column in inspector.get_columns("sharing_grants")}
            assert {"object_type", "object_id", "object_version_id", "object_version_digest", "status"}.issubset(
                grant_columns
            )
            secret_columns = {column["name"] for column in inspector.get_columns("sharing_secrets")}
            assert {"salt", "verifier_hash", "algorithm"}.issubset(secret_columns)
            session_columns = {column["name"] for column in inspector.get_columns("sharing_viewer_sessions")}
            assert {"grant_id", "object_id", "object_version_id", "token_digest"}.issubset(session_columns)

            add_canonical_sharing_model.downgrade()
            table_names = set(inspect(connection).get_table_names())
            assert "sharing_grants" not in table_names
            assert "folder_notebooks" in table_names
            assert "folder_dashboards" in table_names
        finally:
            add_canonical_sharing_model.op = original_op
