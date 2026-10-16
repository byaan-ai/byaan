from __future__ import annotations

import pytest
from sqlalchemy import select

from server.models.semantic_models import SemanticModel, SemanticModelAuditEvent

pytestmark = pytest.mark.asyncio


async def _create_source_resource(test_client) -> dict:
    response = await test_client.post(
        "/api/source-resources",
        json={
            "resource_type": "web",
            "name": "Metric contract source",
            "source_url": "https://example.test/metrics",
            "content": "Gross revenue is net paid order amount.",
            "external_revision": "rev-semantic-1",
            "metadata": {"source": "semantic-beta-test"},
        },
    )
    assert response.status_code == 201
    return response.json()["data"]


async def test_semantic_model_beta_lifecycle_keeps_partial_and_blocks_execution(test_client, test_session) -> None:
    source = await _create_source_resource(test_client)
    snapshot_id = source["latest_snapshot"]["id"]

    create_response = await test_client.post(
        "/api/data-models",
        json={
            "slug": "sales-beta",
            "name": "Sales Beta",
            "domain": "Sales",
            "owner": "Revenue Analytics",
            "datasource_id": source["id"],
            "datasource_name": source["name"],
            "datasource_kind": "source_resource",
            "source_resource_ids": [source["id"]],
            "source_snapshot_ids": [snapshot_id],
            "manifest": {
                "entities": [{"id": "orders", "table": "orders"}],
                "metrics": [{"id": "gross_revenue", "formula": "SUM(orders.net_amount)"}],
                "dimensions": [{"id": "region", "entityId": "orders", "field": "region"}],
                "provenance": {"source": "unit-test"},
            },
        },
    )

    assert create_response.status_code == 201
    created = create_response.json()["data"]
    assert created["commercialStatus"] == "PARTIAL"
    assert created["runtimeStatus"] == "not_certified"
    assert created["publishedVersion"] is None
    assert created["readinessDetail"]["status"] == "PARTIAL"
    assert created["readinessDetail"]["blockers"]

    list_response = await test_client.get("/api/semantic-models")
    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 1

    validate_response = await test_client.post("/api/data-models/sales-beta/validate")
    assert validate_response.status_code == 200
    validated = validate_response.json()["data"]
    assert validated["status"] == "beta"
    assert validated["readinessLevel"] == "partial"
    assert validated["readinessDetail"]["valid"] is False
    assert validated["readinessDetail"]["blockers"]

    publish_response = await test_client.post("/api/data-models/sales-beta/publish")
    assert publish_response.status_code == 409
    assert "beta" in publish_response.json()["message"].lower()

    query_response = await test_client.post(
        "/api/data-models/sales-beta/mcp/query_metric",
        json={"metric": "gross_revenue"},
    )
    assert query_response.status_code == 409
    assert "beta-blocked" in query_response.json()["message"]

    model = (await test_session.execute(select(SemanticModel).where(SemanticModel.slug == "sales-beta"))).scalar_one()
    assert model.published_version is None
    audit_actions = (
        (
            await test_session.execute(
                select(SemanticModelAuditEvent.action).where(SemanticModelAuditEvent.model_id == model.id)
            )
        )
        .scalars()
        .all()
    )
    assert "semantic_model.beta.create" in audit_actions
    assert "semantic_model.beta.validate" in audit_actions


async def test_semantic_model_patch_requires_revision_and_source_provenance(test_client) -> None:
    source = await _create_source_resource(test_client)
    snapshot_id = source["latest_snapshot"]["id"]
    create_response = await test_client.post(
        "/api/data-models",
        json={
            "slug": "support-beta",
            "name": "Support Beta",
            "source_resource_ids": [source["id"]],
            "source_snapshot_ids": [snapshot_id],
            "manifest": {"entities": [], "metrics": []},
        },
    )
    assert create_response.status_code == 201

    missing_revision_response = await test_client.patch(
        "/api/data-models/support-beta",
        json={"name": "Support Beta Updated"},
    )
    assert missing_revision_response.status_code == 400
    assert "expected_revision" in missing_revision_response.json()["message"]

    stale_response = await test_client.patch(
        "/api/data-models/support-beta",
        json={"expected_revision": 99, "name": "Support Beta Updated"},
    )
    assert stale_response.status_code == 409
    assert "revision" in stale_response.json()["message"]

    bad_source_response = await test_client.patch(
        "/api/data-models/support-beta",
        json={"expected_revision": 1, "source_snapshot_ids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert bad_source_response.status_code == 404

    patch_response = await test_client.patch(
        "/api/data-models/support-beta",
        json={
            "expected_revision": 1,
            "name": "Support Beta Updated",
            "manifest": {
                "entities": [{"id": "tickets", "table": "tickets"}],
                "metrics": [{"id": "ticket_count", "formula": "COUNT(*)"}],
            },
        },
    )
    assert patch_response.status_code == 200
    patched = patch_response.json()["data"]
    assert patched["revision"] == 2
    assert patched["name"] == "Support Beta Updated"
    assert patched["commercialStatus"] == "PARTIAL"
