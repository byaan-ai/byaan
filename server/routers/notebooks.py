from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_any_scope, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.models.connections import ALLOWED_CONN_TYPES
from server.models.connections import Connection as ConnectionModel
from server.repositories.connections import ConnectionRepository
from server.repositories.dashboard import DashboardRepository
from server.repositories.messages import MessageRepository
from server.schemas.connections import ConnectionRead
from server.schemas.messages import MessageRead
from server.schemas.notebook_datasets import (
    DatasetAssociateRequest,
    DatasetConnectRequest,
    DatasetConnectResponse,
    NotebookDatasetRead,
)
from server.schemas.notebooks import NotebookCreate, NotebookListResponse, NotebookRead, NotebookUpdate
from server.schemas.query import QueryListItem, QueryListResponse
from server.schemas.standard_response import success_response
from server.services.agent_session_factory import create_agent_session
from server.services.connections import ConnectionService
from server.services.dataset import DatasetService
from server.services.filter_config_service import normalize_filters_for_client
from server.services.folder_service import FolderService
from server.services.notebook import NotebookService
from server.services.viewer_session_service import VIEWER_SESSION_MINUTES, ViewerSessionService
from server.utils.connection_redactor import redact_connection_obj
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _set_viewer_cookie_if_possible(response: Response, request: Request | None, auth: AuthContext) -> None:
    from server.utils.deployment import should_use_secure_cookie

    if request is None:
        return
    if not getattr(auth, "user_id", None) or not getattr(auth, "tenant_id", None):
        return

    viewer_token = ViewerSessionService.generate_token(user_id=auth.user_id, tenant_id=auth.tenant_id)
    response.set_cookie(
        key="viewer_session",
        value=viewer_token,
        httponly=True,
        secure=should_use_secure_cookie(request),
        samesite="lax",
        max_age=VIEWER_SESSION_MINUTES * 60,
        path="/api/viewer",
    )


async def _slack_notebook_titles(session: AsyncSession, notebook_ids: list) -> dict:
    """Map notebook_ids that have a Slack conversation to their thread title."""
    if not notebook_ids:
        return {}
    from server.models.slack_conversation import SlackConversation

    result = await session.execute(
        select(SlackConversation.notebook_id, SlackConversation.thread_title).where(
            SlackConversation.notebook_id.in_(notebook_ids)
        )
    )
    titles: dict = {}
    for notebook_id, thread_title in result.all():
        if notebook_id is None:
            continue
        if notebook_id not in titles or (thread_title and not titles[notebook_id]):
            titles[notebook_id] = thread_title
    return titles


@router.post("/notebooks", status_code=status.HTTP_201_CREATED)
async def create_notebook_endpoint(
    payload: NotebookCreate,
    auth: AuthContext = Depends(require_scope(Scope.NOTEBOOK_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.create_notebook(session, payload, user_id=auth.user_id)
        return success_response(
            data=NotebookRead.model_validate(notebook).model_dump(),
            message=f"Notebook '{payload.notebook_name}' created successfully",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in create_notebook_endpoint: {str(e)}",
            posthog_context={"function": "create_notebook_endpoint", "notebook_name": payload.notebook_name},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while creating the notebook",
        )


@router.get("/notebooks")
async def list_notebooks_endpoint(
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        # Filter notebooks by tenant
        notebooks = await NotebookService.list_notebooks(session)

        # If user only has READ_OWN scope, filter to their own notebooks
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            notebooks = [n for n in notebooks if n.created_by is not None and str(n.created_by) == str(auth.user_id)]

        slack_notebook_titles = await _slack_notebook_titles(session, [n.id for n in notebooks])

        items = []
        for n in notebooks:
            item = NotebookRead.model_validate(n)
            if n.id in slack_notebook_titles:
                item.source = "slack"
                item.slack_thread_title = slack_notebook_titles[n.id]
            items.append(item)

        response = NotebookListResponse(items=items, total=len(notebooks))
        return success_response(data=response.model_dump(), message=f"Retrieved {len(notebooks)} notebook(s)")
    except Exception as e:
        logger.error(
            f"Unexpected error in list_notebooks_endpoint: {str(e)}",
            posthog_context={"function": "list_notebooks_endpoint"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while listing notebooks",
        )


@router.get("/notebooks/{notebook_id}/messages")
async def get_notebook_messages(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has READ_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access notebooks you created",
                )

        message_repo = MessageRepository(session)

        # Only get messages from thread with same ID as notebook
        messages = await message_repo.list(filters={"thread_id": notebook_id})

        messages_list = [MessageRead.model_validate(msg) for msg in messages]
        return success_response(
            data=[m.model_dump() for m in messages_list],
            message=f"Retrieved {len(messages_list)} message(s) for notebook",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_notebook_messages: {str(e)}",
            posthog_context={"function": "get_notebook_messages", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving notebook messages",
        )


@router.delete("/notebooks/{notebook_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_notebook_conversation(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_UPDATE, Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has UPDATE_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_UPDATE):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only update notebooks you created",
                )

        message_repo = MessageRepository(session)
        deleted = await message_repo.delete_by_thread_id(notebook_id)

        agent_session = await create_agent_session(notebook_id)
        await agent_session.clear_session()

        return success_response(
            data=None, message="Conversation cleared successfully" if deleted else "No messages to clear"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in clear_notebook_conversation: {str(e)}",
            posthog_context={"function": "clear_notebook_conversation", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while clearing notebook conversation",
        )


@router.post("/notebooks/{notebook_id}/connections", status_code=status.HTTP_201_CREATED)
async def connect_notebook_endpoint(
    notebook_id: str,
    payload: DatasetConnectRequest,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        tenant_id = auth.tenant_id

        # Determine if single or multiple connections
        is_multiple = payload.connection_ids is not None or payload.connections is not None

        if is_multiple:
            # Handle multiple connections
            connection_ids_to_process = []
            connections_to_create = []

            # Collect existing connection IDs
            if payload.connection_ids:
                connection_ids_to_process.extend(payload.connection_ids)

            # Collect new connections to create
            if payload.connections:
                connections_to_create.extend(payload.connections)

            if not connection_ids_to_process and not connections_to_create:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Either connection_ids or connections are required",
                )

            created_connections = []
            created_datasets = []
            errors = []

            # Process new connections
            for idx, conn_create in enumerate(connections_to_create):
                try:
                    if conn_create.type not in ALLOWED_CONN_TYPES:
                        errors.append(f"Connection {idx + 1}: Invalid type '{conn_create.type}'")
                        continue

                    connection_instance = ConnectionModel(type=conn_create.type, tenant_id=tenant_id)
                    await connection_instance.set_encrypted_connection_obj(conn_create.connection_obj, session)
                    session.add(connection_instance)
                    await session.commit()
                    await session.refresh(connection_instance)

                    try:
                        connection_instance, _ = await ConnectionService.refresh_connection_schema(
                            connection_id=connection_instance.id,
                            session=session,
                        )
                    except ConnectionError as e:
                        errors.append(f"Connection {idx + 1}: {str(e)}")
                    except Exception as e:
                        logger.error(
                            f"Failed to fetch schema for connection {connection_instance.id}: {str(e)}",
                            posthog_context={
                                "function": "connect_notebook_endpoint.fetch_schema",
                                "connection_id": connection_instance.id,
                                "notebook_id": notebook_id,
                            },
                        )

                    connection_ids_to_process.append(connection_instance.id)
                    created_connections.append(ConnectionRead.model_validate(connection_instance))
                except Exception as e:
                    errors.append(f"Connection {idx + 1}: Failed to create - {str(e)}")

            # Process existing connections and create datasets
            conn_repo = ConnectionRepository(session)
            for conn_id in connection_ids_to_process:
                try:
                    existing = await conn_repo.get(conn_id)
                    if existing is None:
                        errors.append(f"Connection {conn_id}: Not found")
                        continue

                    # Use new dataset architecture
                    dataset = await DatasetService.create_dataset(
                        session=session, type="connection", connection_id=conn_id, notebook_id=notebook_id
                    )
                    created_datasets.append(
                        NotebookDatasetRead(
                            id=dataset.id,
                            notebook_id=notebook_id,
                            dataset_id=dataset.id,
                            dataset_type=dataset.type,
                            connection_id=conn_id,
                            created_at=dataset.created_at,
                        )
                    )

                    if existing.id not in [c.id for c in created_connections]:
                        created_connections.append(ConnectionRead.model_validate(existing))
                except Exception as e:
                    errors.append(f"Connection {conn_id}: Failed to associate - {str(e)}")

            if not created_datasets:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "Failed to connect any databases", "errors": errors},
                )

            response = DatasetConnectResponse(
                dataset=created_datasets[0] if created_datasets else None,
                connection=created_connections[0] if created_connections else None,
                datasets=created_datasets,
                connections=created_connections,
            )

            message = f"Successfully connected {len(created_datasets)} database(s)"
            if errors:
                message += f" with {len(errors)} error(s)"

            return success_response(data=response.model_dump(), message=message)

        else:
            # Handle single connection (backward compatibility)
            connection_id: str | None = payload.connection_id

            if connection_id is None:
                if not payload.connection:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Either connection_id or connection details are required",
                    )

                if payload.connection.type not in ALLOWED_CONN_TYPES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid connection type. Allowed types: {', '.join(ALLOWED_CONN_TYPES)}",
                    )

                connection_instance = ConnectionModel(type=payload.connection.type, tenant_id=tenant_id)
                await connection_instance.set_encrypted_connection_obj(payload.connection.connection_obj, session)
                session.add(connection_instance)
                await session.commit()
                await session.refresh(connection_instance)
                connection_id = connection_instance.id

                try:
                    connection_instance, _ = await ConnectionService.refresh_connection_schema(
                        connection_id=connection_instance.id,
                        session=session,
                    )
                except ConnectionError as e:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={
                            "error": "Database Connection Failed",
                            "message": str(e),
                            "type": "connection_error",
                            "connection_saved": True,
                            "connection_id": connection_instance.id,
                        },
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to fetch schema for connection {connection_instance.id}: {str(e)}",
                        posthog_context={
                            "function": "connect_notebook_endpoint.fetch_schema",
                            "connection_id": connection_instance.id,
                            "notebook_id": notebook_id,
                        },
                    )

                connection_read = ConnectionRead.model_validate(connection_instance)
            else:
                conn_repo = ConnectionRepository(session)
                existing = await conn_repo.get(connection_id)
                if existing is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")
                connection_read = ConnectionRead.model_validate(existing)

            # Use new dataset architecture
            dataset = await DatasetService.create_dataset(
                session=session, type="connection", connection_id=connection_id, notebook_id=notebook_id
            )

            response = DatasetConnectResponse(
                dataset=NotebookDatasetRead(
                    id=dataset.id,
                    notebook_id=notebook_id,
                    dataset_id=dataset.id,
                    dataset_type=dataset.type,
                    connection_id=connection_id,
                    created_at=dataset.created_at,
                ),
                connection=connection_read,
            )
            return success_response(
                data=response.model_dump(),
                message=f"Notebook connected to {connection_read.type} database successfully",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in connect_notebook_endpoint: {str(e)}",
            posthog_context={"function": "connect_notebook_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while connecting notebook to database",
        )


@router.get("/notebooks/{notebook_id}/connections")
async def get_notebook_connections_endpoint(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get connection-type datasets for a notebook."""
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has READ_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access notebooks you created",
                )

        datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)
        connection_datasets = [d for d in datasets if d.type == "connection"]

        connections_list = [
            NotebookDatasetRead(
                id=dataset.id,
                notebook_id=notebook_id,
                dataset_id=dataset.id,
                dataset_type=dataset.type,
                connection_id=dataset.connection_id,
                created_at=dataset.created_at,
            ).model_dump()
            for dataset in connection_datasets
        ]

        return success_response(
            data=connections_list,
            message=f"Retrieved {len(connections_list)} connection(s) for notebook",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_notebook_connections_endpoint: {str(e)}",
            posthog_context={"function": "get_notebook_connections_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving notebook connections",
        )


@router.get("/notebooks/{notebook_id}/connections/details")
async def get_notebook_connections_with_details_endpoint(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get ALL datasets (connections AND files) with full details for a notebook."""
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has READ_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access notebooks you created",
                )

        datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)

        conn_repo = ConnectionRepository(session)
        datasources_with_details = []

        for dataset in datasets:
            if dataset.type == "connection" and dataset.connection_id:
                connection = await conn_repo.get(dataset.connection_id)
                if connection:
                    decrypted_obj = await connection.get_decrypted_connection_obj(session)

                    schema_data = None
                    if connection.schema_cache:
                        try:
                            schema_data = json.loads(connection.schema_cache)
                        except json.JSONDecodeError:
                            schema_data = None

                    datasources_with_details.append(
                        {
                            "id": dataset.id,  # Use dataset ID for consistency with file datasets
                            "type": connection.type,
                            "name": connection.name,
                            "connection_obj": redact_connection_obj(decrypted_obj),
                            "connection_id": connection.id,  # Keep connection UUID for reference
                            "created_at": connection.created_at.isoformat(),
                            "notebook_connection_id": dataset.id,
                            "dataset_id": dataset.id,
                            "schema": schema_data,
                            "schema_updated_at": connection.schema_updated_at.isoformat()
                            if connection.schema_updated_at
                            else None,
                        }
                    )
            elif dataset.type == "file":
                dataset_details = await DatasetService.get_dataset_with_details(session, dataset.id)

                if dataset_details:
                    files_list = dataset_details.get("files", [])

                    connection_obj = {
                        "files": files_list,
                        "dataset_type": "file",
                        "db_type": "duckdb",
                    }

                    file_type = "csv"
                    dataset_name = dataset.name or "Unnamed Dataset"

                    if files_list and len(files_list) > 0:
                        first_file = files_list[0]
                        file_type = first_file.get("type", "csv")

                    datasources_with_details.append(
                        {
                            "id": dataset.id,
                            "type": file_type,
                            "name": dataset_name,
                            "connection_obj": connection_obj,
                            "db_type": "duckdb",
                            "created_at": dataset.created_at.isoformat(),
                            "notebook_connection_id": dataset.id,
                            "dataset_id": dataset.id,
                            "schema": dataset_details.get("schema"),
                            "schema_updated_at": dataset.created_at.isoformat(),
                        }
                    )

        return success_response(
            data=datasources_with_details,
            message=f"Retrieved {len(datasources_with_details)} datasource(s) with details",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_notebook_connections_with_details_endpoint: {str(e)}",
            posthog_context={"function": "get_notebook_connections_with_details_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving connection details",
        )


@router.get("/notebooks/{notebook_id}/filters")
async def get_notebook_filters_endpoint(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get saved dashboard filter config for a notebook."""
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has READ_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access notebooks you created",
                )

        config_payload: dict[str, object] = {"filters": [], "version": 1, "created_at": None}
        if notebook.filters_config:
            try:
                parsed = json.loads(notebook.filters_config)
                if isinstance(parsed, dict):
                    raw_filters = parsed.get("filters")
                    if isinstance(raw_filters, list):
                        config_payload["filters"] = normalize_filters_for_client(
                            [f for f in raw_filters if isinstance(f, dict)]
                        )
                    version = parsed.get("version")
                    if isinstance(version, int):
                        config_payload["version"] = version
                    created_at = parsed.get("created_at")
                    if isinstance(created_at, str):
                        config_payload["created_at"] = created_at
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid filters_config JSON for notebook %s",
                    notebook_id,
                    posthog_context={"function": "get_notebook_filters_endpoint", "notebook_id": notebook_id},
                )

        return success_response(data=config_payload, message="Retrieved notebook filters")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_notebook_filters_endpoint: {str(e)}",
            posthog_context={"function": "get_notebook_filters_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving notebook filters",
        )


@router.post("/notebooks/{notebook_id}/connections/{connection_id}/refresh-schema")
async def refresh_notebook_connection_schema_endpoint(
    notebook_id: str,
    connection_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.CONNECTION_UPDATE, Scope.CONNECTION_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Refresh schema for a connection that's part of this notebook's datasets."""
    notebook = await NotebookService.get_notebook(session, notebook_id)
    if notebook is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

    conn_repo = ConnectionRepository(session)
    connection = await conn_repo.get(connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

    # If user only has UPDATE_OWN scope, verify ownership
    if not auth.has_scope(Scope.CONNECTION_UPDATE):
        if connection.created_by is None or str(connection.created_by) != str(auth.user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only refresh schema for connections you created",
            )

    # Check if connection is associated with notebook via datasets
    datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)
    connection_exists = any(d.type == "connection" and str(d.connection_id) == connection_id for d in datasets)
    if not connection_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connection not associated with this notebook"
        )

    try:
        connection, schema = await ConnectionService.refresh_connection_schema(
            connection_id=connection_id,
            session=session,
        )
    except ConnectionError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Database Connection Failed",
                "message": str(e),
                "type": "connection_error",
            },
        )
    except Exception as e:
        logger.error(
            f"Failed to refresh schema for connection {connection_id}: {str(e)}",
            posthog_context={
                "function": "refresh_notebook_connection_schema_endpoint",
                "connection_id": connection_id,
                "notebook_id": notebook_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Schema Refresh Failed",
                "message": f"Failed to refresh schema: {str(e)}",
                "type": "schema_error",
            },
        )

    # Return the refreshed connection with schema data
    return success_response(
        data={
            "id": connection.id,
            "type": connection.type,
            "name": connection.name,
            "schema": schema,
            "schema_updated_at": connection.schema_updated_at.isoformat() if connection.schema_updated_at else None,
        },
        message="Database schema refreshed successfully",
    )


@router.get("/notebooks/{notebook_id}/html", response_class=HTMLResponse)
async def get_notebook_html_endpoint(
    notebook_id: str,
    request: Request,
    version: int | None = None,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get the HTML content for a notebook from the database. Optionally specify a version number."""
    try:
        html_content: str
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            # Return a fallback HTML page instead of 404 to allow PDF generation
            # This prevents PDF export from failing due to notebook lookup issues
            logger.warning(f"Notebook {notebook_id} not found, returning fallback HTML for PDF generation")
            html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Notebook {notebook_id[:8]}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }}
        .warning {{ background-color: #fff3cd; border: 1px solid #ffc107; padding: 20px; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="warning">
        <h1>Notebook {notebook_id[:8]}</h1>
        <p>This notebook could not be found in the database.</p>
        <p>If you're seeing this in a PDF export, the notebook may have been deleted or there may be a temporary database issue.</p>
    </div>
</body>
</html>"""
            response = HTMLResponse(content=html_content)
            _set_viewer_cookie_if_possible(response, request, auth)
            return response

        # If user only has READ_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access notebooks you created",
                )

        if version is not None:
            html_content = await NotebookService.get_notebook_html_version(session, notebook_id, version)
            if html_content is None:
                # Return fallback HTML instead of 404
                logger.warning(f"Version {version} not found for notebook {notebook_id}, returning fallback HTML")
                html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Notebook {notebook_id[:8]} - Version {version}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }}
        .warning {{ background-color: #fff3cd; border: 1px solid #ffc107; padding: 20px; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="warning">
        <h1>Notebook {notebook_id[:8]} - Version {version}</h1>
        <p>Version {version} not found for this notebook.</p>
    </div>
</body>
</html>"""
        else:
            html_content = await NotebookService.get_notebook_html_content(session, notebook_id)
            if html_content is None:
                html_content = f"""<!DOCTYPE html>

<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Notebook {notebook_id[:8]}</title>
</head>
<body>
    <h1>Welcome to Notebook {notebook_id[:8]}</h1>
    <p>No content yet. Start building your dashboard!</p>
</body>
</html>"""

        response = HTMLResponse(content=html_content)
        _set_viewer_cookie_if_possible(response, request, auth)
        return response
    except Exception as e:
        logger.error(
            f"Unexpected error in get_notebook_html_endpoint: {str(e)}",
            posthog_context={"function": "get_notebook_html_endpoint", "notebook_id": notebook_id, "version": version},
        )
        # Return error HTML for graceful degradation
        error_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error - Notebook {notebook_id[:8]}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background-color: #f5f5f5; }}
        .error {{ background-color: #f8d7da; border: 1px solid #dc3545; padding: 20px; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="error">
        <h1>Error Loading Notebook</h1>
        <p>An unexpected error occurred while loading the notebook content.</p>
    </div>
</body>
</html>"""
        response = HTMLResponse(content=error_html, status_code=500)
        _set_viewer_cookie_if_possible(response, request, auth)
        return response


@router.get("/notebooks/{notebook_id}/dashboards/versions")
async def list_dashboard_versions_endpoint(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get all dashboard versions for a notebook."""
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has READ_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access notebooks you created",
                )

        versions = await NotebookService.list_dashboard_versions(session, notebook_id)
        return success_response(data=versions, message=f"Retrieved {len(versions)} dashboard version(s)")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in list_dashboard_versions_endpoint: {str(e)}",
            posthog_context={"function": "list_dashboard_versions_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while listing dashboard versions",
        )


@router.get("/notebooks/{notebook_id}/dashboards/versions/{version_num}")
async def get_dashboard_version_endpoint(
    notebook_id: str,
    version_num: int,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    """Get a specific dashboard version for a notebook."""
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has READ_OWN scope, verify ownership OR folder access
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            is_owner = notebook.created_by is not None and str(notebook.created_by) == str(auth.user_id)

            if not is_owner:
                # Check if user can access via folder membership
                dashboard_repo = DashboardRepository(session)
                dashboard = await dashboard_repo.get_version(notebook_id, version_num)

                has_folder_access = False
                if dashboard:
                    has_folder_access = await FolderService.can_access_dashboard_via_folder(
                        dashboard.id, auth.user_id, session
                    )

                if not has_folder_access:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="You can only access notebooks you created",
                    )

        html_content = await NotebookService.get_notebook_html_version(session, notebook_id, version_num)
        if html_content is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Dashboard version {version_num} not found"
            )

        _set_viewer_cookie_if_possible(response, request, auth)
        return success_response(
            data={"version_num": version_num, "html_content": html_content, "notebook_id": notebook_id},
            message=f"Retrieved dashboard version {version_num}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_dashboard_version_endpoint: {str(e)}",
            posthog_context={
                "function": "get_dashboard_version_endpoint",
                "notebook_id": notebook_id,
                "version_num": version_num,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving dashboard version",
        )


@router.patch("/notebooks/{notebook_id}")
async def update_notebook_endpoint(
    notebook_id: str,
    payload: NotebookUpdate,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_UPDATE, Scope.NOTEBOOK_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has UPDATE_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_UPDATE):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only update notebooks you created",
                )

        updated_notebook = await NotebookService.update_notebook(session, notebook_id, payload)
        if updated_notebook is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update notebook")

        notebook_name = payload.notebook_name or notebook.notebook_name
        return success_response(
            data=NotebookRead.model_validate(updated_notebook).model_dump(),
            message=f"Notebook '{notebook_name}' updated successfully",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in update_notebook_endpoint: {str(e)}",
            posthog_context={"function": "update_notebook_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while updating the notebook",
        )


@router.delete("/notebooks/{notebook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notebook_endpoint(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_DELETE, Scope.NOTEBOOK_DELETE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has DELETE_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_DELETE):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only delete notebooks you created",
                )

        deleted = await NotebookService.delete_notebook(session, notebook_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete notebook")

        # Clean up session data for this notebook
        agent_session = await create_agent_session(notebook_id)
        await agent_session.clear_session()

        return success_response(data=None, message="Notebook deleted successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_notebook_endpoint: {str(e)}",
            posthog_context={"function": "delete_notebook_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting the notebook",
        )


@router.get("/notebooks/{notebook_id}/queries", response_model=QueryListResponse)
async def get_notebook_queries_endpoint(
    notebook_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.NOTEBOOK_READ, Scope.NOTEBOOK_READ_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # If user only has READ_OWN scope, verify ownership
        if not auth.has_scope(Scope.NOTEBOOK_READ):
            if notebook.created_by is None or str(notebook.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only access notebooks you created",
                )

        queries_data = await NotebookService.get_saved_queries_for_notebook(session, notebook_id)
        queries = [QueryListItem(id=query_id, name=name) for query_id, name in queries_data]
        return QueryListResponse(queries=queries)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        logger.error(
            f"Failed to get queries for notebook {notebook_id}: {str(e)}",
            posthog_context={"function": "get_notebook_queries_endpoint", "notebook_id": notebook_id},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve queries")


@router.post("/notebooks/{notebook_id}/connections/associate", status_code=status.HTTP_201_CREATED)
async def associate_existing_connection_endpoint(
    notebook_id: str,
    payload: DatasetAssociateRequest,
    auth: AuthContext = Depends(require_scope(Scope.DATASET_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    """Associate an existing connection with a notebook via dataset architecture."""
    try:
        notebook = await NotebookService.get_notebook(session, notebook_id)
        if notebook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook not found")

        # Determine if single or multiple connections
        is_multiple = payload.connection_ids is not None and len(payload.connection_ids) > 0

        if is_multiple:
            # Handle multiple connections
            created_datasets = []
            connections = []
            errors = []

            conn_repo = ConnectionRepository(session)

            # Check for existing associations
            existing_datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)
            existing_connection_ids = {d.connection_id for d in existing_datasets if d.type == "connection"}

            for conn_id in payload.connection_ids:
                try:
                    existing = await conn_repo.get(conn_id)
                    if existing is None:
                        errors.append(f"Connection {conn_id}: Not found")
                        continue

                    # Check if association already exists
                    if conn_id in existing_connection_ids:
                        errors.append(f"Connection {conn_id}: Already associated with this notebook")
                        continue

                    # Use new dataset architecture
                    dataset = await DatasetService.create_dataset(
                        session=session, type="connection", connection_id=conn_id, notebook_id=notebook_id
                    )
                    created_datasets.append(
                        NotebookDatasetRead(
                            id=dataset.id,
                            notebook_id=notebook_id,
                            dataset_id=dataset.id,
                            dataset_type=dataset.type,
                            connection_id=conn_id,
                            created_at=dataset.created_at,
                        )
                    )
                    connections.append(ConnectionRead.model_validate(existing))
                except Exception as e:
                    errors.append(f"Connection {conn_id}: Failed to associate - {str(e)}")

            if not created_datasets:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"message": "Failed to associate any databases", "errors": errors},
                )

            response = DatasetConnectResponse(
                dataset=created_datasets[0] if created_datasets else None,
                connection=connections[0] if connections else None,
                datasets=created_datasets,
                connections=connections,
            )

            message = f"Successfully associated {len(created_datasets)} database(s)"
            if errors:
                message += f" with {len(errors)} error(s)"

            return success_response(data=response.model_dump(), message=message)

        else:
            # Handle single connection (backward compatibility)
            if not payload.connection_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Either connection_id or connection_ids is required",
                )

            conn_repo = ConnectionRepository(session)
            connection = await conn_repo.get(payload.connection_id)
            if connection is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

            # Check if association already exists using dataset architecture
            existing_datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)
            already_connected = any(
                d.type == "connection" and d.connection_id == payload.connection_id for d in existing_datasets
            )

            if already_connected:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This connection is already associated with this notebook",
                )

            # Use new dataset architecture
            dataset = await DatasetService.create_dataset(
                session=session, type="connection", connection_id=payload.connection_id, notebook_id=notebook_id
            )

            response = DatasetConnectResponse(
                dataset=NotebookDatasetRead(
                    id=dataset.id,
                    notebook_id=notebook_id,
                    dataset_id=dataset.id,
                    dataset_type=dataset.type,
                    connection_id=payload.connection_id,
                    created_at=dataset.created_at,
                ),
                connection=ConnectionRead.model_validate(connection),
            )
            return success_response(
                data=response.model_dump(), message=f"Notebook associated with {connection.type} database successfully"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in associate_existing_connection_endpoint: {str(e)}",
            posthog_context={
                "function": "associate_existing_connection_endpoint",
                "notebook_id": notebook_id,
                "connection_id": getattr(payload, "connection_id", None),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while associating connection with notebook",
        )
