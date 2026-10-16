from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.mcp import tool_wrappers
from server.mcp.tool_wrappers import (
    _json_error,
    create_dashboard_draft_wrapper,
    describe_dashboard_wrapper,
    get_dashboard_lineage_wrapper,
    patch_dashboard_draft_wrapper,
    publish_dashboard_wrapper,
    query_dashboard_wrapper,
    search_dashboards_wrapper,
    validate_dashboard_wrapper,
)
from server.models.dashboard import DashboardRun
from server.models.notebooks import Notebook
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.models.user import User

pytestmark = pytest.mark.asyncio


def test_dashboard_mcp_json_error_redacts_sensitive_exception_values() -> None:
    payload = json.loads(
        _json_error(
            RuntimeError(
                "password=plain-password verifier=argon2id-verifier "
                "token=raw-token credential=worker-credential "
                "sql=select * from other_tenant.secret_orders"
            ),
            operation="query_dashboard",
        )
    )

    serialized = json.dumps(payload)
    assert payload["success"] is False
    assert payload["operation"] == "query_dashboard"
    assert "plain-password" not in serialized
    assert "argon2id-verifier" not in serialized
    assert "raw-token" not in serialized
    assert "worker-credential" not in serialized
    assert "select * from other_tenant.secret_orders" not in serialized


def _manifest_payload(query_id: str) -> dict:
    return {
        "schema_version": "dashboard.manifest.v1",
        "dashboard_id": "dash-mcp",
        "title": "MCP governed dashboard",
        "description": "Dashboard exposed through governed MCP tools",
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
        "provenance": {"created_by_actor_type": "agent", "created_by": "mcp-key-1", "source": "agent"},
        "migration": {"state": "new_structured", "blockers": []},
    }


async def _seed_owner_notebook(test_session: AsyncSession) -> dict:
    user_id = uuid4()
    tenant_id = uuid4()
    test_session.add(
        User(
            id=user_id,
            email=f"dashboard-mcp-owner-{user_id}@example.test",
            hashed_password="hash",
            is_active=True,
            is_verified=True,
        )
    )
    await test_session.flush()
    tenant = Tenant(id=tenant_id, name="Dashboard MCP Tenant", slug=f"dashboard-mcp-{tenant_id}", owner_id=user_id)
    test_session.add(tenant)
    await test_session.flush()
    notebook = Notebook(
        id=uuid4(),
        tenant_id=tenant.id,
        created_by=tenant.owner_id,
        notebook_name="Dashboard MCP notebook",
    )
    test_session.add(notebook)
    await test_session.commit()
    await test_session.refresh(notebook)
    return {"tenant_id": tenant.id, "user_id": tenant.owner_id, "notebook_id": notebook.id}


@pytest.fixture(autouse=True)
def _patch_mcp_session_factory(test_engine, monkeypatch: pytest.MonkeyPatch):
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(tool_wrappers, "AsyncSessionFactory", test_session_factory)


async def test_dashboard_mcp_contract_lifecycle_and_query(
    test_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = await _seed_owner_notebook(test_session)
    query_id = str(uuid4())
    manifest = _manifest_payload(query_id)

    create_payload = json.loads(
        await create_dashboard_draft_wrapper(
            "mcp-governed-dashboard",
            str(ids["notebook_id"]),
            json.dumps(manifest),
            ids["tenant_id"],
            ids["user_id"],
            tags=["finance"],
        )
    )
    assert create_payload["success"] is True
    dashboard = create_payload["dashboard"]
    assert dashboard["slug"] == "mcp-governed-dashboard"

    search_payload = json.loads(
        await search_dashboards_wrapper("mcp", ["finance"], "draft", "", ids["tenant_id"], ids["user_id"])
    )
    assert search_payload["success"] is True
    assert search_payload["items"][0]["id"] == dashboard["id"]

    describe_payload = json.loads(
        await describe_dashboard_wrapper(dashboard["id"], "draft", "full", ids["tenant_id"], ids["user_id"])
    )
    assert describe_payload["success"] is True
    assert describe_payload["version"]["manifest"]["tiles"][0]["id"] == "tile-revenue"

    validate_payload = json.loads(await validate_dashboard_wrapper(dashboard["id"], ids["tenant_id"], ids["user_id"]))
    assert validate_payload["success"] is True
    assert validate_payload["validation"]["valid"] is True

    patch_payload = json.loads(
        await patch_dashboard_draft_wrapper(
            dashboard["id"],
            dashboard["etag"],
            json.dumps([{"op": "replace", "path": "/title", "value": "MCP governed dashboard reviewed"}]),
            "review title through MCP",
            ids["tenant_id"],
            ids["user_id"],
        )
    )
    assert patch_payload["success"] is True
    assert patch_payload["version"]["version_num"] == 2

    stale_payload = json.loads(
        await patch_dashboard_draft_wrapper(
            dashboard["id"],
            dashboard["etag"],
            json.dumps([{"op": "replace", "path": "/title", "value": "stale"}]),
            "stale retry",
            ids["tenant_id"],
            ids["user_id"],
        )
    )
    assert stale_payload["success"] is False
    assert stale_payload["status_code"] == 409
    assert stale_payload["details"]["code"] == "etag_conflict"

    refreshed_payload = json.loads(
        await describe_dashboard_wrapper(dashboard["id"], "draft", "compact", ids["tenant_id"], ids["user_id"])
    )
    publish_payload = json.loads(
        await publish_dashboard_wrapper(
            dashboard["id"],
            refreshed_payload["dashboard"]["etag"],
            "publish through MCP",
            ids["tenant_id"],
            ids["user_id"],
        )
    )
    assert publish_payload["success"] is True
    assert publish_payload["version"]["status"] == "published"

    captured: dict[str, object] = {}

    async def fake_execute_saved_query(session, query_id_arg, filters=None, viewer_user_id=None):
        captured["query_id"] = query_id_arg
        captured["filters"] = filters
        captured["viewer_user_id"] = viewer_user_id
        return {
            "success": True,
            "data": [{"revenue": 42}, {"revenue": 43}],
            "cached": True,
            "stale": False,
            "as_of": "2026-08-16T12:34:56",
        }

    monkeypatch.setattr("server.services.dashboard.QueryService.execute_saved_query", fake_execute_saved_query)

    query_payload = json.loads(
        await query_dashboard_wrapper(
            dashboard["id"],
            ["dv-saved-revenue"],
            {"region": "AMER"},
            "",
            1,
            ids["tenant_id"],
            ids["user_id"],
        )
    )
    assert query_payload["success"] is True
    run = query_payload["run"]
    assert run["actor_type"] == "agent"
    assert run["normalized_filters"] == {"region": "AMER"}
    assert run["views"][0]["result"] == [{"revenue": 42}]
    assert run["views"][0]["pagination"]["has_more"] is True
    assert captured["query_id"] == query_id

    saved_run = await test_session.get(DashboardRun, run["run_id"])
    assert saved_run is not None
    assert saved_run.actor_type == "agent"

    lineage_payload = json.loads(
        await get_dashboard_lineage_wrapper(dashboard["id"], "tile-revenue", ids["tenant_id"], ids["user_id"])
    )
    assert lineage_payload["success"] is True
    assert lineage_payload["lineage"]["data_views"][0]["lineage"][0]["ref"] == query_id


async def test_dashboard_mcp_publish_requires_share_scope(test_session: AsyncSession) -> None:
    ids = await _seed_owner_notebook(test_session)
    member_id = uuid4()
    test_session.add(
        User(
            id=member_id,
            email=f"dashboard-mcp-member-{member_id}@example.test",
            hashed_password="hash",
            is_active=True,
            is_verified=True,
        )
    )
    await test_session.flush()
    test_session.add(TenantMember(user_id=member_id, tenant_id=ids["tenant_id"], role=TenantRole.MEMBER.value))
    await test_session.commit()

    create_payload = json.loads(
        await create_dashboard_draft_wrapper(
            "mcp-member-dashboard",
            str(ids["notebook_id"]),
            json.dumps(_manifest_payload(str(uuid4()))),
            ids["tenant_id"],
            ids["user_id"],
        )
    )
    dashboard = create_payload["dashboard"]

    publish_payload = json.loads(
        await publish_dashboard_wrapper(
            dashboard["id"],
            dashboard["etag"],
            "member cannot publish",
            ids["tenant_id"],
            member_id,
        )
    )
    assert publish_payload["success"] is False
    assert publish_payload["status_code"] == 403
    assert "dashboard.share" in publish_payload["error"]
