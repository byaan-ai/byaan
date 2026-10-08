import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.object_authorizer import NotebookAction, authorize_notebook_action
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.schemas.notebook_export import ShareNotebookJsonRequest
from server.schemas.standard_response import success_response
from server.services.export_service import CompiledHtmlExportService
from server.services.notebook_export_service import NotebookExportService
from server.services.settings import SettingsService
from server.services.sharing import SharingService
from server.utils.config_loader import get_waitlist_config
from server.utils.custom_logger import get_logger
from server.utils.deployment import is_feature_enabled
from server.utils.error_sanitizer import sanitize_error_message, sanitize_text

router = APIRouter()
logger = get_logger(__name__)


def _log_worker_error(response: httpx.Response | object) -> None:
    status_code = getattr(response, "status_code", "unknown")
    body = sanitize_text(str(getattr(response, "text", "")))
    logger.error(f"Worker returned error: {status_code} - {body}")


def get_worker_url() -> str:
    """Get worker URL from env config. Callers must ensure worker features are enabled first."""
    config = get_waitlist_config()
    worker_url = config.get("worker_url")
    if not worker_url:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker-backed features are not enabled in this deployment.",
        )
    return worker_url


def check_sharing_available():
    """Raise 403 if external sharing is not available."""
    if not is_feature_enabled("external_sharing_enabled"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External sharing is not available in this deployment mode",
        )


@router.get("/notebooks/{notebook_id}/export/pdf")
async def export_pdf(
    notebook_id: str,
    version: int | None = None,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Export notebook as a PDF file via Cloudflare Worker.

    This generates a PDF from the dashboard HTML using browser rendering.
    """
    check_sharing_available()

    from server.services.pdf_service import PDFService, PDFServiceError

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.EXPORT,
    )

    try:
        from uuid import UUID

        pdf_bytes = await PDFService.generate(session=session, dashboard_id=UUID(notebook_id), version=version)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=notebook_{notebook_id[:8]}.pdf"},
        )

    except PDFServiceError as e:
        logger.error(
            f"Failed to generate PDF: {sanitize_error_message(e)}",
            posthog_context={
                "function": "export_pdf",
                "notebook_id": notebook_id,
                "version": version,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate PDF: {sanitize_error_message(e)}",
        )
    except ValueError as e:
        logger.error(
            f"Invalid notebook ID: {sanitize_error_message(e)}",
            posthog_context={"function": "export_pdf", "notebook_id": notebook_id},
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")
    except Exception as e:
        logger.error(
            f"Error generating PDF: {sanitize_error_message(e)}",
            exc_info=True,
            posthog_context={"function": "export_pdf", "notebook_id": notebook_id, "version": version},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate PDF: {sanitize_error_message(e)}",
        )


@router.get("/notebooks/{notebook_id}/export/compiled-html")
async def export_compiled_html(
    notebook_id: str,
    version: int | None = None,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Export notebook as a compiled HTML file with all data embedded.

    This creates a standalone HTML file that:
    - Contains all query results embedded directly in the HTML
    - Works without a backend API
    - Can be opened in any browser
    """
    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.EXPORT,
    )

    try:
        # Generate compiled HTML with embedded data
        compiled_html = await CompiledHtmlExportService.generate_compiled_html(
            session=session, notebook_id=notebook_id, version=version
        )

        # Return as downloadable HTML file
        return Response(
            content=compiled_html,
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename=notebook_{notebook_id[:8]}_compiled.html"},
        )

    except ValueError as e:
        logger.error(
            f"Failed to generate compiled HTML: {sanitize_error_message(e)}",
            posthog_context={
                "function": "export_compiled_html",
                "notebook_id": notebook_id,
                "version": version,
                "error_type": "validation_error",
            },
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(
            f"Error generating compiled HTML: {sanitize_error_message(e)}",
            posthog_context={"function": "export_compiled_html", "notebook_id": notebook_id, "version": version},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate compiled HTML: {sanitize_error_message(e)}",
        )


@router.post("/notebooks/{notebook_id}/share")
async def share_notebook(
    notebook_id: str,
    version: int | None = None,
    password: str | None = None,
    update_password: bool = False,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Create or update the share link for a notebook.

    Each notebook can have only one share link. The notebook_id is used as the share ID.
    If a share already exists, it will be updated with the new HTML content.

    - password: If provided, sets/updates the password. If None, keeps existing password.
    - update_password: If True with empty password, removes password protection.
    """
    check_sharing_available()
    api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
    if not api_key_setting:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.SHARE_MANAGE,
    )

    try:
        worker_url = get_worker_url()
        compiled_html = await CompiledHtmlExportService.generate_compiled_html(
            session=session, notebook_id=notebook_id, version=version
        )

        # Build request payload using notebook_id as the share ID
        payload = {
            "id": notebook_id,  # Use notebook_id as share ID for single share per notebook
            "html": compiled_html,
        }

        # Password handling:
        # - If password provided: set/update password
        # - If update_password=True with no password: remove password
        # - Otherwise: worker keeps existing password
        if password and password.strip():
            payload["password"] = password.strip()
        elif update_password:
            payload["password"] = ""  # Empty string signals password removal

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{worker_url}/api/html",
                json=payload,
                headers={"Authorization": f"Bearer {api_key_setting.setting_value}"},
                timeout=30.0,
            )

        if response.status_code != 200:
            _log_worker_error(response)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create share link")

        data = response.json()
        await SharingService(session).ensure_notebook_worker_grant(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            notebook_id=notebook_id,
            legacy_surface="html_notebook_share",
            legacy_id=notebook_id,
            has_password=bool(data.get("has_password", bool(password and password.strip()))),
            password=password.strip() if password and password.strip() else None,
            metadata={"version": version, "share_url": f"https://www.byaan.ai/share/{notebook_id}"},
        )
        is_update = not data.get("is_new", True)
        return success_response(
            data={
                "share_id": notebook_id,
                "share_url": f"https://www.byaan.ai/share/{notebook_id}",
                "is_update": is_update,
            },
            message="Share link updated" if is_update else "Share link created",
        )

    except httpx.ConnectError as e:
        logger.error(f"Error sharing notebook: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to sharing service. Please check your network connection.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sharing notebook: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to share notebook: {sanitize_error_message(e)}",
        ) from e


@router.get("/notebooks/{notebook_id}/share")
async def get_notebook_share(
    notebook_id: str,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get the share for a notebook (single share per notebook)."""
    check_sharing_available()
    api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
    if not api_key_setting:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.SHARE_MANAGE,
    )

    try:
        worker_url = get_worker_url()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{worker_url}/api/html/{notebook_id}",
                headers={"Authorization": f"Bearer {api_key_setting.setting_value}"},
                timeout=30.0,
            )

        # 404 means no share exists for this notebook
        if response.status_code == 404:
            return success_response(data={"share": None}, message="No share exists")

        if response.status_code != 200:
            _log_worker_error(response)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get share")

        data = response.json()
        share = {
            "id": notebook_id,
            "share_url": f"https://www.byaan.ai/share/{notebook_id}",
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "has_password": data.get("has_password", False),
        }

        return success_response(data={"share": share}, message="Share retrieved")

    except httpx.ConnectError as e:
        logger.error(f"Error getting share: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to sharing service.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting share: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get share: {sanitize_error_message(e)}",
        ) from e


@router.delete("/notebooks/{notebook_id}/share")
async def delete_share(
    notebook_id: str,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete the share for a notebook (single share per notebook, uses notebook_id as share_id)."""
    check_sharing_available()
    api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
    if not api_key_setting:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.SHARE_MANAGE,
    )

    try:
        worker_url = get_worker_url()
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{worker_url}/api/html/{notebook_id}",
                headers={"Authorization": f"Bearer {api_key_setting.setting_value}"},
                timeout=30.0,
            )

        if response.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

        if response.status_code != 200:
            _log_worker_error(response)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete share")

        await SharingService(session).revoke_legacy_grant(
            tenant_id=auth.tenant_id,
            legacy_surface="html_notebook_share",
            legacy_id=notebook_id,
            actor_id=auth.user_id,
            reason="worker html notebook share deleted",
        )
        return success_response(data=None, message="Share deleted")

    except httpx.ConnectError as e:
        logger.error(f"Error deleting share: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to sharing service.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting share: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete share: {sanitize_error_message(e)}",
        ) from e


# =============================================================================
# Notebook JSON Export/Share Endpoints
# =============================================================================


@router.get("/notebooks/{notebook_id}/export/json")
async def export_notebook_json(
    notebook_id: str,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Export notebook as a JSON file for local download.

    This creates a portable JSON file containing:
    - Notebook metadata (title, description)
    - Chat history (user and assistant messages)
    - All dashboard versions (HTML content)
    - Dataset definitions with their queries (no credentials)

    The exported JSON can be shared manually or imported into another Byaan instance.
    """
    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.EXPORT,
    )

    try:
        export_data = await NotebookExportService.export_notebook(session, notebook_id)

        # Return as downloadable JSON file
        return JSONResponse(
            content=export_data.model_dump(),
            headers={
                "Content-Disposition": f"attachment; filename=notebook_{notebook_id[:8]}_export.json",
                "Content-Type": "application/json",
            },
        )

    except ValueError as e:
        logger.error(
            f"Failed to export notebook JSON: {sanitize_error_message(e)}",
            posthog_context={
                "function": "export_notebook_json",
                "notebook_id": notebook_id,
                "error_type": "validation_error",
            },
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(
            f"Error exporting notebook JSON: {sanitize_error_message(e)}",
            posthog_context={"function": "export_notebook_json", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export notebook: {sanitize_error_message(e)}",
        )


@router.post("/notebooks/{notebook_id}/share/notebook")
async def share_notebook_json(
    notebook_id: str,
    request: ShareNotebookJsonRequest | None = None,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    """
    Share notebook as JSON via Cloudflare D1 and get a shareable URL.

    This uploads the notebook JSON to the sharing service and returns a URL.
    Recipients can use this URL to import the notebook into their Byaan instance.

    The shared notebook includes:
    - Full chat history
    - All dashboard versions
    - Dataset definitions and queries (no credentials)

    Recipients will need to connect their own databases matching the dataset types.
    """
    check_sharing_available()
    api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
    if not api_key_setting:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.SHARE_MANAGE,
    )

    try:
        # Export notebook to JSON format
        export_data = await NotebookExportService.export_notebook(session, notebook_id)

        # Build request payload for worker
        worker_url = get_worker_url()
        payload = {
            "notebook_id": notebook_id,
            "notebook_json": export_data.model_dump(),
        }

        # Add password if provided
        if request and request.password and request.password.strip():
            payload["password"] = request.password

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{worker_url}/api/notebook",
                json=payload,
                headers={"Authorization": f"Bearer {api_key_setting.setting_value}"},
                timeout=60.0,  # Longer timeout for larger payloads
            )

        if response.status_code != 200:
            _log_worker_error(response)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create share link")

        data = response.json()
        await SharingService(session).ensure_notebook_worker_grant(
            tenant_id=auth.tenant_id,
            actor_id=auth.user_id,
            notebook_id=notebook_id,
            legacy_surface="json_notebook_share",
            legacy_id=str(data["id"]),
            has_password=bool(request and request.password and request.password.strip()),
            password=request.password.strip() if request and request.password and request.password.strip() else None,
            metadata={"share_id": str(data["id"])},
        )
        return success_response(
            data={"share_id": data["id"]},
            message="Notebook shared successfully",
        )

    except httpx.ConnectError as e:
        logger.error(f"Error sharing notebook JSON: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to sharing service. Please check your network connection.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sharing notebook JSON: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to share notebook: {sanitize_error_message(e)}",
        ) from e


@router.get("/notebooks/{notebook_id}/shares/notebook")
async def list_notebook_json_shares(
    notebook_id: str,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    """List all JSON shares for a notebook."""
    check_sharing_available()
    api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
    if not api_key_setting:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.SHARE_MANAGE,
    )

    try:
        worker_url = get_worker_url()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{worker_url}/api/notebook/list/{notebook_id}",
                headers={"Authorization": f"Bearer {api_key_setting.setting_value}"},
                timeout=30.0,
            )

        if response.status_code != 200:
            _log_worker_error(response)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to list shares")

        data = response.json()
        # Transform shares list
        shares = [
            {
                "id": share["id"],
                "created_at": share["created_at"],
                "has_password": share.get("has_password", False),
            }
            for share in data.get("shares", [])
        ]

        return success_response(data={"shares": shares}, message="Shares retrieved")

    except httpx.ConnectError as e:
        logger.error(f"Error listing JSON shares: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to sharing service.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing JSON shares: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list shares: {sanitize_error_message(e)}",
        ) from e


@router.put("/notebooks/{notebook_id}/shares/notebook/{share_id}/password")
async def update_notebook_json_share_password(
    notebook_id: str,
    share_id: str,
    password: str | None = None,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Update or remove password for a notebook JSON share."""
    check_sharing_available()
    api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
    if not api_key_setting:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.SHARE_MANAGE,
    )

    try:
        worker_url = get_worker_url()
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{worker_url}/api/notebook/{share_id}",
                json={"password": password or ""},  # Empty string = remove password
                headers={"Authorization": f"Bearer {api_key_setting.setting_value}"},
                timeout=30.0,
            )

        if response.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

        if response.status_code != 200:
            _log_worker_error(response)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update password")

        data = response.json()
        return success_response(
            data={"success": data.get("success"), "has_password": data.get("has_password")},
            message="Password updated" if password else "Password removed",
        )

    except httpx.ConnectError as e:
        logger.error(f"Error updating share password: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to sharing service.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating share password: {sanitize_error_message(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update password: {sanitize_error_message(e)}",
        ) from e


@router.delete("/notebooks/{notebook_id}/shares/notebook/{share_id}")
async def delete_notebook_json_share(
    notebook_id: str,
    share_id: str,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Delete a JSON share."""
    check_sharing_available()
    api_key_setting = await SettingsService.get_setting_by_key(session, "api_key")
    if not api_key_setting:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    await authorize_notebook_action(
        session=session,
        auth=auth,
        notebook_id=notebook_id,
        action=NotebookAction.SHARE_MANAGE,
    )

    try:
        worker_url = get_worker_url()
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{worker_url}/api/notebook/{share_id}",
                headers={"Authorization": f"Bearer {api_key_setting.setting_value}"},
                timeout=30.0,
            )

        if response.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

        if response.status_code != 200:
            _log_worker_error(response)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete share")

        await SharingService(session).revoke_legacy_grant(
            tenant_id=auth.tenant_id,
            legacy_surface="json_notebook_share",
            legacy_id=share_id,
            actor_id=auth.user_id,
            reason="worker json notebook share deleted",
        )
        return success_response(data=None, message="Share deleted")

    except httpx.ConnectError as e:
        logger.error(f"Error deleting JSON share: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to connect to sharing service.",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting JSON share: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete share: {str(e)}"
        ) from e
