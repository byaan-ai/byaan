"""add skill loop lease and durable digest dedup

Revision ID: add_skill_loop_lease
Revises: add_codebase_learning
Create Date: 2026-07-09

Adds a single-row skill_loop_lease table so only one worker process runs each
skill-loop tick in a multi-worker deployment, plus a last_digest_date column on
skill_loop_settings for durable per-tenant digest deduplication.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_skill_loop_lease"
down_revision = "add_codebase_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_loop_lease",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("holder", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_loop_lease")),
    )
    op.add_column("skill_loop_settings", sa.Column("last_digest_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("skill_loop_settings", "last_digest_date")
    op.drop_table("skill_loop_lease")
