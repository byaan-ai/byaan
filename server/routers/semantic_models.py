from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_any_scope, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.schemas.semantic_models import SemanticModelCreate, SemanticModelPatch
from server.schemas.standard_response import StandardResponse, success_response
from server.services.semantic_model_service import SemanticModelService

router = APIRouter()


def _bad_request_or_not_found(error: ValueError) -> HTTPException:
    message = str(error)
    code = status.HTTP_404_NOT_FOUND if "not found" in message.lower() else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)


@router.post("/data-models", response_model=StandardResponse[dict], status_code=status.HTTP_201_CREATED)
async def create_data_model(
    payload: SemanticModelCreate,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        model = await SemanticModelService.create_model(
            session=session,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            payload=payload,
        )
        return success_response(data=SemanticModelService.model_to_payload(model), message="Data Model created")
    except ValueError as error:
        raise _bad_request_or_not_found(error)


@router.get("/data-models", response_model=StandardResponse[dict])
async def list_data_models(
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    items = await SemanticModelService.list_models(session=session, tenant_id=auth.tenant_id)
    return success_response(data={"items": items, "total": len(items)}, message="Retrieved Data Models")


@router.get("/data-models/{model_slug}", response_model=StandardResponse[dict])
async def get_data_model(
    model_slug: str,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    model = await SemanticModelService.get_model(session=session, tenant_id=auth.tenant_id, slug=model_slug)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data Model not found")
    return success_response(data=SemanticModelService.model_to_payload(model), message="Retrieved Data Model")


@router.patch("/data-models/{model_slug}", response_model=StandardResponse[dict])
async def update_data_model(
    model_slug: str,
    payload: SemanticModelPatch,
    auth: AuthContext = Depends(require_any_scope(Scope.DATASET_UPDATE, Scope.DATASET_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        model = await SemanticModelService.update_model(
            session=session,
            tenant_id=auth.tenant_id,
            slug=model_slug,
            user_id=auth.user_id,
            payload=payload,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    except ValueError as error:
        raise _bad_request_or_not_found(error)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data Model not found")
    return success_response(data=SemanticModelService.model_to_payload(model), message="Data Model updated")


@router.post("/data-models/{model_slug}/validate", response_model=StandardResponse[dict])
async def validate_data_model(
    model_slug: str,
    auth: AuthContext = Depends(require_any_scope(Scope.DATASET_UPDATE, Scope.DATASET_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    model = await SemanticModelService.validate_model(
        session=session,
        tenant_id=auth.tenant_id,
        slug=model_slug,
        user_id=auth.user_id,
    )
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data Model not found")
    return success_response(
        data=SemanticModelService.model_to_payload(model), message="Data Model validation is partial"
    )


@router.post("/data-models/{model_slug}/publish", response_model=StandardResponse[dict])
async def publish_data_model(
    model_slug: str,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        model = await SemanticModelService.publish_model(session=session, tenant_id=auth.tenant_id, slug=model_slug)
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data Model not found")
    return success_response(data=SemanticModelService.model_to_payload(model), message="Data Model published")


@router.post("/data-models/{model_slug}/mcp/query_metric", response_model=StandardResponse[dict])
async def query_data_model_metric(
    model_slug: str,
    auth: AuthContext = Depends(require_scope(Scope.QUERY_EXECUTE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        model = await SemanticModelService.query_metric(session=session, tenant_id=auth.tenant_id, slug=model_slug)
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data Model not found")
    return success_response(data=SemanticModelService.model_to_payload(model), message="Semantic metric query executed")


@router.get("/semantic-models", response_model=StandardResponse[dict])
async def list_semantic_models(
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    items = await SemanticModelService.list_models(session=session, tenant_id=auth.tenant_id)
    return success_response(data={"items": items, "total": len(items)}, message="Retrieved semantic models")


@router.get("/semantic-models/{model_slug}", response_model=StandardResponse[dict])
async def get_semantic_model(
    model_slug: str,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    model = await SemanticModelService.get_model(session=session, tenant_id=auth.tenant_id, slug=model_slug)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Semantic model not found")
    return success_response(data=SemanticModelService.model_to_payload(model), message="Retrieved semantic model")
