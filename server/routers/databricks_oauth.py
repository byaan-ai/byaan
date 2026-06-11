from __future__ import annotations

import html

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.auth.tenant_context import set_tenant_id
from server.db.session import get_async_session
from server.schemas.connections import (
    DatabricksOAuthCancelRequest,
    DatabricksOAuthResultResponse,
    DatabricksOAuthSettingsRequest,
    DatabricksOAuthSettingsResponse,
    DatabricksOAuthStartRequest,
    DatabricksOAuthStartResponse,
    DatabricksOAuthTokens,
    DatabricksWarehousesRequest,
)
from server.schemas.standard_response import success_response
from server.services import databricks_oauth_service
from server.utils.config_loader import is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

router = APIRouter()


_CALLBACK_HTML = """
<!doctype html>
<html><head><title>Databricks Connected</title>
<style>body{font-family:system-ui;background:#0d0d0d;color:#eee;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}</style>
</head><body><div style="text-align:center">
<h2 style="color:#ff7a00">Databricks connected</h2>
<p>You can close this tab and return to Byaan.</p>
<script>
  try { window.close(); } catch (e) {}
  setTimeout(function(){ try { window.close(); } catch (e) {} }, 300);
</script>
</div></body></html>
"""

_ERROR_HTML = """
<!doctype html>
<html><head><title>Databricks Error</title>
<style>body{font-family:system-ui;background:#0d0d0d;color:#eee;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}</style>
</head><body><div style="text-align:center;max-width:560px">
<h2 style="color:#f87171">Databricks sign-in failed</h2>
<p style="color:#aaa">{message}</p>
</div></body></html>
"""


@router.post("/connections/databricks/oauth/start")
async def databricks_oauth_start(
    body: DatabricksOAuthStartRequest,
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    if is_self_hosted() and not await databricks_oauth_service.is_oauth_configured(session):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Databricks OAuth is not configured. Ask an admin to register a custom OAuth app in the Databricks Account Console and paste the client_id and client_secret in Settings.",
        )

    client_id, _client_secret = await databricks_oauth_service.get_oauth_credentials(session)
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Databricks OAuth client_id missing.",
        )

    auth_url, state = await databricks_oauth_service.create_auth_url(
        server_hostname=body.server_hostname,
        client_id=client_id,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
    )
    return success_response(
        data=DatabricksOAuthStartResponse(
            auth_url=auth_url,
            state=state,
            redirect_uri=databricks_oauth_service.get_redirect_uri(),
        ).model_dump(),
        message="Databricks OAuth URL generated",
    )


@router.get("/connections/databricks/oauth/callback")
async def databricks_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(get_async_session),
):
    stored = databricks_oauth_service.peek_state(state)
    if stored and stored.get("tenant_id"):
        try:
            set_tenant_id(stored["tenant_id"])
        except Exception:
            logger.debug("Failed to set tenant context from OAuth state", exc_info=True)

    try:
        client_id, client_secret = await databricks_oauth_service.get_oauth_credentials(session)
        if not client_id:
            raise ValueError("Databricks OAuth client_id missing")

        tokens = await databricks_oauth_service.exchange_code(
            code=code,
            state=state,
            client_id=client_id,
            client_secret=client_secret,
        )
        databricks_oauth_service.store_result(state, tokens, stored)
    except Exception as e:
        logger.error(f"[DATABRICKS OAUTH] Callback failed: {e}", exc_info=True)
        return HTMLResponse(_ERROR_HTML.replace("{message}", html.escape(str(e))), status_code=400)

    return HTMLResponse(_CALLBACK_HTML)


@router.get("/connections/databricks/oauth/result")
async def databricks_oauth_result(
    state: str = Query(...),
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
):
    entry = databricks_oauth_service.pop_result(state)
    if not entry:
        return success_response(
            data=DatabricksOAuthResultResponse(status="pending").model_dump(),
        )
    tokens = entry["tokens"]
    return success_response(
        data=DatabricksOAuthResultResponse(
            status="success",
            tokens=DatabricksOAuthTokens(
                access_token=tokens["access_token"],
                refresh_token=tokens.get("refresh_token"),
                expires_at=tokens["expires_at"],
                scope=tokens.get("scope"),
                server_hostname=tokens["server_hostname"],
            ),
        ).model_dump(),
        message="Databricks tokens retrieved",
    )


@router.post("/connections/databricks/oauth/cancel")
async def databricks_oauth_cancel(
    body: DatabricksOAuthCancelRequest,
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
):
    cancelled = databricks_oauth_service.cancel_flow(body.state)
    return success_response(
        data={"cancelled": cancelled},
        message="Databricks OAuth flow cancelled" if cancelled else "No active flow found",
    )


@router.post("/connections/databricks/oauth/warehouses")
async def databricks_list_warehouses(
    body: DatabricksWarehousesRequest,
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
):
    try:
        warehouses = await databricks_oauth_service.list_warehouses(body.server_hostname, body.access_token)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return success_response(data={"warehouses": warehouses}, message=f"Found {len(warehouses)} warehouse(s)")


@router.get("/connections/databricks/auth/status")
async def databricks_auth_status(
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    configured = await databricks_oauth_service.is_oauth_configured(session)
    can_configure = is_self_hosted() and bool(getattr(auth, "is_admin", False))
    return success_response(
        data={
            "configured": configured,
            "can_configure": can_configure,
            "redirect_uri": databricks_oauth_service.get_redirect_uri(),
        }
    )


# ----- admin config endpoints (hosted mode only) -----


def _require_hosted_admin(auth: AuthContext) -> None:
    if not is_self_hosted():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Databricks OAuth admin configuration is only available in hosted deployments.",
        )
    if not getattr(auth, "is_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


@router.get("/connections/databricks/admin/oauth-config")
async def get_databricks_oauth_config(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_hosted_admin(auth)

    from server.services.settings import SettingsService

    client_id_setting = await SettingsService.get_setting_by_key(
        session, databricks_oauth_service.DATABRICKS_OAUTH_CLIENT_ID_KEY
    )
    secret_setting = await SettingsService.get_setting_by_key(
        session, databricks_oauth_service.DATABRICKS_OAUTH_CLIENT_SECRET_KEY
    )
    return success_response(
        data=DatabricksOAuthSettingsResponse(
            client_id=client_id_setting.setting_value if client_id_setting else "",
            client_secret_configured=secret_setting is not None,
            redirect_uri=databricks_oauth_service.get_redirect_uri(),
        ).model_dump()
    )


@router.put("/connections/databricks/admin/oauth-config")
async def save_databricks_oauth_config(
    body: DatabricksOAuthSettingsRequest,
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_hosted_admin(auth)

    from server.services.crypto_service import CryptoService
    from server.services.settings import SettingsService

    await SettingsService.upsert_setting(
        session,
        setting_key=databricks_oauth_service.DATABRICKS_OAUTH_CLIENT_ID_KEY,
        setting_value=body.client_id,
    )
    encrypted_secret = await CryptoService.encrypt_config({"value": body.client_secret}, session)
    await SettingsService.upsert_setting(
        session,
        setting_key=databricks_oauth_service.DATABRICKS_OAUTH_CLIENT_SECRET_KEY,
        setting_value=encrypted_secret,
        is_encrypted=True,
    )
    return success_response(message="Databricks OAuth configuration saved")


@router.delete("/connections/databricks/admin/oauth-config")
async def delete_databricks_oauth_config(
    auth: AuthContext = Depends(require_scope(Scope.SETTINGS_UPDATE)),
    session: AsyncSession = Depends(get_async_session),
):
    _require_hosted_admin(auth)

    from server.services.settings import SettingsService

    await SettingsService.delete_setting_by_key(session, databricks_oauth_service.DATABRICKS_OAUTH_CLIENT_ID_KEY)
    await SettingsService.delete_setting_by_key(session, databricks_oauth_service.DATABRICKS_OAUTH_CLIENT_SECRET_KEY)
    return success_response(message="Databricks OAuth configuration removed")
