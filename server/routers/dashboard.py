from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.models.dashboard import Dashboard, DashboardAsset, DashboardAuditEvent
from server.models.notebooks import Notebook
from server.repositories.dashboard import DashboardRepository
from server.schemas.standard_response import success_response
from server.services.dashboard import DashboardService

router = APIRouter()


class DashboardAssetCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=160)
    notebook_id: UUID
    manifest: dict[str, Any]
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    change_summary: str = "Create structured dashboard draft"


class DashboardDraftPatchRequest(BaseModel):
    base_etag: str = Field(min_length=1)
    manifest: dict[str, Any] | None = None
    json_patch: list[dict[str, Any]] | None = None
    change_summary: str = Field(default="Update structured dashboard draft", min_length=1)


class DashboardValidateRequest(BaseModel):
    manifest: dict[str, Any] | None = None


class DashboardPublishRequest(BaseModel):
    base_etag: str = Field(min_length=1)
    change_summary: str = Field(default="Publish structured dashboard", min_length=1)


class DashboardReloadRequest(BaseModel):
    base_etag: str = Field(min_length=1)
    semantic_model_versions: dict[str, str] = Field(default_factory=dict)
    source_snapshot_ids: list[str] | None = None
    change_summary: str = Field(default="Reload Dashboard semantic bindings", min_length=1)


class DashboardQueryRequest(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    data_view_ids: list[str] | None = None
    mode: Literal["live", "pinned_snapshot"] = "live"
    correlation_id: str | None = None
    idempotency_key: str | None = None


class DashboardPreviewRequest(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    data_view_ids: list[str] | None = None
    correlation_id: str | None = None


def _require_non_viewer(auth: AuthContext) -> None:
    if auth.is_viewer:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewer access to governed dashboards must use shared viewer routes",
        )


async def _assert_notebook_access(session: AsyncSession, notebook_id: UUID, auth: AuthContext) -> None:
    notebook = await session.scalar(
        select(Notebook).where(Notebook.id == notebook_id, Notebook.tenant_id == auth.tenant_id)
    )
    if not notebook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")
    if not auth.has_scope(Scope.NOTEBOOK_READ) and str(notebook.created_by) != str(auth.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only use notebooks you created")


async def _get_asset_or_404(repo: DashboardRepository, auth: AuthContext, asset_id: UUID) -> DashboardAsset:
    asset = await repo.get_asset(asset_id, auth.tenant_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found")
    return asset


async def _get_version_or_404(
    repo: DashboardRepository,
    auth: AuthContext,
    asset_id: UUID,
    version_num: int,
) -> Dashboard:
    version = await repo.get_asset_version_by_num(
        tenant_id=auth.tenant_id,
        asset_id=asset_id,
        version_num=version_num,
    )
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard version not found")
    return version


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _asset_payload(asset: DashboardAsset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "tenant_id": str(asset.tenant_id),
        "notebook_id": str(asset.notebook_id) if asset.notebook_id else None,
        "slug": asset.slug,
        "name": asset.name,
        "description": asset.description,
        "owner_id": str(asset.owner_id) if asset.owner_id else None,
        "tags": asset.tags_json or [],
        "lifecycle": asset.lifecycle,
        "current_draft_version_id": str(asset.current_draft_version_id) if asset.current_draft_version_id else None,
        "published_version_id": str(asset.published_version_id) if asset.published_version_id else None,
        "access_policy": asset.access_policy_json or {},
        "freshness_policy": asset.freshness_policy_json or {},
        "consumer_summary": asset.consumer_summary_json or {},
        "health_summary": asset.health_summary_json or {},
        "etag": asset.etag,
        "created_at": _dt(asset.created_at),
        "updated_at": _dt(asset.updated_at),
    }


def _version_summary_payload(version: Dashboard) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "asset_id": str(version.asset_id) if version.asset_id else None,
        "notebook_id": str(version.notebook_id),
        "version_num": version.version_num,
        "manifest_schema_version": version.manifest_schema_version,
        "content_hash": version.content_hash,
        "status": version.status,
        "created_by": str(version.created_by) if version.created_by else None,
        "actor_type": version.actor_type,
        "change_summary": version.change_summary,
        "pinned_model_versions": version.pinned_model_versions_json or {},
        "pinned_source_snapshots": version.pinned_source_snapshots_json or [],
        "validation_result": version.validation_result_json or {},
        "renderer_version": version.renderer_version,
        "migration_state": version.migration_state,
        "is_published_immutable": version.is_published_immutable,
        "created_at": _dt(version.created_at),
    }


def _version_payload(version: Dashboard) -> dict[str, Any]:
    payload = _version_summary_payload(version)
    payload["manifest"] = version.manifest_json or {}
    return payload


def _audit_payload(event: DashboardAuditEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "asset_id": str(event.asset_id) if event.asset_id else None,
        "version_id": str(event.version_id) if event.version_id else None,
        "run_id": str(event.run_id) if event.run_id else None,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "action": event.action,
        "correlation_id": event.correlation_id,
        "before_digest": event.before_digest,
        "after_digest": event.after_digest,
        "outcome": event.outcome,
        "details": event.details_json or {},
        "created_at": _dt(event.created_at),
    }


def _lineage_from_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    manifest = manifest or {}
    data_views = manifest.get("data_views") or []
    return {
        "dashboard_id": manifest.get("dashboard_id"),
        "semantic_bindings": manifest.get("semantic_bindings") or [],
        "data_views": [
            {
                "id": data_view.get("id"),
                "kind": data_view.get("kind"),
                "lineage": data_view.get("lineage") or data_view.get("saved_query", {}).get("lineage", []),
                "evidence": data_view.get("evidence") or [],
            }
            for data_view in data_views
        ],
        "migration": manifest.get("migration") or {},
    }


@router.get("/dashboard-assets")
async def list_dashboard_assets(
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    assets = await repo.list_assets(auth.tenant_id)
    return success_response(
        data={"items": [_asset_payload(asset) for asset in assets], "total": len(assets)},
        message=f"Retrieved {len(assets)} dashboard asset(s)",
    )


@router.post("/dashboard-assets", status_code=status.HTTP_201_CREATED)
async def create_dashboard_asset(
    payload: DashboardAssetCreateRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    await _assert_notebook_access(session, payload.notebook_id, auth)
    asset = await DashboardService().create_asset_draft(
        session=session,
        tenant_id=auth.tenant_id,
        actor_id=auth.user_id,
        manifest_payload=payload.manifest,
        slug=payload.slug,
        notebook_id=payload.notebook_id,
        description=payload.description,
        tags=payload.tags,
        change_summary=payload.change_summary,
        actor_type="human",
    )
    return success_response(data=_asset_payload(asset), message="Dashboard asset draft created")


@router.get("/dashboard-assets/{asset_id}")
async def get_dashboard_asset(
    asset_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    asset = await _get_asset_or_404(repo, auth, asset_id)
    versions = await repo.list_asset_versions(tenant_id=auth.tenant_id, asset_id=asset_id)
    return success_response(
        data={**_asset_payload(asset), "versions": [_version_summary_payload(version) for version in versions]},
        message="Retrieved dashboard asset",
    )


@router.get("/dashboard-assets/{asset_id}/versions/{version_num}")
async def get_dashboard_asset_version(
    asset_id: UUID,
    version_num: int,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    await _get_asset_or_404(repo, auth, asset_id)
    version = await _get_version_or_404(repo, auth, asset_id, version_num)
    return success_response(data=_version_payload(version), message="Retrieved dashboard version")


@router.patch("/dashboard-assets/{asset_id}/draft")
async def patch_dashboard_draft(
    asset_id: UUID,
    payload: DashboardDraftPatchRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    asset = await _get_asset_or_404(repo, auth, asset_id)
    if asset.notebook_id:
        await _assert_notebook_access(session, asset.notebook_id, auth)
    if payload.json_patch is not None:
        version = await DashboardService().apply_draft_patch(
            session=session,
            tenant_id=auth.tenant_id,
            asset_id=asset_id,
            actor_id=auth.user_id,
            base_etag=payload.base_etag,
            patch_operations=payload.json_patch,
            change_summary=payload.change_summary,
            actor_type="human",
        )
    elif payload.manifest is not None:
        version = await DashboardService().patch_draft(
            session=session,
            tenant_id=auth.tenant_id,
            asset_id=asset_id,
            actor_id=auth.user_id,
            manifest_payload=payload.manifest,
            base_etag=payload.base_etag,
            change_summary=payload.change_summary,
            actor_type="human",
        )
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="manifest or json_patch is required")
    return success_response(data=_version_payload(version), message="Dashboard draft patched")


@router.post("/dashboard-assets/{asset_id}/validate")
async def validate_dashboard_asset(
    asset_id: UUID,
    payload: DashboardValidateRequest | None = None,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    asset = await _get_asset_or_404(repo, auth, asset_id)
    manifest = payload.manifest if payload and payload.manifest is not None else None
    if manifest is None:
        if not asset.current_draft_version_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dashboard has no editable draft")
        draft = await repo.get_asset_version(
            tenant_id=auth.tenant_id,
            asset_id=asset_id,
            version_id=asset.current_draft_version_id,
        )
        if not draft:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard draft not found")
        manifest = draft.manifest_json or {}
    validated_manifest = DashboardService.validate_manifest_payload(manifest)
    validation = DashboardService.validation_summary(validated_manifest)
    return success_response(
        data={"validation": validation, "manifest": validated_manifest}, message="Dashboard validated"
    )


@router.post("/dashboard-assets/{asset_id}/preview")
async def preview_dashboard_asset(
    asset_id: UUID,
    payload: DashboardPreviewRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    run = await DashboardService().preview_dashboard(
        session=session,
        tenant_id=auth.tenant_id,
        asset_id=asset_id,
        actor_id=str(auth.user_id),
        actor_type="human",
        filters=payload.filters,
        data_view_ids=payload.data_view_ids,
        correlation_id=payload.correlation_id,
    )
    return success_response(data=run, message="Dashboard preview executed")


@router.post("/dashboard-assets/{asset_id}/publish")
async def publish_dashboard_asset(
    asset_id: UUID,
    payload: DashboardPublishRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    version = await DashboardService().publish(
        session=session,
        tenant_id=auth.tenant_id,
        asset_id=asset_id,
        actor_id=auth.user_id,
        base_etag=payload.base_etag,
        change_summary=payload.change_summary,
        actor_type="human",
    )
    return success_response(data=_version_payload(version), message="Dashboard published")


@router.post("/dashboard-assets/{asset_id}/reload")
async def reload_dashboard_asset(
    asset_id: UUID,
    payload: DashboardReloadRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    asset = await _get_asset_or_404(repo, auth, asset_id)
    if asset.notebook_id:
        await _assert_notebook_access(session, asset.notebook_id, auth)
    version, semantic_diff = await DashboardService().reload_dashboard(
        session=session,
        tenant_id=auth.tenant_id,
        asset_id=asset_id,
        actor_id=auth.user_id,
        base_etag=payload.base_etag,
        semantic_model_versions=payload.semantic_model_versions,
        source_snapshot_ids=payload.source_snapshot_ids,
        change_summary=payload.change_summary,
        actor_type="human",
    )
    return success_response(
        data={"draft": _version_payload(version), "semantic_diff": semantic_diff},
        message="Dashboard reload draft created",
    )


@router.post("/dashboard-assets/{asset_id}/query")
async def query_dashboard_asset(
    asset_id: UUID,
    payload: DashboardQueryRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    run = await DashboardService().query_dashboard(
        session=session,
        tenant_id=auth.tenant_id,
        asset_id=asset_id,
        actor_id=str(auth.user_id),
        actor_type="human",
        filters=payload.filters,
        data_view_ids=payload.data_view_ids,
        mode=payload.mode,
        correlation_id=payload.correlation_id,
        idempotency_key=payload.idempotency_key,
    )
    return success_response(data=run, message="Dashboard query executed")


@router.get("/dashboard-assets/{asset_id}/state")
async def get_dashboard_asset_state(
    asset_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    asset = await _get_asset_or_404(repo, auth, asset_id)
    versions = await repo.list_asset_versions(tenant_id=auth.tenant_id, asset_id=asset_id)
    return success_response(
        data={
            "asset": _asset_payload(asset),
            "versions": [_version_summary_payload(version) for version in versions],
            "draft_version_id": str(asset.current_draft_version_id) if asset.current_draft_version_id else None,
            "published_version_id": str(asset.published_version_id) if asset.published_version_id else None,
        },
        message="Retrieved dashboard state",
    )


@router.get("/dashboard-assets/{asset_id}/lineage")
async def get_dashboard_asset_lineage(
    asset_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    asset = await _get_asset_or_404(repo, auth, asset_id)
    version = None
    if asset.published_version_id:
        version = await repo.get_asset_version(
            tenant_id=auth.tenant_id,
            asset_id=asset_id,
            version_id=asset.published_version_id,
        )
    if version is None and asset.current_draft_version_id:
        version = await repo.get_asset_version(
            tenant_id=auth.tenant_id,
            asset_id=asset_id,
            version_id=asset.current_draft_version_id,
        )
    return success_response(
        data={
            "asset_id": str(asset.id),
            "version_id": str(version.id) if version else None,
            "lineage": _lineage_from_manifest(version.manifest_json if version else None),
        },
        message="Retrieved dashboard lineage",
    )


@router.get("/dashboard-assets/{asset_id}/audit")
async def get_dashboard_asset_audit(
    asset_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    repo = DashboardRepository(session)
    await _get_asset_or_404(repo, auth, asset_id)
    events = await repo.list_asset_audit_events(tenant_id=auth.tenant_id, asset_id=asset_id)
    return success_response(
        data={"items": [_audit_payload(event) for event in events], "total": len(events)},
        message=f"Retrieved {len(events)} dashboard audit event(s)",
    )


@router.get("/dashboard-assets/{asset_id}/export/html")
async def export_dashboard_asset_html(
    asset_id: UUID,
    version_num: int | None = None,
    correlation_id: str | None = None,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_non_viewer(auth)
    html_content, filename = await DashboardService().export_dashboard_html(
        session=session,
        tenant_id=auth.tenant_id,
        asset_id=asset_id,
        actor_id=auth.user_id,
        actor_type="human",
        version_num=version_num,
        correlation_id=correlation_id,
    )
    return Response(
        content=html_content,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
