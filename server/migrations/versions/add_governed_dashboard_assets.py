"""add governed dashboard assets

Revision ID: add_governed_dashboard_assets
Revises: add_evaluation_authoritative_model
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision = "add_governed_dashboard_assets"
down_revision = "add_evaluation_authoritative_model"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_dashboard_column_if_missing(column: sa.Column) -> None:
    if "dashboards" not in _tables() or column.name in _columns("dashboards"):
        return
    with op.batch_alter_table("dashboards") as batch_op:
        batch_op.add_column(column)


def _drop_dashboard_column_if_present(column_name: str) -> None:
    if "dashboards" not in _tables() or column_name not in _columns("dashboards"):
        return
    with op.batch_alter_table("dashboards") as batch_op:
        batch_op.drop_column(column_name)


def upgrade() -> None:
    if "dashboard_assets" not in _tables():
        op.create_table(
            "dashboard_assets",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("notebook_id", GUID(), nullable=True),
            sa.Column("slug", sa.String(length=160), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("owner_id", GUID(), nullable=True),
            sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("lifecycle", sa.String(length=40), nullable=False, server_default="legacy_unstructured"),
            sa.Column("current_draft_version_id", GUID(), nullable=True),
            sa.Column("published_version_id", GUID(), nullable=True),
            sa.Column("access_policy_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("freshness_policy_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("consumer_summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("health_summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("etag", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["notebook_id"], ["notebooks.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["current_draft_version_id"],
                ["dashboards.id"],
                name="fk_dashboard_assets_current_draft_version_id_dashboards",
                ondelete="SET NULL",
                use_alter=True,
            ),
            sa.ForeignKeyConstraint(
                ["published_version_id"],
                ["dashboards.id"],
                name="fk_dashboard_assets_published_version_id_dashboards",
                ondelete="SET NULL",
                use_alter=True,
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_assets")),
            sa.UniqueConstraint("tenant_id", "slug", name="uq_dashboard_assets_tenant_slug"),
        )
        op.create_index("ix_dashboard_assets_tenant_id", "dashboard_assets", ["tenant_id"])
        op.create_index("ix_dashboard_assets_notebook_id", "dashboard_assets", ["notebook_id"])
        op.create_index("ix_dashboard_assets_lifecycle", "dashboard_assets", ["lifecycle"])

    dashboard_columns = [
        sa.Column("asset_id", GUID(), nullable=True),
        sa.Column("manifest_schema_version", sa.String(length=64), nullable=True),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="legacy_unstructured"),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("actor_type", sa.String(length=40), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("pinned_model_versions_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("pinned_source_snapshots_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("validation_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("renderer_version", sa.String(length=80), nullable=True),
        sa.Column("migration_state", sa.String(length=40), nullable=False, server_default="legacy_unstructured"),
        sa.Column("is_published_immutable", sa.Boolean(), nullable=False, server_default=sa.false()),
    ]
    for column in dashboard_columns:
        _add_dashboard_column_if_missing(column)

    if "dashboards" in _tables():
        existing_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("dashboards")}
        for index_name, column_name in (
            ("ix_dashboards_asset_id", "asset_id"),
            ("ix_dashboards_content_hash", "content_hash"),
            ("ix_dashboards_status", "status"),
            ("ix_dashboards_migration_state", "migration_state"),
        ):
            if index_name not in existing_indexes and column_name in _columns("dashboards"):
                op.create_index(index_name, "dashboards", [column_name])

    if "dashboard_runs" not in _tables():
        op.create_table(
            "dashboard_runs",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("asset_id", GUID(), nullable=False),
            sa.Column("version_id", GUID(), nullable=False),
            sa.Column("actor_type", sa.String(length=40), nullable=False),
            sa.Column("actor_id", sa.String(length=160), nullable=False),
            sa.Column("correlation_id", sa.String(length=160), nullable=True),
            sa.Column("session_id", sa.String(length=160), nullable=True),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("mode", sa.String(length=40), nullable=False, server_default="live"),
            sa.Column("normalized_filters_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("filter_digest", sa.String(length=128), nullable=False),
            sa.Column("pinned_versions_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("execution_plan_digest", sa.String(length=128), nullable=False),
            sa.Column("overall_freshness", sa.String(length=40), nullable=False, server_default="unknown"),
            sa.Column("result_manifest_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("warnings_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("errors_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("started_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("completed_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["dashboard_assets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["version_id"], ["dashboards.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_runs")),
            sa.UniqueConstraint("asset_id", "idempotency_key", name="uq_dashboard_runs_asset_idempotency"),
        )
        for column_name in (
            "tenant_id",
            "asset_id",
            "version_id",
            "actor_id",
            "correlation_id",
            "mode",
            "filter_digest",
            "execution_plan_digest",
        ):
            op.create_index(f"ix_dashboard_runs_{column_name}", "dashboard_runs", [column_name])

    if "dashboard_audit_events" not in _tables():
        op.create_table(
            "dashboard_audit_events",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("asset_id", GUID(), nullable=True),
            sa.Column("version_id", GUID(), nullable=True),
            sa.Column("run_id", GUID(), nullable=True),
            sa.Column("actor_type", sa.String(length=40), nullable=False),
            sa.Column("actor_id", sa.String(length=160), nullable=False),
            sa.Column("action", sa.String(length=120), nullable=False),
            sa.Column("correlation_id", sa.String(length=160), nullable=True),
            sa.Column("before_digest", sa.String(length=128), nullable=True),
            sa.Column("after_digest", sa.String(length=128), nullable=True),
            sa.Column("outcome", sa.String(length=40), nullable=False),
            sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["dashboard_assets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["version_id"], ["dashboards.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["run_id"], ["dashboard_runs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard_audit_events")),
        )
        for column_name in ("tenant_id", "asset_id", "version_id", "run_id", "actor_id", "action", "correlation_id"):
            op.create_index(f"ix_dashboard_audit_events_{column_name}", "dashboard_audit_events", [column_name])


def downgrade() -> None:
    if "dashboard_audit_events" in _tables():
        for column_name in ("correlation_id", "action", "actor_id", "run_id", "version_id", "asset_id", "tenant_id"):
            op.drop_index(f"ix_dashboard_audit_events_{column_name}", table_name="dashboard_audit_events")
        op.drop_table("dashboard_audit_events")

    if "dashboard_runs" in _tables():
        for column_name in (
            "execution_plan_digest",
            "filter_digest",
            "mode",
            "correlation_id",
            "actor_id",
            "version_id",
            "asset_id",
            "tenant_id",
        ):
            op.drop_index(f"ix_dashboard_runs_{column_name}", table_name="dashboard_runs")
        op.drop_table("dashboard_runs")

    if "dashboards" in _tables():
        existing_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("dashboards")}
        for index_name in (
            "ix_dashboards_migration_state",
            "ix_dashboards_status",
            "ix_dashboards_content_hash",
            "ix_dashboards_asset_id",
        ):
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name="dashboards")

    for column_name in (
        "is_published_immutable",
        "migration_state",
        "renderer_version",
        "validation_result_json",
        "pinned_source_snapshots_json",
        "pinned_model_versions_json",
        "change_summary",
        "actor_type",
        "created_by",
        "status",
        "content_hash",
        "manifest_json",
        "manifest_schema_version",
        "asset_id",
    ):
        _drop_dashboard_column_if_present(column_name)

    if "dashboard_assets" in _tables():
        op.drop_index("ix_dashboard_assets_lifecycle", table_name="dashboard_assets")
        op.drop_index("ix_dashboard_assets_notebook_id", table_name="dashboard_assets")
        op.drop_index("ix_dashboard_assets_tenant_id", table_name="dashboard_assets")
        op.drop_table("dashboard_assets")
