"""Add Byaan Handoffs tables and per-repo handoff settings

Revision ID: add_handoffs
Revises: add_skill_api_auth_header
Create Date: 2026-11-04

"""

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision = "add_handoffs"
down_revision = "add_skill_api_auth_header"
branch_labels = None
depends_on = None

_WORKING_SQL = "status IN ('preparing', 'running', 'waiting_on_human', 'pushing', 'fixing')"


def upgrade() -> None:
    op.create_table(
        "handoffs",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("github_repository_id", GUID(), nullable=True),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("requested_by", GUID(), nullable=True),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("slack_conversation_id", GUID(), nullable=True),
        sa.Column("slack_card_ts", sa.String(length=50), nullable=True),
        sa.Column("notebook_id", GUID(), nullable=True),
        sa.Column("schedule_id", GUID(), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("brief", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("base_branch", sa.String(length=200), nullable=False),
        sa.Column("base_sha", sa.String(length=40), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("pushed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("next_check_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(length=500), nullable=True),
        sa.Column("untested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ci_fix_round", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("claude_session_id", sa.String(length=100), nullable=True),
        sa.Column("workspace_path", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("heartbeat_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoffs")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_handoffs_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["github_repository_id"],
            ["github_repositories.id"],
            name=op.f("fk_handoffs_github_repository_id_github_repositories"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name=op.f("fk_handoffs_requested_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["slack_conversation_id"],
            ["slack_conversations.id"],
            name=op.f("fk_handoffs_slack_conversation_id_slack_conversations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"], ["notebooks.id"], name=op.f("fk_handoffs_notebook_id_notebooks"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"], ["schedules.id"], name=op.f("fk_handoffs_schedule_id_schedules"), ondelete="SET NULL"
        ),
    )
    op.create_index("ix_handoffs_tenant_status", "handoffs", ["tenant_id", "status"], unique=False)
    op.create_index("ix_handoffs_schedule_fingerprint", "handoffs", ["schedule_id", "fingerprint"], unique=False)
    op.create_index("ix_handoffs_status_next_check", "handoffs", ["status", "next_check_at"], unique=False)
    op.create_index(
        "ux_handoffs_one_active_per_repo",
        "handoffs",
        ["github_repository_id"],
        unique=True,
        sqlite_where=sa.text(_WORKING_SQL),
        postgresql_where=sa.text(_WORKING_SQL),
    )

    op.create_table(
        "handoff_events",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("handoff_id", GUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoff_events")),
        sa.ForeignKeyConstraint(
            ["handoff_id"], ["handoffs.id"], name=op.f("fk_handoff_events_handoff_id_handoffs"), ondelete="CASCADE"
        ),
        sa.UniqueConstraint("handoff_id", "seq", name="uq_handoff_events_handoff_seq"),
    )

    op.create_table(
        "handoff_messages",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("handoff_id", GUID(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("author_user_id", GUID(), nullable=True),
        sa.Column("author_label", sa.String(length=255), nullable=True),
        sa.Column("question_seq", sa.Integer(), nullable=True),
        sa.Column("delivered_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoff_messages")),
        sa.ForeignKeyConstraint(
            ["handoff_id"], ["handoffs.id"], name=op.f("fk_handoff_messages_handoff_id_handoffs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            name=op.f("fk_handoff_messages_author_user_id_users"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_handoff_messages_pending", "handoff_messages", ["handoff_id", "delivered_at"], unique=False)

    with op.batch_alter_table("github_repositories") as batch_op:
        batch_op.add_column(sa.Column("handoff_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("handoff_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("github_repositories") as batch_op:
        batch_op.drop_column("handoff_config")
        batch_op.drop_column("handoff_enabled")

    op.drop_index("ix_handoff_messages_pending", table_name="handoff_messages")
    op.drop_table("handoff_messages")
    op.drop_table("handoff_events")

    op.drop_index("ux_handoffs_one_active_per_repo", table_name="handoffs")
    op.drop_index("ix_handoffs_status_next_check", table_name="handoffs")
    op.drop_index("ix_handoffs_schedule_fingerprint", table_name="handoffs")
    op.drop_index("ix_handoffs_tenant_status", table_name="handoffs")
    op.drop_table("handoffs")
