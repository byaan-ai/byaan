"""
Databricks OAuth (U2M) PKCE Service
-----------------------------------
Authorization Code + PKCE flow against a Databricks workspace OIDC endpoint.
Modelled on `github_service.py` for credential storage and on
`claude_oauth_service.py` for PKCE mechanics.

Per-workspace endpoints:
    https://<server_hostname>/oidc/v1/authorize
    https://<server_hostname>/oidc/v1/token

Admins register Byaan once as a custom OAuth app integration in their
Databricks account and paste client_id + client_secret in Settings.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.config_loader import get_databricks_oauth_config, get_email_config, is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

_oauth_config = get_databricks_oauth_config()
DATABRICKS_CLIENT_ID = _oauth_config.get("client_id") or ""
DATABRICKS_CLIENT_SECRET = _oauth_config.get("client_secret") or ""

DATABRICKS_OAUTH_CLIENT_ID_KEY = "databricks_oauth_client_id"
DATABRICKS_OAUTH_CLIENT_SECRET_KEY = "databricks_oauth_client_secret"

DATABRICKS_SCOPES = "sql offline_access all-apis"

REFRESH_SKEW_SECONDS = 300
RESULT_TTL_SECONDS = 300

_oauth_state_store: dict[str, dict[str, Any]] = {}
_oauth_result_store: dict[str, dict[str, Any]] = {}


def _get_frontend_url() -> str:
    url = os.getenv("FRONTEND_URL", "").rstrip("/")
    if url:
        return url
    if is_self_hosted():
        return get_email_config().get("frontend_url", "").rstrip("/")
    return ""


def get_redirect_uri() -> str:
    frontend_url = _get_frontend_url()
    if frontend_url:
        return f"{frontend_url}/api/connections/databricks/oauth/callback"
    return "byaan://databricks/callback"


def _normalize_host(server_hostname: str) -> str:
    host = server_hostname.strip()
    if host.startswith("http://") or host.startswith("https://"):
        host = host.split("://", 1)[1]
    return host.rstrip("/")


def _authorize_url(host: str) -> str:
    return f"https://{host}/oidc/v1/authorize"


def _token_url(host: str) -> str:
    return f"https://{host}/oidc/v1/token"


def generate_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


async def get_oauth_credentials(session: AsyncSession | None = None) -> tuple[str, str]:
    """Return (client_id, client_secret). Settings-backed in self-hosted, env-backed otherwise."""
    if not is_self_hosted():
        return DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET

    if not session:
        return "", ""

    from server.services.crypto_service import CryptoService
    from server.services.settings import SettingsService

    client_id_setting = await SettingsService.get_setting_by_key(session, DATABRICKS_OAUTH_CLIENT_ID_KEY)
    if not client_id_setting:
        return "", ""

    client_id = client_id_setting.setting_value
    secret_setting = await SettingsService.get_setting_by_key(session, DATABRICKS_OAUTH_CLIENT_SECRET_KEY)
    if not secret_setting:
        return client_id, ""

    try:
        decrypted = await CryptoService.decrypt_config(secret_setting.setting_value, session)
        client_secret = decrypted.get("value", "")
    except Exception:
        logger.error("[DATABRICKS OAUTH] Failed to decrypt client secret")
        return client_id, ""

    return client_id, client_secret


async def is_oauth_configured(session: AsyncSession | None = None) -> bool:
    client_id, client_secret = await get_oauth_credentials(session)
    return bool(client_id and client_secret)


async def create_auth_url(
    server_hostname: str,
    client_id: str,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    redirect_uri: str | None = None,
) -> tuple[str, str]:
    host = _normalize_host(server_hostname)
    redirect_uri = redirect_uri or get_redirect_uri()
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    _oauth_state_store[state] = {
        "code_verifier": code_verifier,
        "server_hostname": host,
        "redirect_uri": redirect_uri,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "user_id": str(user_id) if user_id else None,
        "created_at": time.time(),
    }
    logger.info(f"[DATABRICKS OAUTH] Created auth URL for host={host} state={state[:16]}...")

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": DATABRICKS_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{_authorize_url(host)}?{urlencode(params)}", state


def pop_state(state: str) -> dict[str, Any] | None:
    return _oauth_state_store.pop(state, None)


def peek_state(state: str) -> dict[str, Any] | None:
    return _oauth_state_store.get(state)


async def exchange_code(
    code: str,
    state: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Exchange auth code for tokens. Pops the state entry on success.

    Returns the raw Databricks token response augmented with `expires_at` (epoch s)
    and `server_hostname` (so the connector can refresh later without needing the
    workspace URL re-supplied).
    """
    stored = _oauth_state_store.pop(state, None)
    if not stored:
        raise ValueError("Invalid or expired state parameter. Please restart the authentication flow.")

    host = stored["server_hostname"]
    redirect_uri = stored["redirect_uri"]
    code_verifier = stored["code_verifier"]

    logger.info(f"[DATABRICKS OAUTH] Exchanging code for tokens at {host}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _token_url(host),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
            auth=(client_id, client_secret) if client_secret else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        logger.error(f"[DATABRICKS OAUTH] Token exchange failed: {response.status_code} - {response.text}")
        raise ValueError(f"Token exchange failed: {response.text}")

    tokens = response.json()
    tokens["server_hostname"] = host
    tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 3600))
    return tokens


def store_result(state: str, tokens: dict[str, Any], stored_state: dict[str, Any] | None = None) -> None:
    _oauth_result_store[state] = {
        "tokens": tokens,
        "stored_at": time.time(),
        "context": stored_state or {},
    }
    _gc_results()


def pop_result(state: str) -> dict[str, Any] | None:
    _gc_results()
    return _oauth_result_store.pop(state, None)


def _gc_results() -> None:
    now = time.time()
    expired = [s for s, v in _oauth_result_store.items() if now - v["stored_at"] > RESULT_TTL_SECONDS]
    for s in expired:
        _oauth_result_store.pop(s, None)


def is_oauth_block_expired(oauth_block: dict[str, Any]) -> bool:
    expires_at = oauth_block.get("expires_at", 0)
    return time.time() >= (expires_at - REFRESH_SKEW_SECONDS)


async def refresh_databricks_token(
    oauth_block: dict[str, Any],
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Refresh using the stored refresh_token. Returns updated oauth block
    (caller is responsible for persisting it back to the connection row)."""
    host = oauth_block.get("server_hostname")
    refresh_token = oauth_block.get("refresh_token")
    if not host or not refresh_token:
        raise ValueError("Cannot refresh Databricks token: missing server_hostname or refresh_token")

    logger.info(f"[DATABRICKS OAUTH] Refreshing token for {host}")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _token_url(host),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            auth=(client_id, client_secret) if client_secret else None,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        logger.error(f"[DATABRICKS OAUTH] Refresh failed: {response.status_code} - {response.text}")
        raise ValueError(f"Token refresh failed: {response.text}")

    new_tokens = response.json()
    return {
        "access_token": new_tokens["access_token"],
        "refresh_token": new_tokens.get("refresh_token", refresh_token),
        "expires_at": int(time.time()) + int(new_tokens.get("expires_in", 3600)),
        "scope": new_tokens.get("scope", oauth_block.get("scope")),
        "server_hostname": host,
    }


async def list_warehouses(server_hostname: str, access_token: str) -> list[dict[str, Any]]:
    """Hit /api/2.0/sql/warehouses and return a normalized list for the picker UI."""
    host = _normalize_host(server_hostname)
    url = f"https://{host}/api/2.0/sql/warehouses"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})

    if response.status_code != 200:
        logger.error(f"[DATABRICKS OAUTH] list_warehouses failed: {response.status_code} - {response.text}")
        raise ValueError(f"Could not list warehouses: {response.text}")

    payload = response.json()
    warehouses = payload.get("warehouses", []) or []
    return [
        {
            "id": w.get("id"),
            "name": w.get("name"),
            "state": w.get("state"),
            "size": w.get("cluster_size"),
            "http_path": f"/sql/1.0/warehouses/{w.get('id')}",
        }
        for w in warehouses
        if w.get("id")
    ]
