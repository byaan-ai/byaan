from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_discover_returns_catalog_tree(test_client):
    tree = [
        {"name": "main", "schemas": ["default", "gold"]},
        {"name": "analytics", "schemas": ["events"]},
    ]
    with (
        patch(
            "server.routers.connections.AsyncDatabricksConnector.list_catalog_tree",
            new=AsyncMock(return_value=tree),
        ),
        patch("server.routers.connections.AsyncDatabricksConnector.close", new=AsyncMock(return_value=None)),
    ):
        resp = await test_client.post(
            "/api/connections/databricks/discover",
            json={
                "server_hostname": "adb-1.databricks.net",
                "http_path": "/sql/1.0/warehouses/x",
                "access_token": "dapi-fake",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    data = body.get("data") if isinstance(body, dict) and "data" in body else body
    catalogs = data["catalogs"]
    assert [c["name"] for c in catalogs] == ["main", "analytics"]
    assert catalogs[0]["schemas"] == ["default", "gold"]


@pytest.mark.asyncio
async def test_discover_missing_required_field_returns_422(test_client):
    resp = await test_client.post(
        "/api/connections/databricks/discover",
        json={"server_hostname": "adb-1.databricks.net"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_discover_connection_error_returns_422(test_client):
    with (
        patch(
            "server.routers.connections.AsyncDatabricksConnector.list_catalog_tree",
            new=AsyncMock(side_effect=RuntimeError("invalid token")),
        ),
        patch("server.routers.connections.AsyncDatabricksConnector.close", new=AsyncMock(return_value=None)),
    ):
        resp = await test_client.post(
            "/api/connections/databricks/discover",
            json={
                "server_hostname": "adb-1.databricks.net",
                "http_path": "/sql/1.0/warehouses/x",
                "access_token": "bad",
            },
        )
    assert resp.status_code == 422
    body = resp.json()
    assert "invalid token" in body.get("message", "") or "invalid token" in str(body)


@pytest.mark.asyncio
async def test_discover_timeout_returns_504(test_client, monkeypatch):
    monkeypatch.setattr("server.routers.connections.DATABRICKS_DISCOVER_TIMEOUT_SECONDS", 0.05)

    async def slow_tree(self):
        await asyncio.sleep(1)
        return []

    with (
        patch("server.routers.connections.AsyncDatabricksConnector.list_catalog_tree", new=slow_tree),
        patch("server.routers.connections.AsyncDatabricksConnector.close", new=AsyncMock(return_value=None)),
    ):
        resp = await test_client.post(
            "/api/connections/databricks/discover",
            json={
                "server_hostname": "adb-1.databricks.net",
                "http_path": "/sql/1.0/warehouses/x",
                "access_token": "dapi-fake",
            },
        )
    assert resp.status_code == 504
