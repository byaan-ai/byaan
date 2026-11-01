from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.repositories.external_mcp_servers import ExternalMCPServerRepository
from server.schemas.external_mcp_server import (
    ExternalMCPServerCreate,
    ExternalMCPServerListResponse,
    ExternalMCPServerResponse,
    ExternalMCPServerUpdate,
    TestConfigRequest,
    TestConnectionResponse,
)
from server.schemas.standard_response import success_response
from server.services.external_mcp_service import ExternalMCPService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/external-mcp-servers", tags=["external-mcp"])


async def _to_response(repo: ExternalMCPServerRepository, record) -> ExternalMCPServerResponse:
    decrypted, decrypt_error = await ExternalMCPService.decrypt_safely(repo, record)
    masked: dict = {}
    if decrypted is not None:
        masked = ExternalMCPService.mask_response_config(record.transport, decrypted)
    return ExternalMCPServerResponse(
        id=record.id,
        name=record.name,
        transport=record.transport,
        config=masked,
        enabled=record.enabled,
        tool_filter=ExternalMCPServerRepository.parse_tool_filter(record),
        decrypt_error=decrypt_error,
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
    success, tools, error = await ExternalMCPService.test_connection(record.transport, decrypted)
    return success_response(
        data=TestConnectionResponse(success=success, tools=tools, error=error).model_dump(),
        message="Connection test completed",
    )
