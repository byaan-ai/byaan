from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, ForeignKey, MetaData, String, Table, create_engine, inspect

from server.db.base import Base
from server.migrations.versions import add_semantic_model_beta_foundation
from server.models.semantic_models import SemanticModel, SemanticModelAuditEvent, SemanticModelVersion


def _create_minimal_semantic_schema(engine) -> None:
    metadata = MetaData()
    Table("users", metadata, Column("id", String(36), primary_key=True))
    Table(
        "tenants",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    )
    metadata.create_all(engine)


def test_semantic_model_beta_models_are_registered() -> None:
    assert SemanticModel.__tablename__ in Base.metadata.tables
    assert SemanticModelVersion.__tablename__ in Base.metadata.tables
    assert SemanticModelAuditEvent.__tablename__ in Base.metadata.tables


def test_semantic_model_beta_migration_upgrade_and_downgrade_sqlite() -> None:
    engine = create_engine("sqlite:///:memory:")
    _create_minimal_semantic_schema(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = add_semantic_model_beta_foundation.op
        add_semantic_model_beta_foundation.op = operations
        try:
            add_semantic_model_beta_foundation.upgrade()
            inspector = inspect(connection)
            assert "semantic_models" in inspector.get_table_names()
            assert "semantic_model_versions" in inspector.get_table_names()
            assert "semantic_model_audit_events" in inspector.get_table_names()

            model_columns = {column["name"] for column in inspector.get_columns("semantic_models")}
            assert {"manifest_json", "source_snapshot_ids_json", "validation_result_json"}.issubset(model_columns)

            add_semantic_model_beta_foundation.downgrade()
            inspector = inspect(connection)
            assert "semantic_models" not in inspector.get_table_names()
            assert "semantic_model_versions" not in inspector.get_table_names()
            assert "semantic_model_audit_events" not in inspector.get_table_names()
        finally:
            add_semantic_model_beta_foundation.op = original_op
