"""add skill learning loop tables

Revision ID: add_skill_learning_loop_tables
Revises: add_slack_auto_followup_support
Create Date: 2026-07-09

Creates the backend foundations for the skill learning loop: version snapshots
of custom skills, review-gated skill suggestions, and conversation evaluations.
"""

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision = "add_skill_learning_loop_tables"
down_revision = "add_slack_auto_followup_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_versions",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("skill_id", GUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(20), nullable=False),
        sa.Column("suggestion_id", GUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["custom_skills.id"],
            name=op.f("fk_skill_versions_skill_id_custom_skills"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_versions")),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_versions_skill_id_version"),
    )
    op.create_index("ix_skill_versions_skill_id", "skill_versions", ["skill_id"], unique=False)

    op.create_table(
        "skill_suggestions",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("skill_id", GUID(), nullable=True),
        sa.Column("suggestion_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("patch", sa.JSON(), nullable=True),
        sa.Column("proposed_instructions", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("source", sa.JSON(), nullable=True),
        sa.Column("reviewed_by", GUID(), nullable=True),
        sa.Column("reviewed_via", sa.String(10), nullable=True),
        sa.Column("reviewer_slack_user_id", sa.String(50), nullable=True),
        sa.Column("reviewer_display_name", sa.String(200), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("slack_channel_id", sa.String(50), nullable=True),
        sa.Column("slack_message_ts", sa.String(50), nullable=True),
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
            name=op.f("fk_skill_suggestions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["custom_skills.id"],
            name=op.f("fk_skill_suggestions_skill_id_custom_skills"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name=op.f("fk_skill_suggestions_reviewed_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_suggestions")),
    )
    op.create_index("ix_skill_suggestions_tenant_id", "skill_suggestions", ["tenant_id"], unique=False)
    op.create_index("ix_skill_suggestions_skill_id", "skill_suggestions", ["skill_id"], unique=False)

    op.create_table(
        "conversation_evaluations",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("tenant_id", GUID(), nullable=False),
        sa.Column("notebook_id", GUID(), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("verdict", sa.String(15), nullable=True),
        sa.Column("findings", sa.JSON(), nullable=True),
        sa.Column(
            "evaluated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_conversation_evaluations_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["notebook_id"],
            ["notebooks.id"],
            name=op.f("fk_conversation_evaluations_notebook_id_notebooks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_evaluations")),
    )
    op.create_index("ix_conversation_evaluations_tenant_id", "conversation_evaluations", ["tenant_id"], unique=False)
    op.create_index(
        "ix_conversation_evaluations_notebook_id", "conversation_evaluations", ["notebook_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_evaluations_notebook_id", table_name="conversation_evaluations")
    op.drop_index("ix_conversation_evaluations_tenant_id", table_name="conversation_evaluations")
    op.drop_table("conversation_evaluations")

    op.drop_index("ix_skill_suggestions_skill_id", table_name="skill_suggestions")
    op.drop_index("ix_skill_suggestions_tenant_id", table_name="skill_suggestions")
    op.drop_table("skill_suggestions")

    op.drop_index("ix_skill_versions_skill_id", table_name="skill_versions")
    op.drop_table("skill_versions")
