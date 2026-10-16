"""add source connector foundation

Revision ID: add_source_connector_foundation
Revises: backfill_legacy_dashboard_assets
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.db.base import GUID

revision = "add_source_connector_foundation"
down_revision = "backfill_legacy_dashboard_assets"
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
    if "source_connections" not in existing:
        op.create_table(
            "source_connections",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("provider", sa.String(length=60), nullable=False),
            sa.Column("auth_mode", sa.String(length=30), nullable=False),
            sa.Column("encrypted_credentials", sa.Text(), nullable=False),
            sa.Column("external_account_id", sa.Text(), nullable=True),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="beta"),
            sa.Column("capabilities_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("token_expires_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_by", GUID(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.CheckConstraint(
                "provider IN ('local_files', 'web', 'feishu', 'sql_databases', 'volcengine_tos', 'databricks')",
                name=op.f("ck_source_connections_source_connections_provider"),
            ),
            sa.CheckConstraint(
                "auth_mode IN ('oauth', 'access_key', 'connection_string', 'none')",
                name=op.f("ck_source_connections_source_connections_auth_mode"),
            ),
            sa.CheckConstraint(
                "status IN ('beta', 'pending', 'connected', 'authorization_required', "
                "'reauthorization_required', 'failed', 'disconnected')",
                name=op.f("ck_source_connections_source_connections_status"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_source_connections")),
            sa.UniqueConstraint(
                "tenant_id",
                "provider",
                "created_by",
                "external_account_id",
                name="uq_source_connections_account",
            ),
        )
        for column_name in ("tenant_id", "provider", "status", "token_expires_at", "created_by"):
            op.create_index(f"ix_source_connections_{column_name}", "source_connections", [column_name])

    existing = _tables()
    if "source_resources" not in existing:
        op.create_table(
            "source_resources",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("connection_id", GUID(), nullable=True),
            sa.Column("source_connection_id", GUID(), nullable=True),
            sa.Column("resource_type", sa.String(length=40), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("external_id", sa.Text(), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("parent_external_id", sa.Text(), nullable=True),
            sa.Column("selection_config_json", sa.JSON(), nullable=True),
            sa.Column("owner_id", GUID(), nullable=True),
            sa.Column("visibility", sa.String(length=30), nullable=False, server_default="workspace"),
            sa.Column("sync_mode", sa.String(length=20), nullable=False, server_default="manual"),
            sa.Column("sync_config_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="beta"),
            sa.Column("latest_snapshot_id", GUID(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.CheckConstraint(
                "resource_type IN ('file', 'pdf', 'web', 'feishu_doc', 'feishu_wiki', 'feishu_sheet', "
                "'feishu_base', 'tos_bucket', 'tos_prefix', 'tos_object', 'database_catalog', "
                "'database_schema', 'database_table', 'databricks_catalog', 'databricks_schema', "
                "'databricks_table')",
                name=op.f("ck_source_resources_source_resources_resource_type"),
            ),
            sa.CheckConstraint(
                "sync_mode IN ('manual', 'scheduled')",
                name=op.f("ck_source_resources_source_resources_sync_mode"),
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'beta', 'syncing', 'understanding', 'authorization_required', "
                "'reauthorization_required', 'blocked', 'source_unavailable', 'permission_lost', "
                "'needs_confirmation', 'ready', 'failed')",
                name=op.f("ck_source_resources_source_resources_status"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["connection_id"], ["connections.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["source_connection_id"], ["source_connections.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_source_resources")),
        )
        for column_name in (
            "tenant_id",
            "connection_id",
            "source_connection_id",
            "owner_id",
            "status",
            "latest_snapshot_id",
        ):
            op.create_index(f"ix_source_resources_{column_name}", "source_resources", [column_name])

    existing = _tables()
    if "source_snapshots" not in existing:
        op.create_table(
            "source_snapshots",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("resource_id", GUID(), nullable=False),
            sa.Column("external_revision", sa.Text(), nullable=True),
            sa.Column("content_hash", sa.String(length=128), nullable=False),
            sa.Column("raw_storage_uri", sa.Text(), nullable=False),
            sa.Column("captured_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("parser_version", sa.String(length=100), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="captured"),
            sa.Column("error_json", sa.JSON(), nullable=True),
            sa.CheckConstraint(
                "status IN ('pending', 'captured', 'parsed', 'indexed', 'failed')",
                name=op.f("ck_source_snapshots_source_snapshots_status"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["resource_id"], ["source_resources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_source_snapshots")),
        )
        for column_name in ("tenant_id", "resource_id", "content_hash", "status"):
            op.create_index(f"ix_source_snapshots_{column_name}", "source_snapshots", [column_name])


def downgrade() -> None:
    if "source_snapshots" in _tables():
        for column_name in ("status", "content_hash", "resource_id", "tenant_id"):
            _drop_index_if_present(f"ix_source_snapshots_{column_name}", "source_snapshots")
        op.drop_table("source_snapshots")

    if "source_resources" in _tables():
        for column_name in (
            "latest_snapshot_id",
            "status",
            "owner_id",
            "source_connection_id",
            "connection_id",
            "tenant_id",
        ):
            _drop_index_if_present(f"ix_source_resources_{column_name}", "source_resources")
        op.drop_table("source_resources")

    if "source_connections" in _tables():
        for column_name in ("created_by", "token_expires_at", "status", "provider", "tenant_id"):
            _drop_index_if_present(f"ix_source_connections_{column_name}", "source_connections")
        op.drop_table("source_connections")
