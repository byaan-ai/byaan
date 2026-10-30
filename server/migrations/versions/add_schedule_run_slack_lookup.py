"""add slack_channel_id + slack_message_ts lookup to schedule_runs

Revision ID: add_schedule_run_slack_lookup
Revises: add_conversational_cron_fields
Create Date: 2026-10-30

Lets Slack reply-threads off a monitor-posted message inherit the monitor's
home notebook. When ``_send_to_slack`` posts a monitor's output we now capture
``(channel_id, ts)`` on the ``schedule_runs`` row; the Slack conversation
loader looks up a matching run when a brand-new thread is created and copies
the monitor's ``schedule.notebook_id`` into the new ``SlackConversation``.

A composite index on ``(slack_channel_id, slack_message_ts)`` covers the
``WHERE channel = ? AND ts = ?`` lookup fully.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_schedule_run_slack_lookup"
down_revision = "add_conversational_cron_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("schedule_runs") as batch_op:
        batch_op.add_column(sa.Column("slack_channel_id", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("slack_message_ts", sa.String(length=50), nullable=True))

    op.create_index(
        "ix_schedule_runs_slack_lookup",
        "schedule_runs",
        ["slack_channel_id", "slack_message_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_runs_slack_lookup", table_name="schedule_runs")
    with op.batch_alter_table("schedule_runs") as batch_op:
        batch_op.drop_column("slack_message_ts")
        batch_op.drop_column("slack_channel_id")
