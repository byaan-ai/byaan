from __future__ import annotations

from uuid import UUID

import pytest

RETELL_PAYLOAD = {
    "name": "retell",
    "transport": "streamable_http",
    "config": {
        "url": "https://mcp.retellai.com",
        "headers": {"Authorization": "Bearer test_key_123"},
    },
    "enabled": True,
}


@pytest.mark.asyncio
async def test_create_list_update_delete_external_mcp(test_client):
    create_resp = await test_client.post("/api/external-mcp-servers", json=RETELL_PAYLOAD)
    assert create_resp.status_code == 201, create_resp.text
    body = create_resp.json()
    assert body["success"] is True
    created = body["data"]
    assert created["name"] == "retell"
    assert created["transport"] == "streamable_http"
    assert created["config"]["headers"]["Authorization"] == "***"
    assert created["config"]["url"] == "https://mcp.retellai.com"
    server_id = created["id"]
    UUID(server_id)

    list_resp = await test_client.get("/api/external-mcp-servers")
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == server_id

    get_resp = await test_client.get(f"/api/external-mcp-servers/{server_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["id"] == server_id

    update_resp = await test_client.put(
        f"/api/external-mcp-servers/{server_id}",
        json={"enabled": False},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["enabled"] is False

    delete_resp = await test_client.delete(f"/api/external-mcp-servers/{server_id}")
    assert delete_resp.status_code == 200

    after_resp = await test_client.get("/api/external-mcp-servers")
    assert after_resp.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_reserved_name_rejected(test_client):
    for reserved in ("byaan", "byaan_tools"):
        resp = await test_client.post(
            "/api/external-mcp-servers",
            json={**RETELL_PAYLOAD, "name": reserved},
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_name_pattern_rejected(test_client):
    resp = await test_client.post(
        "/api/external-mcp-servers",
        json={**RETELL_PAYLOAD, "name": "Bad Name!"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_name_conflict(test_client):
    first = await test_client.post("/api/external-mcp-servers", json=RETELL_PAYLOAD)
    assert first.status_code == 201
    dup = await test_client.post("/api/external-mcp-servers", json=RETELL_PAYLOAD)
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_stdio_blocked_in_hosted_mode(test_client, monkeypatch):
    from server.services.external_mcp_service import ExternalMCPService

    monkeypatch.setattr(ExternalMCPService, "is_stdio_allowed", staticmethod(lambda: False))
    stdio_payload = {
        "name": "github-mcp",
        "transport": "stdio",
        "config": {"command": "npx", "args": ["-y", "@some/mcp"], "env": {}},
        "enabled": True,
    }
    resp = await test_client.post("/api/external-mcp-servers", json=stdio_payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_config_encryption_roundtrip(test_client):
    create_resp = await test_client.post("/api/external-mcp-servers", json=RETELL_PAYLOAD)
    assert create_resp.status_code == 201
    server_id = create_resp.json()["data"]["id"]

    get_resp = await test_client.get(f"/api/external-mcp-servers/{server_id}")
    data = get_resp.json()["data"]
    assert data["config"]["url"] == "https://mcp.retellai.com"
    assert data["config"]["headers"]["Authorization"] == "***"


@pytest.mark.asyncio
async def test_ssrf_blocked_in_hosted_mode(test_client, monkeypatch):
    from server.services import external_mcp_service as svc_mod

    monkeypatch.setattr(svc_mod, "is_self_hosted", lambda: True)

    for bad_url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/internal",
        "http://127.0.0.1/x",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "file:///etc/passwd",
    ):
        resp = await test_client.post(
            "/api/external-mcp-servers/test-config",
            json={"transport": "streamable_http", "config": {"url": bad_url, "headers": {}}},
        )
        assert resp.status_code == 200, bad_url
        body = resp.json()["data"]
        assert body["success"] is False, bad_url
        assert body["error"], bad_url


@pytest.mark.asyncio
async def test_update_rejects_duplicate_name(test_client):
    first = await test_client.post("/api/external-mcp-servers", json=RETELL_PAYLOAD)
    second_payload = {**RETELL_PAYLOAD, "name": "other-mcp"}
    second = await test_client.post("/api/external-mcp-servers", json=second_payload)
    assert first.status_code == 201 and second.status_code == 201

    second_id = second.json()["data"]["id"]
    resp = await test_client.put(
        f"/api/external-mcp-servers/{second_id}",
        json={"name": "retell"},
    )
    assert resp.status_code == 409
