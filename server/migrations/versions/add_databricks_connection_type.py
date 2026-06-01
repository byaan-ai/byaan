"""Add databricks to allowed connection types

Revision ID: add_databricks_connection_type
Revises: add_dynamodb_connection_type
Create Date: 2026-06-01

"""

from alembic import op

revision = "add_databricks_connection_type"
down_revision = "add_dynamodb_connection_type"
branch_labels = None
depends_on = None

OLD_TYPES = ("pg", "mysql", "mongo", "sqlite", "mssql", "dynamodb")
NEW_TYPES = ("pg", "mysql", "mongo", "sqlite", "mssql", "dynamodb", "databricks")


def upgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint("ck_connections_type_allowed", type_="check")
        batch_op.create_check_constraint("ck_connections_type_allowed", f"type IN {NEW_TYPES}")


def downgrade() -> None:
    with op.batch_alter_table("connections") as batch_op:
        batch_op.drop_constraint("ck_connections_type_allowed", type_="check")
        batch_op.create_check_constraint("ck_connections_type_allowed", f"type IN {OLD_TYPES}")
