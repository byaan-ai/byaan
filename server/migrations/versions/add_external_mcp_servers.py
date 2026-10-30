"""Add external MCP servers table

Revision ID: add_external_mcp_servers
Revises: add_schedule_run_slack_lookup
Create Date: 2026-10-30

"""

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision = "add_external_mcp_servers"
down_revision = "add_schedule_run_slack_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_mcp_servers",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("created_by", GUID(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("config", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("tool_filter", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_mcp_servers")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_external_mcp_servers_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_external_mcp_servers_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_external_mcp_tenant_name"),
    )
    op.create_index(op.f("ix_external_mcp_servers_tenant_id"), "external_mcp_servers", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_external_mcp_servers_created_by"), "external_mcp_servers", ["created_by"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_external_mcp_servers_created_by"), table_name="external_mcp_servers")
    op.drop_index(op.f("ix_external_mcp_servers_tenant_id"), table_name="external_mcp_servers")
    op.drop_table("external_mcp_servers")
