from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.repositories.external_mcp_servers import ExternalMCPServerRepository
from server.schemas.external_mcp_server import (
    ExternalMCPServerCreate,
    ExternalMCPServerListResponse,
    ExternalMCPServerResponse,
    ExternalMCPServerUpdate,
    OAuthDiscoverRequest,
    OAuthStartRequest,
    OAuthStartResponse,
    TestConfigRequest,
    TestConnectionResponse,
)
from server.schemas.standard_response import success_response
from server.services.external_mcp_oauth_service import (
    _oauth_pending,
    build_authorization_url,
    discover_mcp_oauth,
    exchange_code,
    peek_result,
    pop_result,
    record_result,
    register_client,
    resolve_redirect_uri,
)
from server.services.external_mcp_service import ExternalMCPService
from server.utils.config_loader import get_email_config, is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/external-mcp-servers", tags=["external-mcp"])


def _get_redirect_uri() -> str:
    cfg = get_email_config()
    return resolve_redirect_uri(cfg.get("frontend_url"), is_self_hosted())


async def _to_response(repo: ExternalMCPServerRepository, record) -> ExternalMCPServerResponse:
    decrypted, decrypt_error = await ExternalMCPService.decrypt_safely(repo, record)
    masked: dict = {}
    auth_type = None
    oauth_status = None
    if decrypted is not None:
        masked = ExternalMCPService.mask_response_config(record.transport, decrypted)
        auth_type = decrypted.get("auth_type") or "static_bearer"
        oauth_status = ExternalMCPService.compute_oauth_status(decrypted)
    return ExternalMCPServerResponse(
        id=record.id,
        name=record.name,
        transport=record.transport,
        config=masked,
        enabled=record.enabled,
        tool_filter=ExternalMCPServerRepository.parse_tool_filter(record),
        decrypt_error=decrypt_error,
        auth_type=auth_type,
        oauth_status=oauth_status,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.get("", response_model=dict)
async def list_servers(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = ExternalMCPServerRepository(session)
    records = await repo.list(limit=200)
    items = [await _to_response(repo, r) for r in records]
    return success_response(
        data=ExternalMCPServerListResponse(items=items, total=len(items)).model_dump(mode="json"),
        message=f"Retrieved {len(items)} external MCP servers",
    )


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_server(
    payload: ExternalMCPServerCreate,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    if payload.transport == "stdio" and not ExternalMCPService.is_stdio_allowed():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="stdio transport is not allowed in hosted mode",
        )

    repo = ExternalMCPServerRepository(session)
    existing = await repo.get_by_name(payload.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An external MCP server named '{payload.name}' already exists",
        )

    try:
        record = await repo.create(
            {
                "name": payload.name,
                "transport": payload.transport,
                "config_dict": payload.config,
                "enabled": payload.enabled,
                "tool_filter_list": payload.tool_filter,
                "created_by": auth.user_id,
            }
        )
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An external MCP server named '{payload.name}' already exists",
        )

    logger.info(
        f"Created external MCP server {record.id} ({record.name})",
        extra={"posthog_context": {"tenant_id": str(auth.tenant_id), "user_id": str(auth.user_id)}},
    )
    response = await _to_response(repo, record)
    return success_response(data=response.model_dump(mode="json"), message="External MCP server created")


@router.get("/{server_id}", response_model=dict)
async def get_server(
    server_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = ExternalMCPServerRepository(session)
    record = await repo.get(server_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External MCP server not found")
    response = await _to_response(repo, record)
    return success_response(data=response.model_dump(mode="json"), message="External MCP server")


@router.put("/{server_id}", response_model=dict)
async def update_server(
    server_id: UUID,
    payload: ExternalMCPServerUpdate,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = ExternalMCPServerRepository(session)
    existing = await repo.get(server_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External MCP server not found")

    update_data: dict = {}
    if payload.name is not None:
        name_clash = await repo.get_by_name(payload.name)
        if name_clash is not None and name_clash.id != server_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An external MCP server named '{payload.name}' already exists",
            )
        update_data["name"] = payload.name
    if payload.config is not None:
        update_data["config_dict"] = payload.config
    if payload.enabled is not None:
        update_data["enabled"] = payload.enabled
    if payload.tool_filter is not None:
        update_data["tool_filter_list"] = payload.tool_filter

    if not update_data:
        response = await _to_response(repo, existing)
        return success_response(data=response.model_dump(mode="json"), message="No changes applied")

    try:
        record = await repo.update(server_id, update_data)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Name conflict")
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External MCP server not found")
    response = await _to_response(repo, record)
    return success_response(data=response.model_dump(mode="json"), message="External MCP server updated")


@router.delete("/{server_id}", response_model=dict)
async def delete_server(
    server_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = ExternalMCPServerRepository(session)
    ok = await repo.delete(server_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External MCP server not found")
    logger.info(
        f"Deleted external MCP server {server_id}",
        extra={"posthog_context": {"tenant_id": str(auth.tenant_id), "user_id": str(auth.user_id)}},
    )
    return success_response(message="External MCP server deleted")


@router.post("/test-config", response_model=dict)
async def test_config(
    payload: TestConfigRequest,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
):
    if payload.transport == "stdio" and not ExternalMCPService.is_stdio_allowed():
        return success_response(
            data=TestConnectionResponse(
                success=False, tools=[], error="stdio transport is disabled in hosted mode"
            ).model_dump(),
            message="Connection test completed",
        )
    success, tools, error = await ExternalMCPService.test_connection(payload.transport, payload.config)
    return success_response(
        data=TestConnectionResponse(success=success, tools=tools, error=error).model_dump(),
        message="Connection test completed",
    )


@router.post("/{server_id}/test", response_model=dict)
async def test_server(
    server_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = ExternalMCPServerRepository(session)
    record = await repo.get(server_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External MCP server not found")
    decrypted, decrypt_error = await ExternalMCPService.decrypt_safely(repo, record)
    if decrypted is None:
        return success_response(
            data=TestConnectionResponse(success=False, tools=[], error=decrypt_error).model_dump(),
            message="Connection test completed",
        )

    if decrypted.get("auth_type") == "oauth":
        try:
            decrypted = await ExternalMCPService._ensure_fresh_oauth_token(repo=repo, record=record, cfg=decrypted)
        except Exception as exc:
            return success_response(
                data=TestConnectionResponse(
                    success=False,
                    tools=[],
                    error=f"OAuth token refresh failed: {exc}. Click Reconnect.",
                ).model_dump(),
                message="Connection test completed",
            )

    success, tools, error = await ExternalMCPService.test_connection(record.transport, decrypted)
    return success_response(
        data=TestConnectionResponse(success=success, tools=tools, error=error).model_dump(),
        message="Connection test completed",
    )


@router.post("/oauth/discover", response_model=dict)
async def oauth_discover(
    payload: OAuthDiscoverRequest,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
):
    try:
        discovery = await discover_mcp_oauth(payload.mcp_url)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return success_response(data=discovery, message="OAuth discovery completed")


@router.post("/oauth/start", response_model=dict)
async def oauth_start(
    payload: OAuthStartRequest,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = ExternalMCPServerRepository(session)
    existing = await repo.get_by_name(payload.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An external MCP server named '{payload.name}' already exists",
        )

    try:
        discovery = await discover_mcp_oauth(payload.mcp_url)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"OAuth discovery failed: {exc}")

    redirect_uri = _get_redirect_uri()
    try:
        client_info = await register_client(
            registration_endpoint=discovery.get("registration_endpoint"),
            redirect_uri=redirect_uri,
            client_name=f"Byaan ({payload.name})",
            scopes=payload.scopes or discovery.get("scopes_supported") or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if not client_info.get("client_id"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth client registration did not return a client_id",
        )

    authorization_url, state = build_authorization_url(
        discovery=discovery,
        client_id=client_info["client_id"],
        client_secret=client_info.get("client_secret"),
        redirect_uri=redirect_uri,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        server_name=payload.name,
        mcp_url=payload.mcp_url,
        existing_server_id=None,
        scopes=payload.scopes or discovery.get("scopes_supported") or [],
    )
    _oauth_pending[state]["transport"] = payload.transport
    _oauth_pending[state]["enabled"] = payload.enabled
    _oauth_pending[state]["tool_filter"] = payload.tool_filter

    logger.info(f"Started OAuth flow for external MCP '{payload.name}' (state={state[:8]}...)")
    return success_response(
        data=OAuthStartResponse(authorization_url=authorization_url, state=state).model_dump(),
        message="OAuth flow started",
    )


@router.post("/{server_id}/oauth/reconnect", response_model=dict)
async def oauth_reconnect(
    server_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo = ExternalMCPServerRepository(session)
    record = await repo.get(server_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="External MCP server not found")

    decrypted, decrypt_error = await ExternalMCPService.decrypt_safely(repo, record)
    if decrypted is None or decrypted.get("auth_type") != "oauth":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reconnect is only supported for OAuth-configured servers.",
        )

    mcp_url = decrypted.get("url", "")
    try:
        discovery = await discover_mcp_oauth(mcp_url)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"OAuth discovery failed: {exc}")

    client_id = decrypted.get("client_id")
    client_secret = decrypted.get("client_secret")
    if not client_id:
        redirect_uri = _get_redirect_uri()
        client_info = await register_client(
            registration_endpoint=discovery.get("registration_endpoint"),
            redirect_uri=redirect_uri,
            client_name=f"Byaan ({record.name})",
            scopes=decrypted.get("scopes") or discovery.get("scopes_supported") or None,
        )
        client_id = client_info["client_id"]
        client_secret = client_info.get("client_secret")

    redirect_uri = _get_redirect_uri()
    authorization_url, state = build_authorization_url(
        discovery=discovery,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        server_name=record.name,
        mcp_url=mcp_url,
        existing_server_id=record.id,
        scopes=decrypted.get("scopes") or [],
    )
    _oauth_pending[state]["transport"] = record.transport
    _oauth_pending[state]["enabled"] = record.enabled
    _oauth_pending[state]["tool_filter"] = ExternalMCPServerRepository.parse_tool_filter(record)

    return success_response(
        data=OAuthStartResponse(authorization_url=authorization_url, state=state).model_dump(),
        message="OAuth reconnect started",
    )


def _oauth_result_html(status_param: str, name: str | None, msg: str | None) -> str:
    import json
    from html import escape

    is_success = status_param == "success"
    title = "Connected ✓" if is_success else "Connection failed"
    body = (
        f"External MCP server <strong>{escape(name)}</strong> connected successfully. You may close this tab."
        if is_success and name
        else "External MCP server connected. You may close this tab."
        if is_success
        else f"OAuth connection failed: {escape(msg or 'unknown error')}. You may close this tab."
    )
    color = "#10b981" if is_success else "#ef4444"

    def _js_safe(value: str | None) -> str:
        if value is None:
            return "null"
        return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    payload_js = (
        "{ type: 'byaan-mcp-oauth', status: "
        + _js_safe(status_param)
        + ", name: "
        + _js_safe(name)
        + ", msg: "
        + _js_safe(msg)
        + " }"
    )

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; background: #0d0d0d; color: #fff; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
.card {{ max-width: 420px; padding: 32px; border-radius: 12px; background: #1a1a1a; border: 1px solid #333; text-align: center; }}
.icon {{ width: 56px; height: 56px; border-radius: 50%; background: {color}1a; color: {color}; display: inline-flex; align-items: center; justify-content: center; font-size: 32px; margin-bottom: 16px; border: 2px solid {color}; }}
h1 {{ margin: 0 0 12px; font-size: 20px; }}
p {{ margin: 0; color: #aaa; font-size: 14px; line-height: 1.5; }}
</style></head><body>
<div class="card">
<div class="icon">{"✓" if is_success else "✗"}</div>
<h1>{escape(title)}</h1>
<p>{body}</p>
</div>
<script>
try {{ if (window.opener) {{ window.opener.postMessage({payload_js}, '*'); }} }} catch (e) {{}}
setTimeout(() => {{ try {{ window.close(); }} catch (e) {{}} }}, 2500);
</script>
</body></html>"""


@router.get("/oauth/pending-status/{state}", response_model=dict)
async def oauth_pending_status(
    state: str,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
):
    result = peek_result(state)
    if result is None:
        return success_response(data={"status": "pending"}, message="OAuth flow in progress")
    pop_result(state)
    return success_response(data=result, message="OAuth flow completed")


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: AsyncSession = Depends(get_async_session),
):
    from fastapi.responses import HTMLResponse

    def _complete(status_param: str, msg: str | None = None, name: str | None = None) -> HTMLResponse:
        if state:
            record_result(state, status_param, name=name, error=msg)
        return HTMLResponse(content=_oauth_result_html(status_param, name, msg))

    if error:
        return _complete("error", msg=error_description or error)

    if not code or not state:
        return _complete("error", msg="Missing authorization code or state")

    pending = _oauth_pending.get(state)
    if not pending:
        return _complete("error", msg="OAuth session expired. Please restart the flow.")

    user_id = UUID(pending["user_id"])
    tenant_id = UUID(pending["tenant_id"])
    server_name = pending["server_name"]
    transport = pending.get("transport", "streamable_http")
    enabled = pending.get("enabled", True)
    tool_filter = pending.get("tool_filter")
    existing_server_id = pending.get("existing_server_id")

    try:
        config_payload = await exchange_code(code=code, state=state, user_id=user_id)
    except Exception as exc:
        logger.error(f"OAuth code exchange failed: {exc}")
        return _complete("error", msg=str(exc))

    config_payload.pop("pending_meta", None)

    set_tenant_id(tenant_id)
    repo = ExternalMCPServerRepository(session)
    try:
        if existing_server_id:
            await repo.update(
                UUID(existing_server_id),
                {"config_dict": config_payload, "enabled": enabled},
            )
            logger.info(f"OAuth reconnect completed for server {existing_server_id} ('{server_name}')")
        else:
            await repo.create(
                {
                    "name": server_name,
                    "transport": transport,
                    "config_dict": config_payload,
                    "enabled": enabled,
                    "tool_filter_list": tool_filter,
                    "created_by": user_id,
                }
            )
            logger.info(f"OAuth connect completed for new server '{server_name}'")
    except IntegrityError:
        await session.rollback()
        return _complete("error", name=server_name, msg="A server with this name already exists.")
    except Exception as exc:
        await session.rollback()
        logger.error(f"Failed to persist OAuth-connected server: {exc}")
        return _complete("error", name=server_name, msg=str(exc))

    return _complete("success", name=server_name)
