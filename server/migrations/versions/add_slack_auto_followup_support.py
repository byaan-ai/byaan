"""add slack auto followup support

Revision ID: add_slack_auto_followup_support
Revises: add_scope_to_github_repositories
Create Date: 2026-07-07

Adds per-thread state to enable intelligent auto follow-up: ownership marker
(set when Byaan replies to an @mention in a thread) and mute flag for the
`mute byaan` / `resume byaan` keyword commands. Feature is always on when
the Slack integration is active.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_slack_auto_followup_support"
down_revision = "add_scope_to_github_repositories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("slack_conversations") as batch_op:
        batch_op.add_column(sa.Column("bot_owned", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("auto_follow_muted", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_index(
        "ix_slack_conversations_owned_lookup",
        "slack_conversations",
        ["slack_workspace_id", "slack_channel_id", "slack_thread_ts", "bot_owned"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_slack_conversations_owned_lookup", table_name="slack_conversations")

    with op.batch_alter_table("slack_conversations") as batch_op:
        batch_op.drop_column("auto_follow_muted")
        batch_op.drop_column("bot_owned")
