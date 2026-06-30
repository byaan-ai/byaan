"""Add scope column to github_repositories for team sharing

Revision ID: add_scope_to_github_repositories
Revises: add_databricks_connection_type
Create Date: 2026-06-29

"""

import sqlalchemy as sa
from alembic import op

revision = "add_scope_to_github_repositories"
down_revision = "add_databricks_connection_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "github_repositories",
        sa.Column("scope", sa.String(length=10), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    op.drop_column("github_repositories", "scope")
