from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.repositories.github_repository import GitHubRepoRepository
from server.schemas.github import (
    AnalysisRequest,
    AnalysisStatusResponse,
    GitHubAuthConfigResponse,
    GitHubDeviceFlowPollRequest,
    GitHubDeviceFlowPollResponse,
    GitHubDeviceFlowStartResponse,
    GitHubOAuthCallbackRequest,
    GitHubOAuthSettingsRequest,
    GitHubOAuthSettingsResponse,
    GitHubOAuthStartResponse,
    GitHubOAuthStatusResponse,
    GitHubPATRequest,
    GitHubRepoConnect,
    GitHubRepoResponse,
)
from server.schemas.standard_response import error_response, success_response
from server.services import github_service
from server.utils.config_loader import get_email_config, is_self_hosted
from server.utils.custom_logger import get_logger
from server.utils.deployment import is_feature_enabled

logger = get_logger(__name__)

router = APIRouter()

active_analysis_tasks: dict[str, asyncio.Task] = {}


def _check_oauth_scopes(token_data: dict) -> list[str]:
    granted = {s.strip() for s in token_data.get("scope", "").split(",") if s.strip()}
    return sorted(github_service.REQUIRED_SCOPES - granted)


# --- OAuth Endpoints ---


@router.post("/github/oauth/start")
async def oauth_start(
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        client_id, _ = await github_service.get_github_oauth_credentials(session)
        auth_url, state = await github_service.create_auth_url(
            tenant_id=auth.tenant_id, user_id=auth.user_id, client_id=client_id
        )
        return success_response(
            data=GitHubOAuthStartResponse(auth_url=auth_url, state=state).model_dump(),
            message="OAuth URL generated",
        )
    except Exception as e:
        logger.error(f"[GITHUB] OAuth start failed: {e}")
        return error_response(message="Failed to start OAuth flow")


@router.get("/github/oauth/callback")
async def oauth_callback_get(
    code: str,
    state: str,
    session: AsyncSession = Depends(get_async_session),
):
    frontend_url = github_service._frontend_url or get_email_config().get("frontend_url", "")
    try:
        stored = github_service._oauth_state_store.get(state)
        if stored and stored.get("tenant_id"):
            set_tenant_id(stored["tenant_id"])
        client_id, client_secret = await github_service.get_github_oauth_credentials(session)
        token_data, stored = await github_service.exchange_code(
            code, state, client_id=client_id, client_secret=client_secret
        )
        missing = _check_oauth_scopes(token_data)
        if missing:
            raise ValueError(
                f"GitHub token is missing required scope(s): {', '.join(missing)}. "
                "Please reconnect and grant all requested permissions."
            )
        tenant_id = stored.get("tenant_id")
        user_id = stored.get("user_id")
        if not tenant_id or not user_id:
            raise ValueError("Missing auth context. Please restart the OAuth flow.")
        await github_service.save_github_token(tenant_id, user_id, token_data, session)
        return RedirectResponse(url=f"{frontend_url}/github?github_connected=true")
    except Exception as e:
        logger.error(f"[GITHUB] OAuth callback failed: {e}")
        return RedirectResponse(url=f"{frontend_url}/github?github_error={str(e)}")


@router.post("/github/oauth/callback")
async def oauth_callback(
    body: GitHubOAuthCallbackRequest,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        client_id, client_secret = await github_service.get_github_oauth_credentials(session)
        token_data, _ = await github_service.exchange_code(
            body.code, body.state, client_id=client_id, client_secret=client_secret
        )
        missing = _check_oauth_scopes(token_data)
        if missing:
            raise ValueError(
                f"GitHub token is missing required scope(s): {', '.join(missing)}. "
                "Please reconnect and grant all requested permissions."
            )
        await github_service.save_github_token(auth.tenant_id, auth.user_id, token_data, session)
        user_info = await github_service.get_authenticated_user(token_data["access_token"])
        return success_response(
            data={"connected": True, "username": user_info["login"]},
            message="GitHub connected successfully",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"[GITHUB] OAuth callback failed: {e}")
        return error_response(message="Failed to complete OAuth flow")


@router.get("/github/oauth/status")
async def oauth_status(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    token = await github_service.get_github_token(auth.tenant_id, auth.user_id, session)
    if not token:
        return success_response(data=GitHubOAuthStatusResponse(connected=False).model_dump())

    try:
        user_info = await github_service.get_authenticated_user(token)
        auth_method = await github_service.get_stored_auth_method(auth.tenant_id, auth.user_id, session)
        scopes = (
            None
            if auth_method == github_service.AUTH_METHOD_PAT_FINE_GRAINED
            else github_service.GITHUB_SCOPES.split(" ")
        )
        return success_response(
            data=GitHubOAuthStatusResponse(
                connected=True,
                username=user_info["login"],
                scopes=scopes,
                auth_method=auth_method,
            ).model_dump()
        )
    except Exception:
        return success_response(data=GitHubOAuthStatusResponse(connected=False).model_dump())


@router.post("/github/oauth/disconnect")
async def oauth_disconnect(
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    await github_service.delete_github_token(auth.tenant_id, auth.user_id, session)
    return success_response(message="GitHub disconnected")


# --- Device Flow Endpoints ---


@router.post("/github/oauth/device/start")
async def device_flow_start(
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        client_id, _ = await github_service.get_github_oauth_credentials(session)
        if not client_id:
            return error_response(message="GitHub OAuth is not configured")
        data = await github_service.initiate_device_flow(client_id, auth.tenant_id, auth.user_id)
        return success_response(
            data=GitHubDeviceFlowStartResponse(
                device_code=data["device_code"],
                user_code=data["user_code"],
                verification_uri=data.get("verification_uri", "https://github.com/login/device"),
                expires_in=data.get("expires_in", 900),
                interval=data.get("interval", 5),
            ).model_dump(),
            message="Device flow initiated",
        )
    except Exception as e:
        logger.error(f"[GITHUB] Device flow start failed: {e}")
        return error_response(message=str(e))


@router.post("/github/oauth/device/poll")
async def device_flow_poll(
    body: GitHubDeviceFlowPollRequest,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        client_id, _ = await github_service.get_github_oauth_credentials(session)
        result = await github_service.poll_device_token(body.device_code, client_id)

        if result["status"] == "success":
            token_data = result["token_data"]
            missing = _check_oauth_scopes(token_data)
            if missing:
                raise ValueError(
                    f"GitHub token is missing required scope(s): {', '.join(missing)}. "
                    "Please reconnect and grant all requested permissions."
                )
            context = result.get("context", {})
            tenant_id = context.get("tenant_id") or auth.tenant_id
            user_id = context.get("user_id") or auth.user_id
            await github_service.save_github_token(tenant_id, user_id, token_data, session)
            user_info = await github_service.get_authenticated_user(token_data["access_token"])
            return success_response(
                data=GitHubDeviceFlowPollResponse(
                    status="success", connected=True, username=user_info["login"]
                ).model_dump(),
                message="GitHub connected successfully",
            )

        return success_response(data=GitHubDeviceFlowPollResponse(status=result["status"]).model_dump())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"[GITHUB] Device flow poll failed: {e}")
        return error_response(message="Failed to poll device flow status")


# --- Auth Config & PAT Endpoints ---


@router.get("/github/auth/config")
async def get_auth_config(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    oauth_available = await github_service.is_oauth_configured(session)
    can_configure_oauth = is_self_hosted() and auth.is_admin
    return success_response(
        data=GitHubAuthConfigResponse(
            oauth_available=oauth_available,
            can_configure_oauth=can_configure_oauth,
        ).model_dump()
    )


@router.post("/github/auth/pat")
async def connect_with_pat(
    body: GitHubPATRequest,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        user_info = await github_service.validate_and_save_pat(body.token, auth.tenant_id, auth.user_id, session)
        return success_response(
            data={"connected": True, "username": user_info["login"]},
            message="GitHub connected via Personal Access Token",
        )
    except ValueError as e:
        return error_response(message=str(e))
    except httpx.HTTPStatusError:
        return error_response(message="Invalid GitHub token. Please check your PAT and try again.")
    except Exception as e:
        logger.error(f"[GITHUB] PAT connection failed: {e}")
        return error_response(message="Failed to connect with Personal Access Token")


# --- Admin OAuth Config Endpoints ---


@router.get("/github/admin/oauth-config")
async def get_oauth_config(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    from server.services.settings import SettingsService

    client_id_setting = await SettingsService.get_setting_by_key(session, github_service.GITHUB_OAUTH_CLIENT_ID_KEY)
    secret_setting = await SettingsService.get_setting_by_key(session, github_service.GITHUB_OAUTH_CLIENT_SECRET_KEY)
    return success_response(
        data=GitHubOAuthSettingsResponse(
            client_id=client_id_setting.setting_value if client_id_setting else "",
            client_secret_configured=secret_setting is not None,
        ).model_dump()
    )


@router.put("/github/admin/oauth-config")
async def save_oauth_config(
    body: GitHubOAuthSettingsRequest,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    from server.services.crypto_service import CryptoService
    from server.services.settings import SettingsService

    await SettingsService.upsert_setting(
        session,
        setting_key=github_service.GITHUB_OAUTH_CLIENT_ID_KEY,
        setting_value=body.client_id,
    )
    encrypted_secret = await CryptoService.encrypt_config({"value": body.client_secret}, session)
    await SettingsService.upsert_setting(
        session,
        setting_key=github_service.GITHUB_OAUTH_CLIENT_SECRET_KEY,
        setting_value=encrypted_secret,
        is_encrypted=True,
    )
    return success_response(message="GitHub OAuth configuration saved")


@router.delete("/github/admin/oauth-config")
async def delete_oauth_config(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    from server.services.settings import SettingsService

    await SettingsService.delete_setting_by_key(session, github_service.GITHUB_OAUTH_CLIENT_ID_KEY)
    await SettingsService.delete_setting_by_key(session, github_service.GITHUB_OAUTH_CLIENT_SECRET_KEY)
    return success_response(message="GitHub OAuth configuration removed")


# --- Repository Endpoints ---


@router.get("/github/repos")
async def list_github_repos(
    page: int = 1,
    search: str | None = None,
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    token = await github_service.get_github_token(auth.tenant_id, auth.user_id, session)
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not connected")

    try:
        repos = await github_service.list_user_repos(token, page=page, search=search)
        return success_response(data=repos)
    except httpx.HTTPStatusError as e:
        logger.error(f"[GITHUB] List repos failed: {e}")
        if e.response.status_code == 403:
            return error_response(
                message="GitHub token lacks permission to list repositories. Please reconnect with the 'repo' scope."
            )
        return error_response(message="Failed to list repositories")
    except Exception as e:
        logger.error(f"[GITHUB] List repos failed: {e}")
        return error_response(message="Failed to list repositories")


@router.post("/github/repos/connect")
async def connect_repo(
    body: GitHubRepoConnect,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo_repo = GitHubRepoRepository(session)
    existing = await repo_repo.get_by_repo_name(auth.tenant_id, body.repo_full_name)
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Repository already connected")

    repo = await repo_repo.create(auth.tenant_id, auth.user_id, body.repo_full_name, body.default_branch)
    return success_response(
        data=GitHubRepoResponse.model_validate(repo).model_dump(mode="json"),
        message="Repository connected",
    )


@router.get("/github/repos/connected")
async def list_connected_repos(
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo_repo = GitHubRepoRepository(session)
    repos = await repo_repo.list_by_user_and_source(auth.tenant_id, auth.user_id, "github")
    return success_response(data=[GitHubRepoResponse.model_validate(r).model_dump(mode="json") for r in repos])


@router.get("/github/repos/{repo_id}")
async def get_repo(
    repo_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo_repo = GitHubRepoRepository(session)
    repo = await repo_repo.get(repo_id, auth.tenant_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return success_response(data=GitHubRepoResponse.model_validate(repo).model_dump(mode="json"))


@router.delete("/github/repos/{repo_id}")
async def delete_repo(
    repo_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo_repo = GitHubRepoRepository(session)
    existing = await repo_repo.get(repo_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    if existing.user_id != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who connected this repository can disconnect it",
        )

    was_shared = existing.scope == "org"
    deleted = await repo_repo.delete(repo_id, auth.tenant_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    if was_shared:
        remaining = await repo_repo.list_org_accessible(auth.tenant_id, "github")
        if not remaining:
            await github_service.unshare_org_github_token(auth.tenant_id, session)

    return success_response(message="Repository disconnected")


@router.post("/github/repos/{repo_id}/share")
async def share_repo_with_team(
    repo_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Promote a connected repository to org scope so workspace-scoped callers (Slack) can use it."""
    if not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team sharing is not available in this deployment mode",
        )
    repo_repo = GitHubRepoRepository(session)
    existing = await repo_repo.get(repo_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    if existing.user_id != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who connected this repository can share it",
        )

    repo = await repo_repo.set_scope(repo_id, auth.tenant_id, auth.user_id, "org")
    if not repo:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to share repository")

    token_shared = await github_service.share_github_token_with_org(auth.tenant_id, auth.user_id, session)
    if not token_shared:
        await repo_repo.set_scope(repo_id, auth.tenant_id, auth.user_id, "user")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No GitHub credentials available to share. Reconnect your GitHub account and try again.",
        )

    return success_response(
        data=GitHubRepoResponse.model_validate(repo).model_dump(mode="json"),
        message="Repository shared with team",
    )


@router.post("/github/repos/{repo_id}/unshare")
async def unshare_repo_from_team(
    repo_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Revoke org scope. Removes the shared GitHub token if no other org-scoped repo remains."""
    if not is_feature_enabled("team_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Team sharing is not available in this deployment mode",
        )
    repo_repo = GitHubRepoRepository(session)
    existing = await repo_repo.get(repo_id, auth.tenant_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    if existing.user_id != auth.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who connected this repository can unshare it",
        )

    repo = await repo_repo.set_scope(repo_id, auth.tenant_id, auth.user_id, "user")
    if not repo:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unshare repository")

    remaining = await repo_repo.list_org_accessible(auth.tenant_id, "github")
    if not remaining:
        await github_service.unshare_org_github_token(auth.tenant_id, session)

    return success_response(
        data=GitHubRepoResponse.model_validate(repo).model_dump(mode="json"),
        message="Repository is now personal",
    )


# --- Analysis Endpoints ---


@router.post("/github/repos/{repo_id}/analyze")
async def analyze_repo(
    repo_id: UUID,
    body: AnalysisRequest,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo_repo = GitHubRepoRepository(session)
    repo = await repo_repo.get(repo_id, auth.tenant_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    token = await github_service.get_github_token(auth.tenant_id, auth.user_id, session)
    if not token and repo.scope == "org":
        token = await github_service.get_org_github_token(auth.tenant_id, session)
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub not connected")

    from server.services.repo_analysis_service import analyze_repository

    repo_id_str = str(repo_id)
    existing = active_analysis_tasks.get(repo_id_str)
    if existing and not existing.done():
        existing.cancel()

    task = asyncio.create_task(
        analyze_repository(
            repo_id=repo_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            llm_connection_id=body.llm_connection_id,
            github_token=token,
            repo_full_name=repo.repo_full_name,
            default_branch=repo.default_branch,
        )
    )
    active_analysis_tasks[repo_id_str] = task
    task.add_done_callback(lambda _: active_analysis_tasks.pop(repo_id_str, None))

    return success_response(data={"status": "analyzing"}, message="Analysis started")


@router.post("/github/repos/{repo_id}/analyze/cancel")
async def cancel_analysis(
    repo_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    repo_id_str = str(repo_id)
    task = active_analysis_tasks.pop(repo_id_str, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    repo_repo = GitHubRepoRepository(session)
    await repo_repo.update_analysis_status(repo_id, "cancelled")
    return success_response(message="Analysis cancelled")


@router.get("/github/repos/{repo_id}/status")
async def get_analysis_status(
    repo_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.USER_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    repo_repo = GitHubRepoRepository(session)
    repo = await repo_repo.get(repo_id, auth.tenant_id)
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    from server.services.repo_analysis_service import get_analysis_progress

    progress = get_analysis_progress(str(repo_id))
    return success_response(
        data=AnalysisStatusResponse(
            status=repo.analysis_status,
            error=repo.analysis_error,
            progress_message=progress.message if progress else None,
            files_analyzed=progress.files_analyzed if progress else None,
            total_files=progress.total_files if progress else None,
        ).model_dump()
    )
