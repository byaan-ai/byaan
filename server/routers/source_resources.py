from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_any_scope, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.schemas.source_resources import SourceResourceCreate, SourceResourceRead, SourceResourceSyncRequest
from server.schemas.standard_response import StandardResponse, success_response
from server.services.source_resources import SourceResourceService

router = APIRouter()
source_resource_service = SourceResourceService()


def _bad_request_or_not_found(error: ValueError) -> HTTPException:
    message = str(error)
    code = status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)


@router.post(
    "/source-resources",
    response_model=StandardResponse[SourceResourceRead],
    status_code=status.HTTP_201_CREATED,
)
async def create_source_resource(
    payload: SourceResourceCreate,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        data = await source_resource_service.create_resource(
            session=session,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            payload=payload,
            include_all_connections=auth.is_admin,
        )
        return success_response(data=data, message="Source resource created")
    except ValueError as error:
        raise _bad_request_or_not_found(error)


@router.get("/source-resources", response_model=StandardResponse[dict])
async def list_source_resources(
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    items = await source_resource_service.list_resources(session=session, tenant_id=auth.tenant_id)
    return success_response(data={"items": items, "total": len(items)}, message="Retrieved source resources")


@router.get("/source-resources/{resource_id}", response_model=StandardResponse[SourceResourceRead])
async def get_source_resource(
    resource_id: str,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    resource = await source_resource_service.get_resource(
        session=session,
        tenant_id=auth.tenant_id,
        resource_id=resource_id,
    )
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source resource not found")
    data = await source_resource_service.resource_payload(session=session, resource=resource)
    return success_response(data=data, message="Retrieved source resource")


@router.get("/source-resources/{resource_id}/snapshots", response_model=StandardResponse[dict])
async def list_source_resource_snapshots(
    resource_id: str,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        data = await source_resource_service.list_snapshots(
            session=session,
            tenant_id=auth.tenant_id,
            resource_id=resource_id,
        )
        return success_response(data=data, message="Retrieved source resource snapshots")
    except ValueError as error:
        raise _bad_request_or_not_found(error)


@router.post("/source-resources/{resource_id}/sync", response_model=StandardResponse[SourceResourceRead])
async def sync_source_resource(
    resource_id: str,
    payload: SourceResourceSyncRequest,
    auth: AuthContext = Depends(require_any_scope(Scope.DATASET_UPDATE, Scope.DATASET_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        data = await source_resource_service.sync_resource(
            session=session,
            tenant_id=auth.tenant_id,
            resource_id=resource_id,
            payload=payload,
            user_id=auth.user_id,
            include_all=auth.has_scope(Scope.DATASET_UPDATE),
        )
        return success_response(data=data, message="Source resource sync accepted")
    except ValueError as error:
        raise _bad_request_or_not_found(error)
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error))


@router.delete("/source-resources/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source_resource(
    resource_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.DATASET_DELETE, Scope.DATASET_DELETE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    resource = await source_resource_service.get_resource(
        session=session,
        tenant_id=auth.tenant_id,
        resource_id=resource_id,
    )
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source resource not found")
    if not auth.has_scope(Scope.DATASET_DELETE):
        if resource.owner_id is None or str(resource.owner_id) != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only delete source resources you created",
            )
    await source_resource_service.delete_resource(
        session=session,
        tenant_id=auth.tenant_id,
        resource_id=resource_id,
    )
