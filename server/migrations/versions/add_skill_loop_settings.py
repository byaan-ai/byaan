"""add per-tenant skill loop settings

Revision ID: add_skill_loop_settings
Revises: add_slack_skill_review_columns
Create Date: 2026-07-09

Adds a per-tenant configuration table for the skill learning loop so tenants can
independently enable/disable the loop, toggle the digest, and pick a digest hour.
"""

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision = "add_skill_loop_settings"
down_revision = "add_slack_skill_review_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_loop_settings",
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("digest_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("digest_hour", sa.Integer(), nullable=False, server_default=sa.text("17")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_skill_loop_settings_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_skill_loop_settings")),
    )


def downgrade() -> None:
    op.drop_table("skill_loop_settings")
