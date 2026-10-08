from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_any_scope, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.schemas.source_connections import SourceConnectionCreate
from server.schemas.standard_response import StandardResponse, success_response
from server.services.source_connections import SourceConnectionService

router = APIRouter()
source_connection_service = SourceConnectionService()


def _http_error(error: ValueError) -> HTTPException:
    message = str(error)
    code = status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)


@router.get("/connector-definitions", response_model=StandardResponse[dict])
async def list_connector_definitions(
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
):
    return success_response(
        data=source_connection_service.list_connector_definitions(),
        message="Retrieved connector definitions",
    )


@router.post("/source-connections", response_model=StandardResponse[dict], status_code=status.HTTP_201_CREATED)
async def create_source_connection(
    payload: SourceConnectionCreate,
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        connection = await source_connection_service.create_connection(
            session=session,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            payload=payload,
        )
        return success_response(
            data=source_connection_service.connection_payload(connection),
            message="Source connection created",
        )
    except ValueError as error:
        raise _http_error(error)


@router.get("/source-connections", response_model=StandardResponse[dict])
async def list_source_connections(
    provider: str | None = Query(default=None),
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    connections = await source_connection_service.list_connections(
        session=session,
        tenant_id=auth.tenant_id,
        provider=provider,
        user_id=auth.user_id,
        include_all=auth.is_admin,
    )
    items = [source_connection_service.connection_payload(connection) for connection in connections]
    return success_response(data={"items": items, "total": len(items)}, message="Retrieved source connections")


@router.delete("/source-connections/{connection_id}", response_model=StandardResponse[dict])
async def delete_source_connection(
    connection_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.CONNECTION_DELETE, Scope.CONNECTION_DELETE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    ok, resource_count = await source_connection_service.delete_connection(
        session=session,
        tenant_id=auth.tenant_id,
        connection_id=connection_id,
        user_id=auth.user_id,
        include_all=auth.has_scope(Scope.CONNECTION_DELETE),
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source connection not found")
    return success_response(
        data={"deleted": True, "affected_resource_count": resource_count},
        message="Source connection disconnected",
    )
