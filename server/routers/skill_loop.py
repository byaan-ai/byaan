from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.models.notebooks import Notebook
from server.models.skill_loop_settings import SkillLoopSettings
from server.models.slack_workspace import SlackWorkspace
from server.repositories.skill_loop_settings import SkillLoopSettingsRepository
from server.repositories.slack_workspace import SlackWorkspaceRepository
from server.schemas.skill_loop import SkillLoopRunNowRequest, SkillLoopSettingsUpdate
from server.schemas.standard_response import success_response
from server.services.conversation_evaluation_service import skill_loop_service
from server.services.slack_agent_service import SlackAgentService
from server.services.slack_service import SlackService
from server.utils.config_loader import get_skill_loop_config
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _to_data(settings: SkillLoopSettings, workspace: SlackWorkspace | None) -> dict:
    return {
        "enabled": settings.enabled,
        "digest_enabled": settings.digest_enabled,
        "digest_hour": settings.digest_hour,
        "slack_reviewers_channel_id": workspace.reviewers_channel_id if workspace else None,
        "slack_workspace_connected": workspace is not None,
        "loop_globally_enabled": bool(get_skill_loop_config()["enabled"]),
    }


@router.get("/skill-loop/settings")
async def get_skill_loop_settings(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    settings = await SkillLoopSettingsRepository(session).get_or_defaults(auth.tenant_id)
    workspace = await SlackWorkspaceRepository(session).get_by_tenant(auth.tenant_id)
    return success_response(data=_to_data(settings, workspace), message="Skill loop settings retrieved")


@router.put("/skill-loop/settings")
async def update_skill_loop_settings(
    payload: SkillLoopSettingsUpdate,
    auth: AuthContext = Depends(require_scope(Scope.TENANT_MANAGE_ROLES)),
    session: AsyncSession = Depends(get_async_session),
):
    settings = await SkillLoopSettingsRepository(session).upsert(
        auth.tenant_id,
        enabled=payload.enabled,
        digest_enabled=payload.digest_enabled,
        digest_hour=payload.digest_hour,
    )

    workspace = await SlackWorkspaceRepository(session).get_by_tenant(auth.tenant_id)
    message = "Skill loop settings updated"

    if "slack_reviewers_channel_id" in payload.model_fields_set:
        channel = (payload.slack_reviewers_channel_id or "").strip() or None
        if workspace is None:
            message = "Settings saved, but no Slack workspace is connected to store the reviewers channel."
        else:
            workspace = await SlackWorkspaceRepository(session).update(workspace.id, reviewers_channel_id=channel)

    return success_response(data=_to_data(settings, workspace), message=message)


@router.post("/skill-loop/run-now")
async def run_skill_loop_now(
    payload: SkillLoopRunNowRequest | None = None,
    auth: AuthContext = Depends(require_scope(Scope.TENANT_MANAGE_ROLES)),
    session: AsyncSession = Depends(get_async_session),
):
    """Manually trigger the learning loop: one notebook synchronously, or a tenant-wide sweep in the background."""
    notebook_id = payload.notebook_id if payload else None
    if notebook_id is not None:
        result = await session.execute(
            select(Notebook).where(Notebook.id == notebook_id, Notebook.tenant_id == auth.tenant_id)
        )
        if result.scalars().first() is None:
            raise HTTPException(status_code=404, detail="Notebook not found")
        verdict = await skill_loop_service.evaluate_now(notebook_id, auth.tenant_id, trigger="manual")
        return success_response(data={"verdict": verdict}, message=f"Evaluation completed: {verdict}")

    sweep = await skill_loop_service.run_tenant_sweep(auth.tenant_id)
    queued = sweep.get("queued", 0)
    message = sweep.get("note") or f"Queued {queued} conversation(s) for evaluation"
    return success_response(data=sweep, message=message)


@router.get("/skill-loop/slack-channels")
async def list_skill_loop_slack_channels(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """List the tenant's Slack channels for the reviewers-channel picker."""
    workspace = await SlackWorkspaceRepository(session).get_by_tenant(auth.tenant_id)
    if workspace is None or not workspace.is_active:
        return success_response(
            data={"connected": False, "channels": []},
            message="No Slack workspace connected",
        )

    try:
        bot_token = await SlackAgentService._get_bot_token(workspace, session)
        channels = await SlackService(bot_token).list_channels(limit=200)
        return success_response(
            data={"connected": True, "channels": channels},
            message=f"Retrieved {len(channels)} Slack channels",
        )
    except Exception as e:
        logger.warning(f"Failed to list Slack channels for tenant {auth.tenant_id}: {e}")
        return success_response(
            data={"connected": True, "channels": []},
            message="Slack workspace connected, but channels could not be loaded",
        )
