from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, ForeignKey, MetaData, String, Table, create_engine, inspect

from server.db.base import Base
from server.migrations.versions import add_evaluation_authoritative_model
from server.models.evaluation import (
    AdvisorChangeSet,
    AdvisorSuggestion,
    EvaluationArtifact,
    EvaluationAssessment,
    EvaluationAuditEvent,
    EvaluationCase,
    EvaluationCaseRun,
    EvaluationOverride,
    EvaluationRun,
    EvaluationSuite,
    EvaluationSuiteVersion,
    EvaluationTargetSnapshot,
    PromotionDecision,
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
    Table(
        "notebooks",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    )
    Table(
        "conversation_evaluations",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
        Column("notebook_id", String(36), ForeignKey("notebooks.id"), nullable=False),
    )
    Table(
        "skill_suggestions",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
    )
    metadata.create_all(engine)


def test_evaluation_persistence_models_are_registered() -> None:
    for model in (
        EvaluationSuite,
        EvaluationSuiteVersion,
        EvaluationCase,
        EvaluationTargetSnapshot,
        EvaluationRun,
        EvaluationCaseRun,
        EvaluationAssessment,
        EvaluationOverride,
        EvaluationArtifact,
        AdvisorChangeSet,
        AdvisorSuggestion,
        PromotionDecision,
        EvaluationAuditEvent,
    ):
        assert model.__tablename__ in Base.metadata.tables


def test_evaluation_authoritative_model_migration_upgrade_and_downgrade_sqlite() -> None:
    engine = create_engine("sqlite:///:memory:")
    _create_minimal_legacy_schema(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = add_evaluation_authoritative_model.op
        add_evaluation_authoritative_model.op = operations
        try:
            add_evaluation_authoritative_model.upgrade()
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            assert {
                "evaluation_suites",
                "evaluation_suite_versions",
                "evaluation_cases",
                "evaluation_target_snapshots",
                "evaluation_runs",
                "evaluation_case_runs",
                "evaluation_assessments",
                "evaluation_overrides",
                "evaluation_artifacts",
                "advisor_change_sets",
                "advisor_suggestions",
                "promotion_decisions",
                "evaluation_audit_events",
            }.issubset(table_names)
            assert "conversation_evaluations" in table_names
            assert "skill_suggestions" in table_names

            run_columns = {column["name"] for column in inspector.get_columns("evaluation_runs")}
            assert {"status", "preflight_blockers_json", "target_snapshot_id", "idempotency_key"}.issubset(run_columns)

            add_evaluation_authoritative_model.downgrade()
            table_names = set(inspect(connection).get_table_names())
            assert "evaluation_suites" not in table_names
            assert "conversation_evaluations" in table_names
            assert "skill_suggestions" in table_names
        finally:
            add_evaluation_authoritative_model.op = original_op
