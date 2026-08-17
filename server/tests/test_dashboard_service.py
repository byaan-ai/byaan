from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.dashboard import Dashboard, DashboardAuditEvent, DashboardRun
from server.models.notebooks import Notebook
from server.models.tenant import Tenant
from server.models.user import User
from server.services.dashboard import DashboardService


def _saved_query_manifest(query_id: str, dashboard_id: str = "dash-saved-query") -> dict:
    return {
        "schema_version": "dashboard.manifest.v1",
        "dashboard_id": dashboard_id,
        "title": "Saved query dashboard",
        "description": "Compatibility dashboard",
        "audience": ["finance"],
        "semantic_bindings": [
            {
                "id": "sales-model",
                "model_slug": "sales",
                "model_version": "v1",
                "source_snapshot_ids": ["snapshot-1"],
                "allowed_metrics": ["revenue"],
            }
        ],
        "data_views": [
            {
                "id": "dv-saved-revenue",
                "kind": "saved_query",
                "question": "What revenue did the saved query return?",
                "output_schema": [{"name": "revenue", "data_type": "number", "unit": "USD"}],
                "filter_fields": ["region"],
                "saved_query": {
                    "query_id": query_id,
                    "compatibility_reason": "legacy reviewed dashboard query",
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


def _semantic_manifest() -> dict:
    manifest = _saved_query_manifest(str(uuid4()), "dash-semantic")
    manifest["data_views"] = [
        {
            "id": "dv-paid-revenue",
            "kind": "semantic_metric",
            "question": "What paid revenue is recognized?",
            "output_schema": [{"name": "paid_revenue", "data_type": "number", "unit": "USD"}],
            "filter_fields": ["region"],
            "semantic_metric": {
                "semantic_binding_id": "sales-model",
                "metric": "revenue",
                "dimensions": [],
            },
        }
    ]
    manifest["filters"][0]["source"] = "semantic_field"
    manifest["filters"][0]["affected_data_view_ids"] = ["dv-paid-revenue"]
    manifest["tiles"][0]["data_view_id"] = "dv-paid-revenue"
    return manifest


async def _seed_owner_notebook(test_session: AsyncSession) -> dict[str, UUID]:
    user_id = uuid4()
    tenant_id = uuid4()
    notebook_id = uuid4()
    test_session.add(
        User(
            id=user_id,
            email=f"dashboard-{user_id}@example.test",
            hashed_password="hash",
            is_active=True,
            is_verified=True,
        )
    )
    await test_session.flush()
    test_session.add(Tenant(id=tenant_id, name="Dashboard Tenant", slug=f"dashboard-{tenant_id}", owner_id=user_id))
    await test_session.flush()
    test_session.add(
        Notebook(
            id=notebook_id,
            tenant_id=tenant_id,
            created_by=user_id,
            notebook_name="Dashboard notebook",
        )
    )
    await test_session.commit()
    return {"tenant_id": tenant_id, "user_id": user_id, "notebook_id": notebook_id}


@pytest.mark.asyncio
async def test_create_patch_publish_and_export_dashboard(test_session: AsyncSession) -> None:
    ids = await _seed_owner_notebook(test_session)
    service = DashboardService()
    manifest = _saved_query_manifest(str(uuid4()))

    asset = await service.create_asset_draft(
        session=test_session,
        tenant_id=ids["tenant_id"],
        actor_id=ids["user_id"],
        notebook_id=ids["notebook_id"],
        slug="revenue-ops",
        manifest_payload=manifest,
    )

    assert asset.lifecycle == "draft"
    assert asset.etag.startswith("sha256:")
    assert asset.current_draft_version_id is not None
    draft = await test_session.scalar(select(Dashboard).where(Dashboard.asset_id == asset.id))
    assert draft is not None
    assert draft.status == "draft"
    assert draft.manifest_schema_version == "dashboard.manifest.v1"
    assert draft.pinned_model_versions_json == {"sales": "v1"}
    assert draft.pinned_source_snapshots_json == ["snapshot-1"]
    assert draft.validation_result_json["valid"] is True

    patched_manifest = deepcopy(manifest)
    patched_manifest["title"] = "Revenue operations reviewed"
    patched = await service.patch_draft(
        session=test_session,
        tenant_id=ids["tenant_id"],
        asset_id=asset.id,
        actor_id=ids["user_id"],
        base_etag=asset.etag,
        manifest_payload=patched_manifest,
        change_summary="review title",
    )
    await test_session.refresh(asset)
    assert patched.version_num == 2
    assert asset.current_draft_version_id == patched.id

    published = await service.publish(
        session=test_session,
        tenant_id=ids["tenant_id"],
        asset_id=asset.id,
        actor_id=ids["user_id"],
        base_etag=asset.etag,
        change_summary="publish reviewed dashboard",
    )
    await test_session.refresh(asset)
    assert published.status == "published"
    assert published.is_published_immutable is True
    assert asset.lifecycle == "published"
    assert asset.published_version_id == published.id

    html, filename = await service.export_dashboard_html(
        session=test_session,
        tenant_id=ids["tenant_id"],
        asset_id=asset.id,
        actor_id=ids["user_id"],
    )
    assert filename == "revenue-ops-v2.html"
    assert "Revenue operations reviewed" in html
    assert "dashboard.manifest.v1" in html

    audit_events = (
        await test_session.execute(
            select(DashboardAuditEvent.action)
            .where(DashboardAuditEvent.asset_id == asset.id)
            .order_by(DashboardAuditEvent.created_at)
        )
    ).scalars().all()
    assert audit_events == [
        "dashboard.draft.create",
        "dashboard.draft.patch",
        "dashboard.publish",
        "dashboard.export",
    ]


@pytest.mark.asyncio
async def test_patch_draft_rejects_stale_etag_and_non_allowlisted_manifest_keys(test_session: AsyncSession) -> None:
    ids = await _seed_owner_notebook(test_session)
    service = DashboardService()
    manifest = _saved_query_manifest(str(uuid4()), "dash-conflict")
    asset = await service.create_asset_draft(
        session=test_session,
        tenant_id=ids["tenant_id"],
        actor_id=ids["user_id"],
        notebook_id=ids["notebook_id"],
        slug="revenue-conflict",
        manifest_payload=manifest,
    )

    with pytest.raises(HTTPException) as stale:
        await service.patch_draft(
            session=test_session,
            tenant_id=ids["tenant_id"],
            asset_id=asset.id,
            actor_id=ids["user_id"],
            base_etag="stale",
            manifest_payload=manifest,
            change_summary="stale patch",
        )
    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == "etag_conflict"

    blocked_manifest = deepcopy(manifest)
    blocked_manifest["dashboard_id"] = "dash-full-manifest-rewrite"
    with pytest.raises(HTTPException) as blocked:
        await service.patch_draft(
            session=test_session,
            tenant_id=ids["tenant_id"],
            asset_id=asset.id,
            actor_id=ids["user_id"],
            base_etag=asset.etag,
            manifest_payload=blocked_manifest,
            change_summary="blocked manifest rewrite",
        )
    assert blocked.value.status_code == 403
    assert blocked.value.detail["code"] == "dashboard_manifest_patch_forbidden"
    assert blocked.value.detail["blocked_keys"] == ["dashboard_id"]


@pytest.mark.asyncio
async def test_query_saved_query_dashboard_persists_run_and_blocks_unknown_filters(
    test_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _seed_owner_notebook(test_session)
    query_id = str(uuid4())
    service = DashboardService()
    asset = await service.create_asset_draft(
        session=test_session,
        tenant_id=ids["tenant_id"],
        actor_id=ids["user_id"],
        notebook_id=ids["notebook_id"],
        slug="revenue-query",
        manifest_payload=_saved_query_manifest(query_id),
    )
    published = await service.publish(
        session=test_session,
        tenant_id=ids["tenant_id"],
        asset_id=asset.id,
        actor_id=ids["user_id"],
        base_etag=asset.etag,
        change_summary="publish",
    )

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

    run = await service.query_dashboard(
        session=test_session,
        tenant_id=ids["tenant_id"],
        asset_id=asset.id,
        actor_id=str(ids["user_id"]),
        actor_type="human",
        filters={"region": "AMER"},
        data_view_ids=["dv-saved-revenue"],
        correlation_id="corr-service",
    )

    assert run["dashboard_version_id"] == str(published.id)
    assert run["normalized_filters"] == {"region": "AMER"}
    assert run["views"][0]["result"] == [{"revenue": 42}]
    assert captured["query_id"] == query_id
    assert captured["viewer_user_id"] is None

    saved_run = await test_session.get(DashboardRun, run["run_id"])
    assert saved_run is not None
    assert saved_run.correlation_id == "corr-service"

    with pytest.raises(HTTPException) as unknown_filter:
        await service.query_dashboard(
            session=test_session,
            tenant_id=ids["tenant_id"],
            asset_id=asset.id,
            actor_id=str(ids["user_id"]),
            actor_type="human",
            filters={"raw_sql": "select * from other_tenant.secret"},
            data_view_ids=["dv-saved-revenue"],
        )
    assert unknown_filter.value.status_code == 403


@pytest.mark.asyncio
async def test_semantic_metric_execution_is_explicitly_partial_until_modeling_lands(test_session: AsyncSession) -> None:
    ids = await _seed_owner_notebook(test_session)
    service = DashboardService()
    asset = await service.create_asset_draft(
        session=test_session,
        tenant_id=ids["tenant_id"],
        actor_id=ids["user_id"],
        notebook_id=ids["notebook_id"],
        slug="semantic-partial",
        manifest_payload=_semantic_manifest(),
    )
    await service.publish(
        session=test_session,
        tenant_id=ids["tenant_id"],
        asset_id=asset.id,
        actor_id=ids["user_id"],
        base_etag=asset.etag,
        change_summary="publish",
    )

    run = await service.query_dashboard(
        session=test_session,
        tenant_id=ids["tenant_id"],
        asset_id=asset.id,
        actor_id=str(ids["user_id"]),
        actor_type="human",
        filters={"region": "AMER"},
        data_view_ids=["dv-paid-revenue"],
    )

    assert run["overall_freshness"] == "blocked"
    assert run["views"][0]["status"] == "blocked"
    assert run["views"][0]["error"]["code"] == "semantic_metric_partial"
