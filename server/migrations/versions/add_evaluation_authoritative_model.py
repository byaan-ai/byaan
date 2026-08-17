"""add evaluation authoritative model

Revision ID: add_evaluation_authoritative_model
Revises: add_canonical_sharing_model
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from server.db.base import GUID

revision = "add_evaluation_authoritative_model"
down_revision = "add_canonical_sharing_model"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _drop_table_if_present(table_name: str) -> None:
    if table_name in _tables():
        op.drop_table(table_name)


def upgrade() -> None:
    existing = _tables()
    if "evaluation_suites" not in existing:
        op.create_table(
            "evaluation_suites",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("slug", sa.String(length=160), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("owner_id", GUID(), nullable=True),
            sa.Column("target_kinds_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("lifecycle", sa.String(length=40), nullable=False, server_default="draft"),
            sa.Column("current_draft_version_id", GUID(), nullable=True),
            sa.Column("published_version_id", GUID(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_suites")),
            sa.UniqueConstraint("tenant_id", "slug", name="uq_evaluation_suites_tenant_slug"),
        )
        op.create_index("ix_evaluation_suites_tenant_id", "evaluation_suites", ["tenant_id"])
        op.create_index("ix_evaluation_suites_lifecycle", "evaluation_suites", ["lifecycle"])

    if "evaluation_suite_versions" not in existing:
        op.create_table(
            "evaluation_suite_versions",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("suite_id", GUID(), nullable=False),
            sa.Column("version_num", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
            sa.Column("contract_version", sa.String(length=80), nullable=False),
            sa.Column("manifest_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("gate_policy_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content_hash", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("created_by", GUID(), nullable=True),
            sa.Column("published_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_suite_versions")),
            sa.UniqueConstraint("suite_id", "version_num", name="uq_evaluation_suite_versions_suite_version"),
        )
        op.create_index("ix_evaluation_suite_versions_tenant_id", "evaluation_suite_versions", ["tenant_id"])
        op.create_index("ix_evaluation_suite_versions_suite_id", "evaluation_suite_versions", ["suite_id"])
        op.create_index("ix_evaluation_suite_versions_status", "evaluation_suite_versions", ["status"])
        op.create_index("ix_evaluation_suite_versions_content_hash", "evaluation_suite_versions", ["content_hash"])

    existing = _tables()
    if "evaluation_suites" in existing:
        with op.batch_alter_table("evaluation_suites") as batch_op:
            batch_op.create_foreign_key(
                "fk_evaluation_suites_current_draft_version_id_versions",
                "evaluation_suite_versions",
                ["current_draft_version_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_foreign_key(
                "fk_evaluation_suites_published_version_id_versions",
                "evaluation_suite_versions",
                ["published_version_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if "evaluation_cases" not in existing:
        op.create_table(
            "evaluation_cases",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("suite_version_id", GUID(), nullable=False),
            sa.Column("case_key", sa.String(length=160), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("target_kinds_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("operation", sa.String(length=80), nullable=False),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("expected_contract_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("provenance_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("tags_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("content_hash", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["suite_version_id"], ["evaluation_suite_versions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_cases")),
            sa.UniqueConstraint("suite_version_id", "case_key", name="uq_evaluation_cases_version_key"),
        )
        for column_name in ("tenant_id", "suite_version_id", "content_hash"):
            op.create_index(f"ix_evaluation_cases_{column_name}", "evaluation_cases", [column_name])

    if "evaluation_target_snapshots" not in existing:
        op.create_table(
            "evaluation_target_snapshots",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("target_kind", sa.String(length=60), nullable=False),
            sa.Column("target_ref", sa.String(length=255), nullable=False),
            sa.Column("contract_version", sa.String(length=80), nullable=False),
            sa.Column("snapshot_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("pin_digest", sa.String(length=128), nullable=False),
            sa.Column("blockers_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_target_snapshots")),
        )
        for column_name in ("tenant_id", "target_kind", "target_ref", "pin_digest"):
            op.create_index(f"ix_evaluation_target_snapshots_{column_name}", "evaluation_target_snapshots", [column_name])

    if "evaluation_runs" not in existing:
        op.create_table(
            "evaluation_runs",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("suite_version_id", GUID(), nullable=False),
            sa.Column("target_snapshot_id", GUID(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
            sa.Column("actor_type", sa.String(length=40), nullable=False, server_default="human"),
            sa.Column("actor_id", sa.String(length=160), nullable=False),
            sa.Column("baseline_run_id", GUID(), nullable=True),
            sa.Column("candidate_label", sa.String(length=160), nullable=True),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("lease_holder", sa.String(length=160), nullable=True),
            sa.Column("lease_expires_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("heartbeat_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("stop_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("preflight_blockers_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("summary_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("started_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("completed_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["suite_version_id"], ["evaluation_suite_versions.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_snapshot_id"], ["evaluation_target_snapshots.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["baseline_run_id"], ["evaluation_runs.id"]),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
            sa.UniqueConstraint("suite_version_id", "idempotency_key", name="uq_evaluation_runs_suite_idempotency"),
        )
        for column_name in ("tenant_id", "suite_version_id", "target_snapshot_id", "status", "actor_id"):
            op.create_index(f"ix_evaluation_runs_{column_name}", "evaluation_runs", [column_name])

    if "evaluation_case_runs" not in existing:
        op.create_table(
            "evaluation_case_runs",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("run_id", GUID(), nullable=False),
            sa.Column("case_id", GUID(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("input_digest", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("output_digest", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("result_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("error_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("started_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("completed_at", sa.TIMESTAMP(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["case_id"], ["evaluation_cases.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_case_runs")),
        )
        for column_name in ("tenant_id", "run_id", "case_id", "status"):
            op.create_index(f"ix_evaluation_case_runs_{column_name}", "evaluation_case_runs", [column_name])

    if "evaluation_assessments" not in existing:
        op.create_table(
            "evaluation_assessments",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("case_run_id", GUID(), nullable=False),
            sa.Column("category", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("score", sa.String(length=80), nullable=True),
            sa.Column("hard_fail", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["case_run_id"], ["evaluation_case_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_assessments")),
        )
        for column_name in ("tenant_id", "case_run_id", "category"):
            op.create_index(f"ix_evaluation_assessments_{column_name}", "evaluation_assessments", [column_name])

    if "evaluation_overrides" not in existing:
        op.create_table(
            "evaluation_overrides",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("assessment_id", GUID(), nullable=False),
            sa.Column("override_type", sa.String(length=40), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("created_by", GUID(), nullable=True),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["assessment_id"], ["evaluation_assessments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_overrides")),
        )
        for column_name in ("tenant_id", "assessment_id"):
            op.create_index(f"ix_evaluation_overrides_{column_name}", "evaluation_overrides", [column_name])

    if "evaluation_artifacts" not in existing:
        op.create_table(
            "evaluation_artifacts",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("run_id", GUID(), nullable=True),
            sa.Column("case_run_id", GUID(), nullable=True),
            sa.Column("artifact_type", sa.String(length=80), nullable=False),
            sa.Column("uri", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=128), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["case_run_id"], ["evaluation_case_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_artifacts")),
        )
        for column_name in ("tenant_id", "run_id", "case_run_id", "artifact_type", "content_hash"):
            op.create_index(f"ix_evaluation_artifacts_{column_name}", "evaluation_artifacts", [column_name])

    if "advisor_change_sets" not in existing:
        op.create_table(
            "advisor_change_sets",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("suite_version_id", GUID(), nullable=True),
            sa.Column("target_ref", sa.String(length=255), nullable=False),
            sa.Column("base_version_ref", sa.String(length=255), nullable=False),
            sa.Column("base_etag", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
            sa.Column("evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("verification_run_id", GUID(), nullable=True),
            sa.Column("regression_run_id", GUID(), nullable=True),
            sa.Column("created_by", sa.String(length=160), nullable=False),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["suite_version_id"], ["evaluation_suite_versions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["verification_run_id"], ["evaluation_runs.id"]),
            sa.ForeignKeyConstraint(["regression_run_id"], ["evaluation_runs.id"]),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_change_sets")),
        )
        for column_name in ("tenant_id", "target_ref", "status"):
            op.create_index(f"ix_advisor_change_sets_{column_name}", "advisor_change_sets", [column_name])

    if "advisor_suggestions" not in existing:
        op.create_table(
            "advisor_suggestions",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("change_set_id", GUID(), nullable=False),
            sa.Column("suggestion_type", sa.String(length=80), nullable=False),
            sa.Column("patch_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("affected_case_ids_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["change_set_id"], ["advisor_change_sets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_suggestions")),
        )
        for column_name in ("tenant_id", "change_set_id", "status"):
            op.create_index(f"ix_advisor_suggestions_{column_name}", "advisor_suggestions", [column_name])

    if "promotion_decisions" not in existing:
        op.create_table(
            "promotion_decisions",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("change_set_id", GUID(), nullable=True),
            sa.Column("verification_run_id", GUID(), nullable=True),
            sa.Column("regression_run_id", GUID(), nullable=True),
            sa.Column("decision", sa.String(length=40), nullable=False),
            sa.Column("decided_by", GUID(), nullable=True),
            sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
            sa.Column("audit_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["change_set_id"], ["advisor_change_sets.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["verification_run_id"], ["evaluation_runs.id"]),
            sa.ForeignKeyConstraint(["regression_run_id"], ["evaluation_runs.id"]),
            sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_promotion_decisions")),
        )
        op.create_index("ix_promotion_decisions_tenant_id", "promotion_decisions", ["tenant_id"])

    if "evaluation_audit_events" not in existing:
        op.create_table(
            "evaluation_audit_events",
            sa.Column("id", GUID(), nullable=False),
            sa.Column("tenant_id", GUID(), nullable=False),
            sa.Column("suite_id", GUID(), nullable=True),
            sa.Column("suite_version_id", GUID(), nullable=True),
            sa.Column("run_id", GUID(), nullable=True),
            sa.Column("actor_type", sa.String(length=40), nullable=False),
            sa.Column("actor_id", sa.String(length=160), nullable=False),
            sa.Column("action", sa.String(length=120), nullable=False),
            sa.Column("outcome", sa.String(length=40), nullable=False),
            sa.Column("details_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["suite_version_id"], ["evaluation_suite_versions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_audit_events")),
        )
        for column_name in ("tenant_id", "suite_id", "suite_version_id", "run_id", "actor_id", "action"):
            op.create_index(f"ix_evaluation_audit_events_{column_name}", "evaluation_audit_events", [column_name])


def downgrade() -> None:
    for table_name in (
        "evaluation_audit_events",
        "promotion_decisions",
        "advisor_suggestions",
        "advisor_change_sets",
        "evaluation_artifacts",
        "evaluation_overrides",
        "evaluation_assessments",
        "evaluation_case_runs",
        "evaluation_runs",
        "evaluation_target_snapshots",
        "evaluation_cases",
    ):
        _drop_table_if_present(table_name)

    if "evaluation_suites" in _tables():
        with op.batch_alter_table("evaluation_suites") as batch_op:
            batch_op.drop_constraint("fk_evaluation_suites_published_version_id_versions", type_="foreignkey")
            batch_op.drop_constraint("fk_evaluation_suites_current_draft_version_id_versions", type_="foreignkey")
    _drop_table_if_present("evaluation_suite_versions")
    _drop_table_if_present("evaluation_suites")
