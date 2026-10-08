from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.mcp import tool_wrappers
from server.mcp.tool_wrappers import (
    create_semantic_model_wrapper,
    create_source_resource_wrapper,
    describe_semantic_model_wrapper,
    list_semantic_models_wrapper,
    publish_semantic_model_wrapper,
    query_semantic_metric_wrapper,
    update_semantic_model_wrapper,
    validate_semantic_model_wrapper,
)
from server.mcp.tools import register_all_tools
from server.models.semantic_models import SemanticModel
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_mcp_session_factory(test_engine, monkeypatch: pytest.MonkeyPatch):
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(tool_wrappers, "AsyncSessionFactory", test_session_factory)


async def _seed_owner(test_session: AsyncSession) -> tuple[Tenant, User]:
    owner = User(
        id=uuid4(),
        email=f"semantic-mcp-owner-{uuid4()}@example.test",
        hashed_password="fakehash",
        is_active=True,
        is_verified=True,
    )
    test_session.add(owner)
    await test_session.flush()
    tenant = Tenant(
        id=uuid4(),
        name="Semantic MCP Tenant",
        slug=f"semantic-mcp-{uuid4().hex[:8]}",
        owner_id=owner.id,
        is_personal=True,
    )
    test_session.add(tenant)
    await test_session.flush()
    test_session.add(TenantMember(user_id=owner.id, tenant_id=tenant.id, role=TenantRole.OWNER.value))
    await test_session.commit()
    return tenant, owner


async def _create_source_with_snapshot(tenant: Tenant, owner: User) -> dict:
    payload = json.loads(
        await create_source_resource_wrapper(
            json.dumps(
                {
                    "resource_type": "web",
                    "name": "Metric contract source",
                    "source_url": "https://example.test/metrics",
                    "content": "Gross revenue is net paid order amount.",
                    "external_revision": "rev-semantic-mcp-1",
                    "metadata": {"source": "semantic-mcp-test"},
                }
            ),
            tenant.id,
            owner.id,
        )
    )
    assert payload["success"] is True
    return payload["resource"]


async def test_semantic_model_mcp_tools_are_registered() -> None:
    async def get_session():
        return {"session_id": "test", "tenant_id": uuid4(), "user_id": uuid4(), "notebook_id": None}

    mcp = FastMCP("semantic-registration-test")
    register_all_tools(mcp, get_session)

    names = {tool.name for tool in await mcp.list_tools()}

    assert {
        "list_semantic_models",
        "describe_semantic_model",
        "create_semantic_model",
        "update_semantic_model",
        "validate_semantic_model",
        "publish_semantic_model",
        "query_semantic_metric",
    }.issubset(names)


async def test_semantic_model_mcp_beta_lifecycle_and_blocked_runtime(
    test_session: AsyncSession,
) -> None:
    tenant, owner = await _seed_owner(test_session)
    source = await _create_source_with_snapshot(tenant, owner)

    created_payload = json.loads(
        await create_semantic_model_wrapper(
            json.dumps(
                {
                    "slug": "sales-mcp-beta",
                    "name": "Sales MCP Beta",
                    "domain": "Sales",
                    "owner": "Revenue Analytics",
                    "datasource_id": source["id"],
                    "datasource_name": source["name"],
                    "datasource_kind": "source_resource",
                    "source_resource_ids": [source["id"]],
                    "source_snapshot_ids": [source["latest_snapshot"]["id"]],
                    "manifest": {
                        "entities": [{"id": "orders", "table": "orders"}],
                        "metrics": [{"id": "gross_revenue", "formula": "SUM(orders.net_amount)"}],
                        "dimensions": [{"id": "region", "entityId": "orders", "field": "region"}],
                        "provenance": {"source": "mcp-test"},
                    },
                }
            ),
            tenant.id,
            owner.id,
        )
    )
    assert created_payload["success"] is True
    created = created_payload["model"]
    assert created["commercialStatus"] == "PARTIAL"
    assert created["runtimeStatus"] == "not_certified"
    assert created["publishedVersion"] is None
    assert created["readinessDetail"]["status"] == "PARTIAL"
    assert created["readinessDetail"]["valid"] is False

    listed = json.loads(await list_semantic_models_wrapper(tenant.id, owner.id, query="sales"))
    assert listed["success"] is True
    assert listed["items"][0]["slug"] == "sales-mcp-beta"
    assert listed["commercial_status"] == "PARTIAL"

    described = json.loads(await describe_semantic_model_wrapper("sales-mcp-beta", tenant.id, owner.id))
    assert described["success"] is True
    assert described["model"]["sourceSnapshotIds"] == [source["latest_snapshot"]["id"]]

    stale_patch = json.loads(
        await update_semantic_model_wrapper(
            "sales-mcp-beta",
            json.dumps({"expected_revision": 99, "name": "stale"}),
            tenant.id,
            owner.id,
        )
    )
    assert stale_patch["success"] is False
    assert stale_patch["status_code"] == 409

    patched = json.loads(
        await update_semantic_model_wrapper(
            "sales-mcp-beta",
            json.dumps(
                {
                    "expected_revision": 1,
                    "name": "Sales MCP Beta Reviewed",
                    "manifest": {
                        "entities": [{"id": "orders", "table": "orders"}],
                        "metrics": [{"id": "gross_revenue", "formula": "SUM(orders.net_amount)"}],
                    },
                }
            ),
            tenant.id,
            owner.id,
        )
    )
    assert patched["success"] is True
    assert patched["model"]["revision"] == 2
    assert patched["model"]["name"] == "Sales MCP Beta Reviewed"

    validated = json.loads(await validate_semantic_model_wrapper("sales-mcp-beta", tenant.id, owner.id))
    assert validated["success"] is True
    assert validated["model"]["readinessLevel"] == "partial"
    assert validated["model"]["readinessDetail"]["blockers"]

    publish = json.loads(await publish_semantic_model_wrapper("sales-mcp-beta", tenant.id, owner.id))
    assert publish["success"] is False
    assert publish["status_code"] == 409
    assert "beta" in publish["error"].lower()

    metric = json.loads(await query_semantic_metric_wrapper("sales-mcp-beta", tenant.id, owner.id))
    assert metric["success"] is False
    assert metric["status_code"] == 409
    assert "beta-blocked" in metric["error"]

    model = (
        await test_session.execute(select(SemanticModel).where(SemanticModel.slug == "sales-mcp-beta"))
    ).scalar_one()
    assert model.published_version is None
