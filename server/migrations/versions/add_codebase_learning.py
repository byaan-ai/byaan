"""add codebase learning foundations

Revision ID: add_codebase_learning
Revises: add_skill_loop_settings
Create Date: 2026-07-09

Adds the skill_citations table (anchoring skill claims to source-code excerpts) and
per-repo branch-tracking / skill-sync columns on github_repositories.
"""

import sqlalchemy as sa
from alembic import op
from fastapi_users_db_sqlalchemy.generics import GUID

revision = "add_codebase_learning"
down_revision = "add_skill_loop_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("github_repositories", sa.Column("tracked_branch", sa.String(200), nullable=True))
    op.add_column(
        "github_repositories",
        sa.Column("skill_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "skill_citations",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("skill_id", GUID(), nullable=False),
        sa.Column("repo_id", GUID(), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("blob_sha", sa.String(64), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=False),
        sa.Column("snippet_hash", sa.String(64), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("claim_key", sa.String(200), nullable=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="valid"),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["custom_skills.id"],
            name=op.f("fk_skill_citations_skill_id_custom_skills"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["github_repositories.id"],
            name=op.f("fk_skill_citations_repo_id_github_repositories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_citations")),
    )
    op.create_index(op.f("ix_skill_citations_skill_id"), "skill_citations", ["skill_id"])
    op.create_index("ix_skill_citations_repo_id_path", "skill_citations", ["repo_id", "path"])


def downgrade() -> None:
    op.drop_index("ix_skill_citations_repo_id_path", table_name="skill_citations")
    op.drop_index(op.f("ix_skill_citations_skill_id"), table_name="skill_citations")
    op.drop_table("skill_citations")

    op.drop_column("github_repositories", "skill_sync_enabled")
    op.drop_column("github_repositories", "tracked_branch")
