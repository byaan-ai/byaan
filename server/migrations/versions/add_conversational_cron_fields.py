"""add monitor fields to schedules and schedule_runs

Revision ID: add_conversational_cron_fields
Revises: add_slack_default_model
Create Date: 2026-10-24

Extends schedules + schedule_runs for conversational Monitors (crons created
in chat or Slack). All additive, nullable or safe-defaulted — zero impact on
existing rows.

schedules:
- slack_thread_ts, slack_workspace_id, broadcast_to_channel: thread-targeted delivery.
- origin: notebook_chat | slack | ui.
- status: draft | active | paused | auto_paused. Backfilled from is_enabled.
- notify_policy: always | on_alert | on_change (default always).
- paused_until: optional datetime for timed pauses.
- consecutive_failures: counter for auto-pause after 3 failures.
- running_since: lease timestamp replacing the stuck is_running flag.
- llm_connection_id, model: pinned model so runs stay consistent.
- manual_trigger_pending, manual_slack_target: cross-worker signaling for
  "Run a test now" clicks. Replaces in-process globals that broke under
  multi-worker uvicorn deployments.

schedule_runs:
- outcome: ok | alert | no_change.
- headline: short line for history.
- findings: JSON list of structured findings.
- deliveries: JSON list of attempted deliveries + statuses.
- trigger: scheduled | manual | catchup.
"""

import sqlalchemy as sa
from alembic import op

from server.db.base import GUID

revision = "add_conversational_cron_fields"
down_revision = "add_slack_default_model"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("schedules") as batch_op:
        batch_op.add_column(sa.Column("slack_thread_ts", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("slack_workspace_id", GUID(), nullable=True))
        batch_op.add_column(
            sa.Column("broadcast_to_channel", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("origin", sa.String(length=20), nullable=False, server_default=sa.text("'ui'")))
        batch_op.add_column(
            sa.Column("status", sa.String(length=20), nullable=False, server_default=sa.text("'active'"))
        )
        batch_op.add_column(
            sa.Column("notify_policy", sa.String(length=20), nullable=False, server_default=sa.text("'always'"))
        )
        batch_op.add_column(sa.Column("paused_until", sa.TIMESTAMP(timezone=False), nullable=True))
        batch_op.add_column(
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0"))
        )
        batch_op.add_column(sa.Column("running_since", sa.TIMESTAMP(timezone=False), nullable=True))
        batch_op.add_column(sa.Column("llm_connection_id", GUID(), nullable=True))
        batch_op.add_column(sa.Column("model", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("manual_trigger_pending", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("manual_slack_target", sa.JSON(), nullable=True))
        batch_op.create_foreign_key(
            "fk_schedules_slack_workspace",
            "slack_workspaces",
            ["slack_workspace_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_schedules_llm_connection",
            "llm_connections",
            ["llm_connection_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_schedules_slack_lookup",
        "schedules",
        ["slack_workspace_id", "slack_channel_id", "slack_thread_ts"],
        unique=False,
    )
    op.create_index("ix_schedules_status", "schedules", ["status"], unique=False)

    # Backfill status from is_enabled so existing rows keep their effective state.
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    op.execute(
        "UPDATE schedules SET status = 'paused' WHERE is_enabled = 0"
        if is_sqlite
        else "UPDATE schedules SET status = 'paused' WHERE is_enabled = FALSE"
    )

    with op.batch_alter_table("schedule_runs") as batch_op:
        batch_op.add_column(sa.Column("outcome", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("headline", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("findings", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("deliveries", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("trigger", sa.String(length=20), nullable=False, server_default=sa.text("'scheduled'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("schedule_runs") as batch_op:
        batch_op.drop_column("trigger")
        batch_op.drop_column("deliveries")
        batch_op.drop_column("findings")
        batch_op.drop_column("headline")
        batch_op.drop_column("outcome")

    op.drop_index("ix_schedules_status", table_name="schedules")
    op.drop_index("ix_schedules_slack_lookup", table_name="schedules")

    with op.batch_alter_table("schedules") as batch_op:
        batch_op.drop_constraint("fk_schedules_llm_connection", type_="foreignkey")
        batch_op.drop_constraint("fk_schedules_slack_workspace", type_="foreignkey")
        batch_op.drop_column("manual_slack_target")
        batch_op.drop_column("manual_trigger_pending")
        batch_op.drop_column("model")
        batch_op.drop_column("llm_connection_id")
        batch_op.drop_column("running_since")
        batch_op.drop_column("consecutive_failures")
        batch_op.drop_column("paused_until")
        batch_op.drop_column("notify_policy")
        batch_op.drop_column("status")
        batch_op.drop_column("origin")
        batch_op.drop_column("broadcast_to_channel")
        batch_op.drop_column("slack_workspace_id")
        batch_op.drop_column("slack_thread_ts")
