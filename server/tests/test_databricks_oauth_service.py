from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from server.services import databricks_oauth_service as svc


def test_generate_pkce_pair_shape():
    verifier, challenge = svc.generate_pkce_pair()
    assert len(verifier) >= 43
    assert len(challenge) >= 43
    assert "=" not in challenge


def test_normalize_host_strips_scheme_and_trailing_slash():
    assert svc._normalize_host("https://adb-1.azuredatabricks.net/") == "adb-1.azuredatabricks.net"
    assert svc._normalize_host("adb-2.azuredatabricks.net") == "adb-2.azuredatabricks.net"


@pytest.mark.asyncio
async def test_create_auth_url_stores_state():
    url, state = await svc.create_auth_url(
        server_hostname="adb-x.azuredatabricks.net",
        client_id="cid",
        redirect_uri="http://localhost:17433/cb",
    )
    assert "oidc/v1/authorize" in url
    assert "code_challenge=" in url
    assert "scope=sql+offline_access+all-apis" in url
    stored = svc.peek_state(state)
    assert stored is not None
    assert stored["server_hostname"] == "adb-x.azuredatabricks.net"
    svc.pop_state(state)


@pytest.mark.asyncio
async def test_exchange_code_invalid_state_raises():
    with pytest.raises(ValueError, match="Invalid or expired state"):
        await svc.exchange_code("code", "missing-state", "cid", "secret")


@pytest.mark.asyncio
async def test_exchange_code_success():
    _, state = await svc.create_auth_url(
        server_hostname="adb-x.azuredatabricks.net",
        client_id="cid",
        redirect_uri="http://localhost/cb",
    )

    response = httpx.Response(
        200,
        json={
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "scope": "sql offline_access all-apis",
            "token_type": "Bearer",
        },
    )

    async def _post(*a, **k):
        return response

    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_post)):
        tokens = await svc.exchange_code("code-xyz", state, "cid", "secret")

    assert tokens["access_token"] == "AT"
    assert tokens["refresh_token"] == "RT"
    assert tokens["server_hostname"] == "adb-x.azuredatabricks.net"
    assert tokens["expires_at"] > time.time()


@pytest.mark.asyncio
async def test_refresh_databricks_token_rotates_and_recomputes_expiry():
    response = httpx.Response(
        200,
        json={"access_token": "new-AT", "refresh_token": "new-RT", "expires_in": 1800, "scope": "sql"},
    )

    async def _post(*a, **k):
        return response

    oauth = {
        "access_token": "old-AT",
        "refresh_token": "old-RT",
        "expires_at": 0,
        "server_hostname": "adb-x.azuredatabricks.net",
    }
    with patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=_post)):
        out = await svc.refresh_databricks_token(oauth, "cid", "secret")

    assert out["access_token"] == "new-AT"
    assert out["refresh_token"] == "new-RT"
    assert out["expires_at"] > time.time()


def test_is_oauth_block_expired_within_skew():
    assert svc.is_oauth_block_expired({"expires_at": int(time.time()) + 100}) is True
    assert svc.is_oauth_block_expired({"expires_at": int(time.time()) + 3600}) is False


def test_result_store_roundtrip_and_pop():
    svc.store_result("st-1", {"access_token": "a", "expires_at": 1, "server_hostname": "h"})
    entry = svc.pop_result("st-1")
    assert entry is not None
    assert entry["tokens"]["access_token"] == "a"
    assert svc.pop_result("st-1") is None


@pytest.mark.asyncio
async def test_list_warehouses_normalizes_response():
    response = httpx.Response(
        200,
        json={
            "warehouses": [
                {"id": "w1", "name": "Big", "state": "RUNNING", "cluster_size": "XL"},
                {"id": "w2", "name": "Small", "state": "STOPPED", "cluster_size": "S"},
            ]
        },
    )

    async def _get(*a, **k):
        return response

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=_get)):
        out = await svc.list_warehouses("adb-x.azuredatabricks.net", "AT")

    assert [w["id"] for w in out] == ["w1", "w2"]
    assert out[0]["http_path"] == "/sql/1.0/warehouses/w1"
