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

import asyncio
import base64
import hashlib
import os
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.config_loader import get_email_config, is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

# Databricks built-in public OAuth client for U2M PKCE flows. Its only
# registered redirect URI is http://localhost:8020 (fixed port, no path),
# so we bind an ephemeral loopback listener on 8020 during each flow.
# Used in desktop/local mode where browser + backend share a host.
DATABRICKS_PUBLIC_CLIENT_ID = "databricks-cli"
DATABRICKS_PUBLIC_REDIRECT_URI = "http://localhost:8020"
DATABRICKS_LOOPBACK_PORT = 8020
DATABRICKS_LOOPBACK_TIMEOUT_SECONDS = 300

# In hosted mode an admin registers a custom OAuth app in their Databricks
# Account Console and stores the credentials in the Settings table.
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
        return (get_email_config().get("frontend_url") or "").rstrip("/")
    return ""


def get_redirect_uri() -> str:
    """In hosted mode, redirect through the FastAPI callback at the public
    frontend URL. In desktop/local mode, use the public-client loopback URI."""
    if is_self_hosted():
        frontend_url = _get_frontend_url()
        if frontend_url:
            return f"{frontend_url}/api/connections/databricks/oauth/callback"
    return DATABRICKS_PUBLIC_REDIRECT_URI


def _normalize_host(server_hostname: str) -> str:
    host = server_hostname.strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    host = host.split("?", 1)[0]
    return host.strip().rstrip(".")


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
    """Return (client_id, client_secret).

    - Hosted mode: load admin-configured custom OAuth app from the Settings table.
    - Desktop/local: fall back to the Databricks built-in public client (PKCE-only,
      no secret) so users can sign in without any admin configuration.
    """
    if is_self_hosted() and session is not None:
        from server.services.crypto_service import CryptoService
        from server.services.settings import SettingsService

        client_id_setting = await SettingsService.get_setting_by_key(session, DATABRICKS_OAUTH_CLIENT_ID_KEY)
        if client_id_setting and client_id_setting.setting_value:
            client_id = client_id_setting.setting_value
            secret_setting = await SettingsService.get_setting_by_key(session, DATABRICKS_OAUTH_CLIENT_SECRET_KEY)
            client_secret = ""
            if secret_setting and secret_setting.setting_value:
                try:
                    decrypted = await CryptoService.decrypt_config(secret_setting.setting_value, session)
                    client_secret = decrypted.get("value", "")
                except Exception:
                    logger.error("[DATABRICKS OAUTH] Failed to decrypt client secret")
            return client_id, client_secret

    return DATABRICKS_PUBLIC_CLIENT_ID, ""


async def is_oauth_configured(session: AsyncSession | None = None) -> bool:
    """Hosted mode requires admin-supplied credentials; desktop/local always
    works via the public client."""
    if not is_self_hosted():
        return True
    client_id, _ = await get_oauth_credentials(session)
    return bool(client_id) and client_id != DATABRICKS_PUBLIC_CLIENT_ID


_SUCCESS_PAGE = (
    b"<!doctype html><html><head><title>Databricks Connected</title>"
    b"<style>body{font-family:system-ui;background:#0d0d0d;color:#eee;"
    b"display:flex;align-items:center;justify-content:center;height:100vh;margin:0}</style>"
    b"</head><body><div style='text-align:center'>"
    b"<h2 style='color:#ff7a00'>Databricks connected</h2>"
    b"<p>You can close this tab and return to Byaan.</p>"
    b"</div></body></html>"
)

_ERROR_PAGE_TEMPLATE = (
    "<!doctype html><html><head><title>Databricks Error</title>"
    "<style>body{{font-family:system-ui;background:#0d0d0d;color:#eee;"
    "display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}</style>"
    "</head><body><div style='text-align:center;max-width:560px'>"
    "<h2 style='color:#f87171'>Databricks sign-in failed</h2>"
    "<p style='color:#aaa'>{message}</p></div></body></html>"
)

_active_loopback_servers: dict[str, asyncio.AbstractServer] = {}


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

    # databricks-cli public client only accepts http://localhost:8020 as redirect,
    # so for that flow we must run a loopback listener to receive the code.
    if redirect_uri == DATABRICKS_PUBLIC_REDIRECT_URI:
        await _start_loopback_listener(state, client_id)

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


async def _start_loopback_listener(state: str, client_id: str) -> None:
    """Bind 127.0.0.1:8020 and wait for the first callback hit. On hit,
    parse code+state from the GET query, exchange for tokens, store result,
    and close the listener. Auto-closes on timeout."""

    done = asyncio.Event()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        terminate = False
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            try:
                method, path, _ = request_line.decode("latin-1").split(" ", 2)
            except ValueError:
                return
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break

            qs = parse_qs(urlparse(path).query)
            code = (qs.get("code") or [None])[0]
            cb_state = (qs.get("state") or [None])[0]
            err = (qs.get("error_description") or qs.get("error") or [None])[0]

            # Ignore stray hits (favicon, preflight, browser probes) without
            # tearing down the listener — only a payload that carries the
            # OAuth response should complete the flow.
            if not (code or err):
                writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                return

            terminate = True

            if err or not code or not cb_state:
                body = _ERROR_PAGE_TEMPLATE.format(message=err or "Missing code/state").encode()
                writer.write(
                    b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                await writer.drain()
                logger.error(f"[DATABRICKS OAUTH] Loopback callback error: {err}")
                return

            stored = peek_state(cb_state)
            try:
                tokens = await exchange_code(code=code, state=cb_state, client_id=client_id, client_secret="")
                store_result(cb_state, tokens, stored)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(_SUCCESS_PAGE)).encode() + b"\r\n\r\n" + _SUCCESS_PAGE
                )
            except Exception as e:
                logger.error(f"[DATABRICKS OAUTH] Token exchange failed in loopback: {e}", exc_info=True)
                body = _ERROR_PAGE_TEMPLATE.format(message=str(e)).encode()
                writer.write(
                    b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
            await writer.drain()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug(f"[DATABRICKS OAUTH] Ignoring loopback writer close error: {e}", exc_info=True)
            if terminate:
                done.set()

    try:
        server = await asyncio.start_server(handle_client, host="0.0.0.0", port=DATABRICKS_LOOPBACK_PORT)
    except OSError as e:
        _oauth_state_store.pop(state, None)
        raise ValueError(
            f"Could not bind localhost:{DATABRICKS_LOOPBACK_PORT} for Databricks OAuth callback. "
            "Make sure no other process (e.g. databricks CLI) is using that port, then retry."
        ) from e

    _active_loopback_servers[state] = server
    logger.info(
        f"[DATABRICKS OAUTH] Loopback listener bound on 127.0.0.1:{DATABRICKS_LOOPBACK_PORT} state={state[:16]}..."
    )

    async def _serve_until_done() -> None:
        try:
            await asyncio.wait_for(done.wait(), timeout=DATABRICKS_LOOPBACK_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning(f"[DATABRICKS OAUTH] Loopback listener timed out state={state[:16]}...")
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("[DATABRICKS OAUTH] Loopback server crashed")
        finally:
            server.close()
            try:
                await server.wait_closed()
            except Exception as e:
                logger.debug(f"[DATABRICKS OAUTH] Ignoring loopback wait_closed() error: {e}")
            _active_loopback_servers.pop(state, None)
            _oauth_state_store.pop(state, None)

    asyncio.create_task(_serve_until_done())


def cancel_flow(state: str) -> bool:
    """Abort an in-progress OAuth flow: close its loopback listener and drop
    any state/result entries. Returns True if anything was found to cancel."""
    found = False
    server = _active_loopback_servers.pop(state, None)
    if server is not None:
        found = True
        try:
            server.close()
        except Exception as e:
            logger.debug(f"[DATABRICKS OAUTH] Ignoring cancel close error: {e}")
    if _oauth_state_store.pop(state, None) is not None:
        found = True
    if _oauth_result_store.pop(state, None) is not None:
        found = True
    if found:
        logger.info(f"[DATABRICKS OAUTH] Cancelled flow state={state[:16]}...")
    return found


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
