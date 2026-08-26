"""add default_model to slack_workspaces

Revision ID: add_slack_default_model
Revises: add_skill_loop_lease
Create Date: 2026-08-26

"""

import sqlalchemy as sa
from alembic import op

revision = "add_slack_default_model"
down_revision = "add_skill_loop_lease"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "slack_workspaces",
        sa.Column("default_model", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("slack_workspaces", "default_model")
