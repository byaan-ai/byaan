from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.constants.models import resolve_model
from server.db.session import get_async_session
from server.schemas.standard_response import success_response
from server.services.posthog_service import ANALYTICS_OPT_OUT_KEY, PostHogService
from server.services.settings import SettingsService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Settings keys for preferred model
PREFERRED_MODEL_PROVIDER_KEY = "preferred_model_provider"
PREFERRED_MODEL_KEY = "preferred_model"


class PreferredModelResponse(BaseModel):
    provider: str | None
    model: str | None


class PreferredModelRequest(BaseModel):
    provider: str
    model: str


@router.get("/settings/preferred-model")
async def get_preferred_model(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get the user's preferred model for new notebooks."""
    try:
        user_id = auth.user_id
        provider_setting = await SettingsService.get_setting_by_key_for_user(
            session, PREFERRED_MODEL_PROVIDER_KEY, user_id
        )
        model_setting = await SettingsService.get_setting_by_key_for_user(session, PREFERRED_MODEL_KEY, user_id)

        response = PreferredModelResponse(
            provider=provider_setting.setting_value if provider_setting else None,
            model=resolve_model(
                model_setting.setting_value if model_setting else None,
                provider_setting.setting_value if provider_setting else None,
            ),
        )

        return success_response(
            data=response.model_dump(),
            message="Preferred model retrieved successfully",
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in get_preferred_model: {str(e)}",
            posthog_context={"function": "get_preferred_model"},
        )
        # Return empty preference on error instead of failing
        return success_response(
            data=PreferredModelResponse(provider=None, model=None).model_dump(),
            message="No preferred model set",
        )


@router.put("/settings/preferred-model", status_code=status.HTTP_200_OK)
async def set_preferred_model(
    payload: PreferredModelRequest,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Set the user's preferred model for new notebooks."""
    try:
        user_id = auth.user_id
        await SettingsService.upsert_setting_for_user(
            session,
            setting_key=PREFERRED_MODEL_PROVIDER_KEY,
            setting_value=payload.provider,
            user_id=user_id,
            description="User's preferred LLM provider for new notebooks",
        )

        await SettingsService.upsert_setting_for_user(
            session,
            setting_key=PREFERRED_MODEL_KEY,
            setting_value=payload.model,
            user_id=user_id,
            description="User's preferred model for new notebooks",
        )

        return success_response(
            data={"provider": payload.provider, "model": payload.model},
            message=f"Preferred model set to {payload.model}",
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in set_preferred_model: {str(e)}",
            posthog_context={"function": "set_preferred_model", "provider": payload.provider, "model": payload.model},
        )
        raise


class AnalyticsOptOutResponse(BaseModel):
    opt_out: bool


class AnalyticsOptOutRequest(BaseModel):
    opt_out: bool


@router.get("/settings/analytics-opt-out")
async def get_analytics_opt_out(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get the user's analytics opt-out preference."""
    user_id = auth.user_id
    setting = await SettingsService.get_setting_by_key_for_user(session, ANALYTICS_OPT_OUT_KEY, user_id)
    opted_out = bool(setting and setting.setting_value == "true")
    PostHogService.prime_user_opt_out(str(user_id), opted_out)
    return success_response(
        data=AnalyticsOptOutResponse(opt_out=opted_out).model_dump(),
        message="Analytics opt-out preference retrieved",
    )


@router.put("/settings/analytics-opt-out", status_code=status.HTTP_200_OK)
async def set_analytics_opt_out(
    payload: AnalyticsOptOutRequest,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Set the user's analytics opt-out preference."""
    user_id = auth.user_id
    await SettingsService.upsert_setting_for_user(
        session,
        setting_key=ANALYTICS_OPT_OUT_KEY,
        setting_value="true" if payload.opt_out else "false",
        user_id=user_id,
        description="User's analytics opt-out preference (true disables PostHog tracking)",
    )
    PostHogService.prime_user_opt_out(str(user_id), payload.opt_out)
    return success_response(
        data=AnalyticsOptOutResponse(opt_out=payload.opt_out).model_dump(),
        message="Analytics opt-out preference updated",
    )


@router.delete("/settings/preferred-model", status_code=status.HTTP_200_OK)
async def clear_preferred_model(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Clear the user's preferred model."""
    try:
        user_id = auth.user_id
        await SettingsService.delete_setting_by_key_for_user(session, PREFERRED_MODEL_PROVIDER_KEY, user_id)
        await SettingsService.delete_setting_by_key_for_user(session, PREFERRED_MODEL_KEY, user_id)

        return success_response(
            data=None,
            message="Preferred model cleared",
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in clear_preferred_model: {str(e)}",
            posthog_context={"function": "clear_preferred_model"},
        )
        raise
