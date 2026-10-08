from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.schemas.standard_response import success_response
from server.serializers.sharing import sharing_grant_evidence_payload, sharing_grant_payload
from server.services.sharing import SharingService

router = APIRouter()


def _service_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    if "not found" in detail:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


@router.get("/sharing/grants")
async def list_sharing_grants(
    object_type: str | None = None,
    object_id: UUID | None = None,
    legacy_surface: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 50,
    auth: AuthContext = Depends(require_scope(Scope.SHARING_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    grants = await SharingService(session).list_grants(
        tenant_id=auth.tenant_id,
        object_type=object_type,
        object_id=object_id,
        legacy_surface=legacy_surface,
        status=status_filter,
        limit=limit,
    )
    return success_response(
        data={"items": [sharing_grant_payload(grant) for grant in grants], "total": len(grants)},
        message="Sharing grants listed",
    )


@router.get("/sharing/grants/{grant_id}")
async def describe_sharing_grant(
    grant_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.SHARING_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        evidence = await SharingService(session).grant_evidence(tenant_id=auth.tenant_id, grant_id=grant_id)
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=sharing_grant_evidence_payload(evidence), message="Sharing grant described")
