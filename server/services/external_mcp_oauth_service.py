from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import UUID

import httpx
from mcp.client.auth.oauth2 import PKCEParameters

from server.services.external_mcp_service import _validate_remote_url
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

PENDING_TTL_SECONDS = 600
TOKEN_REFRESH_SKEW_SECONDS = 300
DISCOVERY_TIMEOUT = 10.0
TOKEN_TIMEOUT = 30.0

_oauth_pending: dict[str, dict[str, Any]] = {}
_oauth_results: dict[str, dict[str, Any]] = {}
_refresh_locks: dict[str, asyncio.Lock] = {}


def record_result(state: str, status: str, name: str | None = None, error: str | None = None) -> None:
    _oauth_results[state] = {"status": status, "name": name, "error": error, "ts": time.time()}
    _prune_results()


def _prune_results() -> None:
    now = time.time()
    stale = [k for k, v in _oauth_results.items() if now - v["ts"] > PENDING_TTL_SECONDS]
    for k in stale:
        _oauth_results.pop(k, None)


def pop_result(state: str) -> dict[str, Any] | None:
    _prune_results()
    return _oauth_results.pop(state, None)


def peek_result(state: str) -> dict[str, Any] | None:
    _prune_results()
    return _oauth_results.get(state)


def _get_refresh_lock(server_id: str) -> asyncio.Lock:
    lock = _refresh_locks.get(server_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[server_id] = lock
    return lock


def _prune_expired_pending() -> None:
    now = time.time()
    stale = [k for k, v in _oauth_pending.items() if now - v["created_at"] > PENDING_TTL_SECONDS]
    for k in stale:
        _oauth_pending.pop(k, None)


def _guard_url(url: str) -> None:
    err = _validate_remote_url(url)
    if err:
        raise ValueError(err)


async def discover_mcp_oauth(mcp_url: str) -> dict[str, Any]:
    """Probe MCP URL for OAuth metadata per MCP spec 2026-07-28.

    Returns dict with authorization_endpoint, token_endpoint, registration_endpoint,
    scopes_supported, authorization_server, resource (mcp_url).
    """
    _guard_url(mcp_url)
    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT, follow_redirects=True) as client:
        probe = await client.post(
            mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Accept": "application/json, text/event-stream"},
        )

        if probe.status_code != 401:
            raise ValueError(
                f"Server at {mcp_url} did not require OAuth (status {probe.status_code}). "
                "Use Static Bearer auth instead."
            )

        www_auth = probe.headers.get("WWW-Authenticate", "")
        prm_url = _extract_www_auth_field(www_auth, "resource_metadata")
        if not prm_url:
            prm_url = _build_default_prm_url(mcp_url)

        _guard_url(prm_url)
        prm_resp = await client.get(prm_url)
        if prm_resp.status_code >= 400:
            raise ValueError(f"Protected Resource Metadata unreachable at {prm_url}: {prm_resp.status_code}")
        prm = prm_resp.json()

        auth_servers = prm.get("authorization_servers") or []
        if not auth_servers:
            raise ValueError("Protected Resource Metadata missing authorization_servers")
        auth_server = auth_servers[0]
        _guard_url(auth_server)

        as_meta_url = auth_server.rstrip("/") + "/.well-known/oauth-authorization-server"
        _guard_url(as_meta_url)
        as_resp = await client.get(as_meta_url)
        if as_resp.status_code >= 400:
            oidc_url = auth_server.rstrip("/") + "/.well-known/openid-configuration"
            _guard_url(oidc_url)
            as_resp = await client.get(oidc_url)
        if as_resp.status_code >= 400:
            raise ValueError(f"Authorization server metadata unreachable: {as_resp.status_code}")
        as_meta = as_resp.json()

    authorization_endpoint = as_meta.get("authorization_endpoint")
    token_endpoint = as_meta.get("token_endpoint")
    if not authorization_endpoint or not token_endpoint:
        raise ValueError("Authorization server metadata missing authorization_endpoint or token_endpoint")

    _guard_url(authorization_endpoint)
    _guard_url(token_endpoint)

    return {
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
        "registration_endpoint": as_meta.get("registration_endpoint"),
        "scopes_supported": as_meta.get("scopes_supported") or prm.get("scopes_supported") or [],
        "authorization_server": auth_server,
        "resource": mcp_url,
        "code_challenge_methods_supported": as_meta.get("code_challenge_methods_supported") or ["S256"],
    }


def _extract_www_auth_field(header: str, field: str) -> str | None:
    import re

    m = re.search(rf'{field}="([^"]+)"', header)
    return m.group(1) if m else None


def _build_default_prm_url(mcp_url: str) -> str:
    parsed = urlparse(mcp_url)
    return f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource"


async def register_client(
    registration_endpoint: str | None,
    redirect_uri: str,
    client_name: str,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Dynamic Client Registration (RFC 7591). Returns {client_id, client_secret?}."""
    if not registration_endpoint:
        raise ValueError(
            "This server does not expose Dynamic Client Registration. "
            "Static Bearer auth or pre-registered client required."
        )
    _guard_url(registration_endpoint)

    payload = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if scopes:
        payload["scope"] = " ".join(scopes)

    async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT) as client:
        resp = await client.post(registration_endpoint, json=payload)
        if resp.status_code >= 400:
            raise ValueError(f"Dynamic Client Registration failed: {resp.status_code} {resp.text[:200]}")
        data = resp.json()

    return {
        "client_id": data.get("client_id"),
        "client_secret": data.get("client_secret"),
    }


def build_authorization_url(
    discovery: dict[str, Any],
    client_id: str,
    client_secret: str | None,
    redirect_uri: str,
    tenant_id: UUID,
    user_id: UUID,
    server_name: str,
    mcp_url: str,
    existing_server_id: UUID | None = None,
    scopes: list[str] | None = None,
) -> tuple[str, str]:
    """Generate PKCE pair, persist pending state, return (authorization_url, state)."""
    _prune_expired_pending()

    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)

    _oauth_pending[state] = {
        "code_verifier": pkce.code_verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "token_endpoint": discovery["token_endpoint"],
        "authorization_endpoint": discovery["authorization_endpoint"],
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "server_name": server_name,
        "mcp_url": mcp_url,
        "existing_server_id": str(existing_server_id) if existing_server_id else None,
        "scopes": scopes or [],
        "created_at": time.time(),
    }

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
    }
    if scopes:
        params["scope"] = " ".join(scopes)

    authorization_url = f"{discovery['authorization_endpoint']}?{urlencode(params)}"
    return authorization_url, state


async def exchange_code(code: str, state: str, user_id: UUID) -> dict[str, Any]:
    """Exchange authorization code for tokens. Returns full config dict ready to encrypt."""
    _prune_expired_pending()
    pending = _oauth_pending.pop(state, None)
    if not pending:
        raise ValueError("Invalid or expired OAuth state. Please restart the connection flow.")

    if pending["user_id"] != str(user_id):
        _oauth_pending[state] = pending
        raise ValueError("OAuth callback user mismatch. Session may have been hijacked.")

    _guard_url(pending["token_endpoint"])

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": pending["client_id"],
        "redirect_uri": pending["redirect_uri"],
        "code_verifier": pending["code_verifier"],
    }
    if pending.get("client_secret"):
        data["client_secret"] = pending["client_secret"]

    async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
        resp = await client.post(
            pending["token_endpoint"],
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise ValueError(f"Token exchange failed: {resp.status_code} {resp.text[:200]}")
        tokens = resp.json()

    expires_in = tokens.get("expires_in")
    expires_at = time.time() + expires_in if expires_in else None

    return {
        "url": pending["mcp_url"],
        "auth_type": "oauth",
        "authorization_endpoint": pending["authorization_endpoint"],
        "token_endpoint": pending["token_endpoint"],
        "client_id": pending["client_id"],
        "client_secret": pending.get("client_secret"),
        "scopes": pending["scopes"],
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": expires_at,
        "pending_meta": {
            "server_name": pending["server_name"],
            "tenant_id": pending["tenant_id"],
            "existing_server_id": pending["existing_server_id"],
        },
    }


async def refresh_access_token(server_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Refresh OAuth access token. Returns new cfg with updated access_token + expires_at.

    Idempotent + per-server-locked. Caller persists result via repo.update.
    """
    lock = _get_refresh_lock(server_id)
    async with lock:
        now = time.time()
        expires_at = cfg.get("expires_at")
        if expires_at and expires_at - now > TOKEN_REFRESH_SKEW_SECONDS:
            return cfg

        refresh_token = cfg.get("refresh_token")
        token_endpoint = cfg.get("token_endpoint")
        client_id = cfg.get("client_id")
        if not refresh_token or not token_endpoint or not client_id:
            raise ValueError("Server is missing OAuth credentials for refresh; reconnect required.")

        _guard_url(token_endpoint)

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if cfg.get("client_secret"):
            data["client_secret"] = cfg["client_secret"]

        async with httpx.AsyncClient(timeout=TOKEN_TIMEOUT) as client:
            resp = await client.post(
                token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code >= 400:
                raise ValueError(f"Token refresh failed: {resp.status_code} {resp.text[:200]}")
            tokens = resp.json()

        new_cfg = dict(cfg)
        new_cfg["access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            new_cfg["refresh_token"] = tokens["refresh_token"]
        expires_in = tokens.get("expires_in")
        new_cfg["expires_at"] = time.time() + expires_in if expires_in else None
        return new_cfg


def resolve_redirect_uri(frontend_url: str | None, is_hosted: bool, backend_port: int = 8000) -> str:
    """Build OAuth callback redirect URI based on deployment mode."""
    if is_hosted and frontend_url:
        return f"{frontend_url.rstrip('/')}/api/external-mcp-servers/oauth/callback"
    return f"http://localhost:{backend_port}/api/external-mcp-servers/oauth/callback"
