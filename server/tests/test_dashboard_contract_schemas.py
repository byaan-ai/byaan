from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from server.schemas.dashboard import DashboardManifest, DashboardRun


def _valid_manifest_payload() -> dict:
    return {
        "schema_version": "dashboard.manifest.v1",
        "dashboard_id": "dash-revenue",
        "title": "Revenue operations",
        "description": "Decision surface for pipeline and recognized revenue",
        "audience": ["sales_ops", "finance"],
        "semantic_bindings": [
            {
                "id": "sales-model",
                "model_slug": "sales",
                "model_version": "v3",
                "source_snapshot_ids": ["snapshot-1"],
                "allowed_metrics": ["revenue"],
                "allowed_dimensions": ["region"],
            }
        ],
        "data_views": [
            {
                "id": "dv-revenue-by-region",
                "kind": "semantic_metric",
                "question": "How much revenue is recognized by region?",
                "output_schema": [
                    {"name": "region", "data_type": "string", "description": "Sales region"},
                    {
                        "name": "revenue",
                        "data_type": "number",
                        "description": "Recognized revenue",
                        "unit": "USD",
                    },
                ],
                "filter_fields": ["region"],
                "semantic_metric": {
                    "semantic_binding_id": "sales-model",
                    "metric": "revenue",
                    "dimensions": ["region"],
                    "grain": "month",
                },
            }
        ],
        "filters": [
            {
                "id": "region",
                "label": "Region",
                "source": "semantic_field",
                "field": "region",
                "filter_type": "enum",
                "operators": ["in"],
                "affected_data_view_ids": ["dv-revenue-by-region"],
                "domain": ["AMER", "EMEA"],
            }
        ],
        "layout": {"sections": [{"id": "summary", "title": "Summary", "tile_ids": ["tile-revenue"]}]},
        "tiles": [
            {
                "id": "tile-revenue",
                "title": "Revenue by region",
                "tile_type": "bar",
                "business_question": "Which regions contribute revenue?",
                "data_view_id": "dv-revenue-by-region",
                "encoding": {"x": "region", "y": "revenue"},
                "accessible_fallback": {
                    "summary": "Revenue by region table",
                    "table_fields": ["region", "revenue"],
                },
            }
        ],
        "actions": [
            {
                "id": "export",
                "label": "Export",
                "action_type": "export",
                "required_scope": "dashboard:export",
            }
        ],
        "freshness_policy": {"mode": "live", "max_age_seconds": 1800, "allow_stale": True},
        "access_policy": {
            "required_scopes": ["dashboard:read", "dashboard:query"],
            "row_policy_refs": ["tenant_rls"],
            "column_policy_refs": ["finance_columns"],
            "redaction_policy_refs": ["pii_redaction"],
        },
        "provenance": {
            "created_by_actor_type": "human",
            "created_by": "user-1",
            "source": "human",
            "evidence_refs": ["source-understanding-1"],
        },
        "migration": {"state": "new_structured", "blockers": []},
    }


def _valid_run_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "contract_version": "dashboard.run.v1",
        "run_id": "run-1",
        "dashboard_id": "dash-revenue",
        "dashboard_version_id": "version-1",
        "actor_type": "agent",
        "actor_id": "mcp-key-1",
        "correlation_id": "corr-1",
        "mode": "live",
        "normalized_filters": {"region": ["AMER"]},
        "filter_digest": "filters:abc",
        "pinned_versions": {"semantic_models": {"sales": "v3"}, "source_snapshots": ["snapshot-1"]},
        "execution_plan_digest": "plan:def",
        "started_at": now,
        "completed_at": now,
        "overall_freshness": "fresh",
        "views": [
            {
                "data_view_id": "dv-revenue-by-region",
                "status": "success",
                "result": [{"region": "AMER", "revenue": 120}],
                "schema": [
                    {"name": "region", "data_type": "string"},
                    {"name": "revenue", "data_type": "number", "unit": "USD"},
                ],
                "row_count": 1,
                "cached": False,
                "stale": False,
                "as_of": now,
            }
        ],
    }


def test_dashboard_manifest_accepts_structured_contract() -> None:
    manifest = DashboardManifest.model_validate(_valid_manifest_payload())

    assert manifest.schema_version == "dashboard.manifest.v1"
    assert manifest.data_views[0].semantic_metric is not None
    assert manifest.tiles[0].data_view_id == manifest.data_views[0].id


def test_dashboard_manifest_json_schema_requires_authoritative_sections() -> None:
    schema = DashboardManifest.model_json_schema()

    assert schema["properties"]["schema_version"]["const"] == "dashboard.manifest.v1"
    assert {
        "schema_version",
        "dashboard_id",
        "title",
        "audience",
        "semantic_bindings",
        "data_views",
        "filters",
        "layout",
        "tiles",
        "actions",
        "freshness_policy",
        "access_policy",
        "provenance",
        "migration",
    }.issubset(set(schema["required"]))


def test_dashboard_manifest_rejects_unbound_references() -> None:
    payload = _valid_manifest_payload()
    payload["tiles"][0]["data_view_id"] = "missing-view"

    with pytest.raises(ValidationError, match="unknown data view"):
        DashboardManifest.model_validate(payload)


def test_dashboard_manifest_rejects_draft_semantic_binding() -> None:
    payload = _valid_manifest_payload()
    payload["semantic_bindings"][0]["readiness"] = "blocked"

    with pytest.raises(ValidationError, match="published model versions"):
        DashboardManifest.model_validate(payload)


def test_dashboard_run_rejects_pinned_snapshot_without_artifact() -> None:
    payload = _valid_run_payload()
    payload["mode"] = "pinned_snapshot"

    with pytest.raises(ValidationError, match="result_artifact_id"):
        DashboardRun.model_validate(payload)
