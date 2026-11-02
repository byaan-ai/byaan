"""Add api_auth_header column to custom_skills table

Revision ID: add_skill_api_auth_header
Revises: add_external_mcp_servers
Create Date: 2026-11-01

"""

import sqlalchemy as sa
from alembic import op

revision = "add_skill_api_auth_header"
down_revision = "add_external_mcp_servers"
branch_labels = None
depends_on = None


def _existing_columns() -> list[str]:
    inspector = sa.inspect(op.get_bind())
    return [c["name"] for c in inspector.get_columns("custom_skills")]


def upgrade() -> None:
    if "api_auth_header" not in _existing_columns():
        op.add_column("custom_skills", sa.Column("api_auth_header", sa.String(100), nullable=True))


def downgrade() -> None:
    if "api_auth_header" in _existing_columns():
        op.drop_column("custom_skills", "api_auth_header")
