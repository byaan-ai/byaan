"""backfill legacy dashboard assets

Revision ID: backfill_legacy_dashboard_assets
Revises: add_governed_dashboard_assets
Create Date: 2026-08-16
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "backfill_legacy_dashboard_assets"
down_revision = "add_governed_dashboard_assets"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _digest_payload(payload: object) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _unique_slug(connection: sa.Connection, tenant_id: object, notebook_id: object) -> str:
    base = f"legacy-{str(notebook_id)[:48]}"
    slug = base
    counter = 2
    while connection.execute(
        sa.text("SELECT 1 FROM dashboard_assets WHERE tenant_id = :tenant_id AND slug = :slug"),
        {"tenant_id": tenant_id, "slug": slug},
    ).first():
        slug = f"{base}-{counter}"
        counter += 1
    return slug[:160]


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _mapping_from_json(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def upgrade() -> None:
    if not {"dashboard_assets", "dashboards", "notebooks"}.issubset(_tables()):
        return
    dashboard_columns = _columns("dashboards")
    required_dashboard_columns = {"id", "tenant_id", "notebook_id", "version_num", "html_content", "asset_id"}
    if not required_dashboard_columns.issubset(dashboard_columns):
        return

    connection = op.get_bind()
    notebook_columns = _columns("notebooks")
    notebook_name_select = "n.notebook_name" if "notebook_name" in notebook_columns else "NULL"
    created_by_select = "n.created_by" if "created_by" in notebook_columns else "NULL"
    group_by_columns = ["d.tenant_id", "d.notebook_id", "n.id"]
    if "notebook_name" in notebook_columns:
        group_by_columns.append("n.notebook_name")
    if "created_by" in notebook_columns:
        group_by_columns.append("n.created_by")
    families = connection.execute(
        sa.text(
            f"""
            SELECT d.tenant_id, d.notebook_id, {notebook_name_select} AS notebook_name, {created_by_select} AS created_by
            FROM dashboards d
            LEFT JOIN notebooks n ON n.id = d.notebook_id
            WHERE d.asset_id IS NULL
            GROUP BY {", ".join(group_by_columns)}
            ORDER BY d.notebook_id
            """
        )
    ).mappings()

    for family in families:
        versions = list(
            connection.execute(
                sa.text(
                    """
                    SELECT id, version_num, html_content
                    FROM dashboards
                    WHERE tenant_id = :tenant_id AND notebook_id = :notebook_id AND asset_id IS NULL
                    ORDER BY version_num
                    """
                ),
                {"tenant_id": family["tenant_id"], "notebook_id": family["notebook_id"]},
            ).mappings()
        )
        if not versions:
            continue

        latest = versions[-1]
        asset_id = str(uuid4())
        slug = _unique_slug(connection, family["tenant_id"], family["notebook_id"])
        name = family["notebook_name"] or f"Legacy Dashboard {str(family['notebook_id'])[:8]}"
        validation = {
            "valid": False,
            "blockers": ["legacy HTML dashboard requires structured manifest review before agent-ready publish"],
            "warnings": [],
            "migration_state": "legacy_unstructured",
            "backfilled_at": datetime.utcnow().isoformat(),
        }
        health_summary = {
            "freshness": "unknown",
            "migration": {
                "state": "legacy_unstructured",
                "backfilled_by": revision,
                "latest_dashboard_version_id": str(latest["id"]),
            },
        }
        etag = _digest_payload({"legacy_dashboard_id": str(latest["id"]), "notebook_id": str(family["notebook_id"])})

        connection.execute(
            sa.text(
                """
                INSERT INTO dashboard_assets (
                    id, tenant_id, notebook_id, slug, name, description, owner_id, tags_json, lifecycle,
                    current_draft_version_id, published_version_id, access_policy_json, freshness_policy_json,
                    consumer_summary_json, health_summary_json, etag
                )
                VALUES (
                    :id, :tenant_id, :notebook_id, :slug, :name, :description, :owner_id, :tags_json,
                    'legacy_unstructured', NULL, :published_version_id, :access_policy_json,
                    :freshness_policy_json, :consumer_summary_json, :health_summary_json, :etag
                )
                """
            ),
            {
                "id": asset_id,
                "tenant_id": family["tenant_id"],
                "notebook_id": family["notebook_id"],
                "slug": slug,
                "name": name,
                "description": "Backfilled from preserved legacy dashboard HTML; structured review required.",
                "owner_id": family["created_by"],
                "tags_json": _json(["legacy_unstructured"]),
                "published_version_id": latest["id"],
                "access_policy_json": _json({"required_scopes": ["dashboard:read"]}),
                "freshness_policy_json": _json({"mode": "live", "allow_stale": True, "require_as_of": False}),
                "consumer_summary_json": _json({}),
                "health_summary_json": _json(health_summary),
                "etag": etag,
            },
        )

        for dashboard in versions:
            content_hash = _digest_payload({"html_content": dashboard["html_content"] or ""})
            connection.execute(
                sa.text(
                    """
                    UPDATE dashboards
                    SET asset_id = :asset_id,
                        content_hash = :content_hash,
                        status = 'legacy_unstructured',
                        change_summary = :change_summary,
                        pinned_model_versions_json = :pinned_model_versions_json,
                        pinned_source_snapshots_json = :pinned_source_snapshots_json,
                        validation_result_json = :validation_result_json,
                        migration_state = 'legacy_unstructured',
                        is_published_immutable = :is_published_immutable
                    WHERE id = :dashboard_id
                    """
                ),
                {
                    "asset_id": asset_id,
                    "content_hash": content_hash,
                    "change_summary": "Backfilled legacy Dashboard asset; structured manifest review required.",
                    "pinned_model_versions_json": _json({}),
                    "pinned_source_snapshots_json": _json([]),
                    "validation_result_json": _json(validation),
                    "is_published_immutable": False,
                    "dashboard_id": dashboard["id"],
                },
            )


def downgrade() -> None:
    if not {"dashboard_assets", "dashboards"}.issubset(_tables()):
        return
    connection = op.get_bind()
    asset_ids: list[object] = []
    candidates = connection.execute(
        sa.text(
            """
            SELECT id, health_summary_json
            FROM dashboard_assets
            WHERE lifecycle = 'legacy_unstructured'
              AND slug LIKE 'legacy-%'
              AND current_draft_version_id IS NULL
            """
        )
    ).mappings()
    for candidate in candidates:
        health_summary = _mapping_from_json(candidate["health_summary_json"])
        migration_summary = health_summary.get("migration")
        if not isinstance(migration_summary, dict) or migration_summary.get("backfilled_by") != revision:
            continue
        asset_ids.append(candidate["id"])

    for asset_id in asset_ids:
        connection.execute(
            sa.text(
                """
                UPDATE dashboards
                SET asset_id = NULL
                WHERE asset_id = :asset_id
                """
            ),
            {"asset_id": asset_id},
        )
        connection.execute(sa.text("DELETE FROM dashboard_assets WHERE id = :asset_id"), {"asset_id": asset_id})
