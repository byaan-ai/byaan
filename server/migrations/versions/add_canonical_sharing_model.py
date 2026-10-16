"""add canonical sharing model

Revision ID: add_canonical_sharing_model
Revises: add_skill_loop_lease
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.db.base import GUID

revision = "add_canonical_sharing_model"
down_revision = "add_skill_loop_lease"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _drop_table_if_present(table_name: str) -> None:
    if table_name in _tables():
        op.drop_table(table_name)


def upgrade() -> None:
    existing = _tables()
    if "sharing_grants" not in existing:
        op.create_table(
            "sharing_grants",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("object_type", sa.String(length=60), nullable=False),
            sa.Column("object_id", GUID(), nullable=False),
            sa.Column("object_version_id", GUID(), nullable=True),
            sa.Column("object_version_digest", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("mode", sa.String(length=60), nullable=False, server_default="immutable_version"),
            sa.Column("channel", sa.String(length=60), nullable=False, server_default="public_link"),
            sa.Column("audience", sa.String(length=60), nullable=False, server_default="link_holder"),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
            sa.Column("created_by", GUID(), nullable=True),
            sa.Column("expires_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("revoked_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("revoked_by", GUID(), nullable=True),
            sa.Column("revocation_reason", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_sharing_grants")),
        )
        for column_name in (
            "tenant_id",
            "object_type",
            "object_id",
            "object_version_id",
            "mode",
            "channel",
            "audience",
            "status",
        ):
            op.create_index(f"ix_sharing_grants_{column_name}", "sharing_grants", [column_name])

    if "sharing_secrets" not in existing:
        op.create_table(
            "sharing_secrets",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("grant_id", GUID(), nullable=False),
            sa.Column("secret_type", sa.String(length=40), nullable=False, server_default="password"),
            sa.Column("algorithm", sa.String(length=60), nullable=False),
            sa.Column("salt", sa.String(length=128), nullable=False),
            sa.Column("verifier_hash", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
            sa.Column("created_by", GUID(), nullable=True),
            sa.Column("rotated_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("expires_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["grant_id"], ["sharing_grants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_sharing_secrets")),
            sa.UniqueConstraint("grant_id", "secret_type", "status", name="uq_sharing_secrets_grant_type_status"),
        )
        for column_name in ("tenant_id", "grant_id", "secret_type", "status"):
            op.create_index(f"ix_sharing_secrets_{column_name}", "sharing_secrets", [column_name])

    if "sharing_viewer_sessions" not in existing:
        op.create_table(
            "sharing_viewer_sessions",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("grant_id", GUID(), nullable=False),
            sa.Column("object_type", sa.String(length=60), nullable=False),
            sa.Column("object_id", GUID(), nullable=False),
            sa.Column("object_version_id", GUID(), nullable=True),
            sa.Column("token_id", sa.String(length=80), nullable=False),
            sa.Column("token_digest", sa.String(length=128), nullable=False),
            sa.Column("viewer_user_id", GUID(), nullable=True),
            sa.Column("viewer_principal_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("issued_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("expires_at", sa.TIMESTAMP(), nullable=False),
            sa.Column("revoked_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["grant_id"], ["sharing_grants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["viewer_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_sharing_viewer_sessions")),
            sa.UniqueConstraint("token_digest", name="uq_sharing_viewer_sessions_token_digest"),
        )
        for column_name in (
            "tenant_id",
            "grant_id",
            "object_type",
            "object_id",
            "object_version_id",
            "token_id",
        ):
            op.create_index(f"ix_sharing_viewer_sessions_{column_name}", "sharing_viewer_sessions", [column_name])

    if "sharing_audit_events" not in existing:
        op.create_table(
            "sharing_audit_events",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("grant_id", GUID(), nullable=True),
            sa.Column("viewer_session_id", GUID(), nullable=True),
            sa.Column("object_type", sa.String(length=60), nullable=False),
            sa.Column("object_id", GUID(), nullable=True),
            sa.Column("object_version_id", GUID(), nullable=True),
            sa.Column("actor_type", sa.String(length=40), nullable=False),
            sa.Column("actor_id", sa.String(length=160), nullable=False),
            sa.Column("action", sa.String(length=120), nullable=False),
            sa.Column("outcome", sa.String(length=40), nullable=False),
            sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["grant_id"], ["sharing_grants.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["viewer_session_id"], ["sharing_viewer_sessions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_sharing_audit_events")),
        )
        for column_name in (
            "tenant_id",
            "grant_id",
            "viewer_session_id",
            "object_type",
            "object_id",
            "object_version_id",
            "actor_id",
            "action",
            "outcome",
        ):
            op.create_index(f"ix_sharing_audit_events_{column_name}", "sharing_audit_events", [column_name])

    if "sharing_compatibility_links" not in existing:
        op.create_table(
            "sharing_compatibility_links",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("grant_id", GUID(), nullable=False),
            sa.Column("legacy_surface", sa.String(length=80), nullable=False),
            sa.Column("legacy_id", sa.String(length=255), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["grant_id"], ["sharing_grants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_sharing_compatibility_links")),
            sa.UniqueConstraint("legacy_surface", "legacy_id", name="uq_sharing_compatibility_links_legacy"),
        )
        for column_name in ("tenant_id", "grant_id", "legacy_surface", "legacy_id"):
            op.create_index(
                f"ix_sharing_compatibility_links_{column_name}", "sharing_compatibility_links", [column_name]
            )


def downgrade() -> None:
    for table_name in (
        "sharing_compatibility_links",
        "sharing_audit_events",
        "sharing_viewer_sessions",
        "sharing_secrets",
        "sharing_grants",
    ):
        _drop_table_if_present(table_name)
