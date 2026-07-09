"""add slack skill review columns

Revision ID: add_slack_skill_review_columns
Revises: add_skill_learning_loop_tables
Create Date: 2026-07-09

Adds a human-readable thread title to Slack conversations (derived from the
inbound message) and a per-workspace reviewers channel used to route skill
learning-loop review cards.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_slack_skill_review_columns"
down_revision = "add_skill_learning_loop_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("slack_conversations") as batch_op:
        batch_op.add_column(sa.Column("thread_title", sa.String(500), nullable=True))

    with op.batch_alter_table("slack_workspaces") as batch_op:
        batch_op.add_column(sa.Column("reviewers_channel_id", sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("slack_workspaces") as batch_op:
        batch_op.drop_column("reviewers_channel_id")

    with op.batch_alter_table("slack_conversations") as batch_op:
        batch_op.drop_column("thread_title")
