from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.models.dashboard import DashboardRun
from server.models.notebooks import Notebook
from server.models.tenant import Tenant

pytestmark = pytest.mark.asyncio


def _manifest_payload(query_id: str | None = None, dashboard_id: str = "dash-rest") -> dict:
    query_id = query_id or str(uuid4())
    return {
        "schema_version": "dashboard.manifest.v1",
        "dashboard_id": dashboard_id,
        "title": "REST governed dashboard",
        "description": "Dashboard exposed through the governed REST contract",
        "audience": ["finance"],
        "semantic_bindings": [
            {
                "id": "sales-model",
                "model_slug": "sales",
                "model_version": "v1",
                "source_snapshot_ids": ["snapshot-1"],
                "allowed_metrics": ["revenue"],
                "allowed_dimensions": ["region"],
            }
        ],
        "data_views": [
            {
                "id": "dv-saved-revenue",
                "kind": "saved_query",
                "question": "What revenue did the reviewed query return?",
                "output_schema": [{"name": "revenue", "data_type": "number", "unit": "USD"}],
                "filter_fields": ["region"],
                "saved_query": {
                    "query_id": query_id,
                    "compatibility_reason": "reviewed legacy dashboard query",
                    "filter_contract": {},
                    "lineage": [
                        {
                            "id": "query-lineage",
                            "kind": "saved_query",
                            "name": "Revenue query",
                            "ref": query_id,
                        }
                    ],
                },
            }
        ],
        "filters": [
            {
                "id": "region",
                "label": "Region",
                "source": "saved_query_contract",
                "field": "region",
                "filter_type": "enum",
                "operators": ["eq"],
                "affected_data_view_ids": ["dv-saved-revenue"],
            }
        ],
        "layout": {"sections": [{"id": "main", "tile_ids": ["tile-revenue"]}]},
        "tiles": [
            {
                "id": "tile-revenue",
                "title": "Revenue",
                "tile_type": "kpi",
                "business_question": "What is revenue?",
                "data_view_id": "dv-saved-revenue",
            }
        ],
        "actions": [],
        "freshness_policy": {"mode": "live", "max_age_seconds": 3600, "allow_stale": True},
        "access_policy": {"required_scopes": ["dashboard:read", "dashboard:query"]},
        "provenance": {"created_by_actor_type": "human", "created_by": "user-1", "source": "human"},
        "migration": {"state": "new_structured", "blockers": []},
    }


async def _seed_notebook(test_session) -> Notebook:
    tenant = (await test_session.execute(select(Tenant))).scalars().first()
    assert tenant is not None
    notebook = Notebook(
        id=uuid4(),
        tenant_id=tenant.id,
        created_by=tenant.owner_id,
        notebook_name="Dashboard REST notebook",
    )
    test_session.add(notebook)
    await test_session.commit()
    await test_session.refresh(notebook)
    return notebook


async def test_dashboard_asset_rest_lifecycle_query_state_lineage_and_audit(
    test_client,
    test_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notebook = await _seed_notebook(test_session)
    query_id = str(uuid4())
    manifest = _manifest_payload(query_id)

    create_response = await test_client.post(
        "/api/dashboard-assets",
        json={
            "slug": "rest-governed-dashboard",
            "notebook_id": str(notebook.id),
            "manifest": manifest,
            "tags": ["finance"],
            "change_summary": "create from API",
        },
    )
    assert create_response.status_code == 201
    asset = create_response.json()["data"]
    assert asset["slug"] == "rest-governed-dashboard"
    assert asset["lifecycle"] == "draft"
    assert asset["etag"].startswith("sha256:")

    list_response = await test_client.get("/api/dashboard-assets")
    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 1

    version_response = await test_client.get(f"/api/dashboard-assets/{asset['id']}/versions/1")
    assert version_response.status_code == 200
    assert version_response.json()["data"]["manifest"]["data_views"][0]["id"] == "dv-saved-revenue"

    validate_response = await test_client.post(f"/api/dashboard-assets/{asset['id']}/validate", json={})
    assert validate_response.status_code == 200
    assert validate_response.json()["data"]["validation"]["valid"] is True

    patched_manifest = deepcopy(manifest)
    patched_manifest["title"] = "REST governed dashboard reviewed"
    patch_response = await test_client.patch(
        f"/api/dashboard-assets/{asset['id']}/draft",
        json={
            "base_etag": asset["etag"],
            "manifest": patched_manifest,
            "change_summary": "review title",
        },
    )
    assert patch_response.status_code == 200
    patched = patch_response.json()["data"]
    assert patched["version_num"] == 2
    assert patched["manifest"]["title"] == "REST governed dashboard reviewed"

    stale_patch_response = await test_client.patch(
        f"/api/dashboard-assets/{asset['id']}/draft",
        json={
            "base_etag": asset["etag"],
            "manifest": patched_manifest,
            "change_summary": "stale retry",
        },
    )
    assert stale_patch_response.status_code == 409
    assert stale_patch_response.json()["data"]["code"] == "etag_conflict"

    refreshed_response = await test_client.get(f"/api/dashboard-assets/{asset['id']}")
    assert refreshed_response.status_code == 200
    refreshed = refreshed_response.json()["data"]
    publish_response = await test_client.post(
        f"/api/dashboard-assets/{asset['id']}/publish",
        json={"base_etag": refreshed["etag"], "change_summary": "publish from API"},
    )
    assert publish_response.status_code == 200
    published = publish_response.json()["data"]
    assert published["status"] == "published"
    assert published["is_published_immutable"] is True

    captured: dict[str, object] = {}

    async def fake_execute_saved_query(session, query_id_arg, filters=None, viewer_user_id=None):
        captured["query_id"] = query_id_arg
        captured["filters"] = filters
        captured["viewer_user_id"] = viewer_user_id
        return {
            "success": True,
            "data": [{"revenue": 42}],
            "query_id": query_id_arg,
            "cached": True,
            "stale": False,
            "as_of": "2026-08-16T00:00:00",
        }

    monkeypatch.setattr("server.services.dashboard.QueryService.execute_saved_query", fake_execute_saved_query)

    preview_response = await test_client.post(
        f"/api/dashboard-assets/{asset['id']}/preview",
        json={"data_view_ids": ["dv-saved-revenue"], "correlation_id": "corr-preview"},
    )
    assert preview_response.status_code == 200
    assert preview_response.json()["data"]["preview"] is True

    query_response = await test_client.post(
        f"/api/dashboard-assets/{asset['id']}/query",
        json={
            "filters": {"region": "AMER"},
            "data_view_ids": ["dv-saved-revenue"],
            "correlation_id": "corr-rest",
        },
    )
    assert query_response.status_code == 200
    run = query_response.json()["data"]
    assert captured["query_id"] == query_id
    assert captured["viewer_user_id"] is None
    assert run["dashboard_id"] == asset["id"]
    assert run["views"][0]["result"] == [{"revenue": 42}]
    assert run["views"][0]["cached"] is True

    saved_run = await test_session.get(DashboardRun, run["run_id"])
    assert saved_run is not None
    assert saved_run.correlation_id == "corr-rest"

    unknown_view_response = await test_client.post(
        f"/api/dashboard-assets/{asset['id']}/query",
        json={"data_view_ids": ["missing-view"]},
    )
    assert unknown_view_response.status_code == 403

    export_response = await test_client.get(f"/api/dashboard-assets/{asset['id']}/export/html")
    assert export_response.status_code == 200
    assert "text/html" in export_response.headers["content-type"]
    assert 'attachment; filename="rest-governed-dashboard-v2.html"' == export_response.headers["content-disposition"]
    assert "dashboard.manifest.v1" in export_response.text

    state_response = await test_client.get(f"/api/dashboard-assets/{asset['id']}/state")
    assert state_response.status_code == 200
    state_payload = state_response.json()["data"]
    assert state_payload["published_version_id"] == published["id"]
    assert len(state_payload["versions"]) == 2

    lineage_response = await test_client.get(f"/api/dashboard-assets/{asset['id']}/lineage")
    assert lineage_response.status_code == 200
    assert lineage_response.json()["data"]["lineage"]["data_views"][0]["lineage"][0]["ref"] == query_id

    audit_response = await test_client.get(f"/api/dashboard-assets/{asset['id']}/audit")
    assert audit_response.status_code == 200
    audit_actions = [item["action"] for item in audit_response.json()["data"]["items"]]
    assert "dashboard.draft.create" in audit_actions
    assert "dashboard.draft.patch" in audit_actions
    assert "dashboard.publish" in audit_actions
    assert "dashboard.preview" in audit_actions
    assert "dashboard.query" in audit_actions
    assert "dashboard.export" in audit_actions


async def test_dashboard_full_manifest_patch_rejects_non_allowlisted_top_level_change(
    test_client,
    test_session,
) -> None:
    notebook = await _seed_notebook(test_session)
    manifest = _manifest_payload()
    create_response = await test_client.post(
        "/api/dashboard-assets",
        json={
            "slug": "rest-governed-dashboard-full-manifest-guard",
            "notebook_id": str(notebook.id),
            "manifest": manifest,
            "change_summary": "create guard fixture",
        },
    )
    assert create_response.status_code == 201
    asset = create_response.json()["data"]

    blocked_manifest = deepcopy(manifest)
    blocked_manifest["dashboard_id"] = "blocked-dashboard-id-rewrite"
    patch_response = await test_client.patch(
        f"/api/dashboard-assets/{asset['id']}/draft",
        json={
            "base_etag": asset["etag"],
            "manifest": blocked_manifest,
            "change_summary": "attempt non-allowlisted full manifest change",
        },
    )

    assert patch_response.status_code == 403
    data = patch_response.json()["data"]
    assert data["code"] == "dashboard_manifest_patch_forbidden"
    assert data["blocked_keys"] == ["dashboard_id"]
