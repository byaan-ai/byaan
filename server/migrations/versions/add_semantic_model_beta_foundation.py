"""add semantic model beta foundation

Revision ID: add_semantic_model_beta_foundation
Revises: add_source_connector_foundation
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.db.base import GUID

revision = "add_semantic_model_beta_foundation"
down_revision = "add_source_connector_foundation"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _drop_index_if_present(index_name: str, table_name: str) -> None:
    if table_name not in _tables():
        return
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name in indexes:
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    existing = _tables()
    if "semantic_models" not in existing:
        op.create_table(
            "semantic_models",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("created_by", GUID(), nullable=True),
            sa.Column("slug", sa.String(length=160), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("domain", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("owner", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("datasource_id", sa.Text(), nullable=True),
            sa.Column("datasource_name", sa.Text(), nullable=True),
            sa.Column("datasource_kind", sa.String(length=64), nullable=False, server_default="source"),
            sa.Column("contract_version", sa.String(length=80), nullable=False, server_default="semantic.model.v1beta"),
            sa.Column("manifest_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("source_resource_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("source_snapshot_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="beta"),
            sa.Column("readiness", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("readiness_level", sa.String(length=32), nullable=False, server_default="partial"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("draft_revision", sa.String(length=64), nullable=False, server_default="draft-1"),
            sa.Column("published_version", sa.String(length=64), nullable=True),
            sa.Column("validation_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("consumer_summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.CheckConstraint(
                "status IN ('beta', 'draft', 'needs_review', 'validation_failed', 'archived')",
                name=op.f("ck_semantic_models_semantic_models_status"),
            ),
            sa.CheckConstraint(
                "readiness_level IN ('blocked', 'partial')",
                name=op.f("ck_semantic_models_semantic_models_readiness_level"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_semantic_models")),
            sa.UniqueConstraint("tenant_id", "slug", name="uq_semantic_models_tenant_slug"),
        )
        for column_name in ("tenant_id", "created_by", "datasource_id", "status"):
            op.create_index(f"ix_semantic_models_{column_name}", "semantic_models", [column_name])

    existing = _tables()
    if "semantic_model_versions" not in existing:
        op.create_table(
            "semantic_model_versions",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("model_id", GUID(), nullable=False),
            sa.Column("version_label", sa.String(length=64), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("source_snapshot_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("validation_result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("content_hash", sa.String(length=128), nullable=False),
            sa.Column("created_by", GUID(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["model_id"], ["semantic_models.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_semantic_model_versions")),
            sa.UniqueConstraint("model_id", "version_label", name="uq_semantic_model_versions_model_label"),
        )
        for column_name in ("tenant_id", "model_id", "created_by", "content_hash"):
            op.create_index(f"ix_semantic_model_versions_{column_name}", "semantic_model_versions", [column_name])

    existing = _tables()
    if "semantic_model_audit_events" not in existing:
        op.create_table(
            "semantic_model_audit_events",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("model_id", GUID(), nullable=False),
            sa.Column("actor_id", GUID(), nullable=True),
            sa.Column("action", sa.String(length=120), nullable=False),
            sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["model_id"], ["semantic_models.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_semantic_model_audit_events")),
        )
        for column_name in ("tenant_id", "model_id", "actor_id", "action"):
            op.create_index(
                f"ix_semantic_model_audit_events_{column_name}",
                "semantic_model_audit_events",
                [column_name],
            )


def downgrade() -> None:
    if "semantic_model_audit_events" in _tables():
        for column_name in ("action", "actor_id", "model_id", "tenant_id"):
            _drop_index_if_present(f"ix_semantic_model_audit_events_{column_name}", "semantic_model_audit_events")
        op.drop_table("semantic_model_audit_events")

    if "semantic_model_versions" in _tables():
        for column_name in ("content_hash", "created_by", "model_id", "tenant_id"):
            _drop_index_if_present(f"ix_semantic_model_versions_{column_name}", "semantic_model_versions")
        op.drop_table("semantic_model_versions")

    if "semantic_models" in _tables():
        for column_name in ("status", "datasource_id", "created_by", "tenant_id"):
            _drop_index_if_present(f"ix_semantic_models_{column_name}", "semantic_models")
        op.drop_table("semantic_models")
