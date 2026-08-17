from __future__ import annotations

from enum import Enum

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext
from server.auth.scopes import Scope
from server.models.notebooks import Notebook


class NotebookAction(str, Enum):
    EXPORT = "export"
    SHARE_MANAGE = "share_manage"


NOTEBOOK_ACTION_SCOPES: dict[NotebookAction, Scope] = {
    NotebookAction.EXPORT: Scope.DASHBOARD_EXPORT,
    NotebookAction.SHARE_MANAGE: Scope.DASHBOARD_SHARE,
}


async def authorize_notebook_action(
    *,
    session: AsyncSession,
    auth: AuthContext,
    notebook_id: str,
    action: NotebookAction,
) -> Notebook:
    required_scope = NOTEBOOK_ACTION_SCOPES[action]
    if not auth.has_scope(required_scope):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission denied. Required scope: {required_scope.value}",
        )

    notebook = await session.scalar(
        select(Notebook).where(
            Notebook.id == notebook_id,
            Notebook.tenant_id == auth.tenant_id,
        )
    )
    if notebook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

    if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only share or export notebooks you created",
        )

    return notebook
