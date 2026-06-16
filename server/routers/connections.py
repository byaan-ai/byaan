from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_any_scope, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.models.connections import ALLOWED_CONN_TYPES
from server.repositories.connections import ConnectionRepository
from server.schemas.connections import (
    ConnectionCreate,
    ConnectionListSimpleResponse,
    ConnectionUpdateResponse,
    DatabricksDiscoverRequest,
    DatabricksDiscoverResponse,
)
from server.schemas.standard_response import success_response
from server.services import databricks_oauth_service
from server.services.connections import ConnectionService
from server.services.database_operations import DatabaseOperationsService
from server.services.databricks_connector import AsyncDatabricksConnector
from server.utils.custom_logger import get_logger

DATABRICKS_DISCOVER_TIMEOUT_SECONDS = 60

logger = get_logger(__name__)

router = APIRouter()


@router.post("/connections/databricks/discover")
async def discover_databricks_endpoint(
    payload: DatabricksDiscoverRequest,
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
):
    warehouses: list[dict] = []
    if not payload.http_path:
        try:
            warehouses = await databricks_oauth_service.list_warehouses(payload.server_hostname, payload.access_token)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    connector = AsyncDatabricksConnector(
        {
            "server_hostname": payload.server_hostname,
            "http_path": payload.http_path or (warehouses[0]["http_path"] if warehouses else ""),
            "oauth": {
                "access_token": payload.access_token,
                "expires_at": 2**31 - 1,  # treat as non-expiring within discovery
                "server_hostname": payload.server_hostname,
            },
        }
    )
    try:
        catalogs = await asyncio.wait_for(connector.list_catalog_tree(), timeout=DATABRICKS_DISCOVER_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": "Databricks discovery timed out",
                "message": f"Listing catalogs exceeded {DATABRICKS_DISCOVER_TIMEOUT_SECONDS}s. Try again or contact your Databricks admin.",
                "type": "timeout_error",
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Databricks discover failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Databricks connection failed",
                "message": str(e),
                "type": "connection_error",
            },
        )
    finally:
        try:
            await connector.close()
        except Exception:
            logger.debug("Ignoring close error after Databricks discover", exc_info=True)

    response = DatabricksDiscoverResponse(catalogs=catalogs, warehouses=warehouses)
    return success_response(
        data=response.model_dump(),
        message=f"Discovered {len(response.catalogs)} catalog(s)",
    )


@router.post("/connections", status_code=status.HTTP_201_CREATED)
async def create_connection_endpoint(
    payload: ConnectionCreate,
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_CREATE)),
    session: AsyncSession = Depends(get_async_session),
):
    if not payload.type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connection type is required")

    # Reject file types - they should use the file upload endpoint
    if payload.type in ("csv", "excel", "parquet", "json"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{payload.type}' should be uploaded via POST /datasets/upload-files endpoint, not as a connection",
        )

    if payload.type not in ALLOWED_CONN_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid connection type: {payload.type}. Allowed types: {', '.join(ALLOWED_CONN_TYPES)}",
        )

    if payload.type == "databricks":
        oauth = (payload.connection_obj or {}).get("oauth") or {}
        if not oauth.get("access_token") or not oauth.get("refresh_token"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Databricks connections require an OAuth block with access_token and refresh_token. "
                "Use the Sign in with Databricks flow.",
            )

    try:
        connection, schema = await ConnectionService.create_connection_with_schema(
            connection_type=payload.type,
            connection_name=payload.name or "",
            connection_obj=payload.connection_obj,
            session=session,
            tenant_id=auth.tenant_id,
            created_by=auth.user_id,
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Schema Fetch Failed",
                "message": f"Failed to fetch schema: {str(e)}",
                "type": "schema_error",
            },
        )

    response = ConnectionUpdateResponse.model_validate(connection)
    response.database_schema = schema
    return success_response(
        data=response.model_dump(), message=f"Connection created successfully for {connection.type} database"
    )


@router.get("/connections")
async def list_connections_endpoint(
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        # Get all connections in tenant
        connections_with_names = await ConnectionService.get_connections_with_names(session)

        # Filter based on creator role: owner/admin creations visible to all, member creations private
        filtered_connections = await ConnectionService.filter_connections_by_creator_role(
            connections_with_names,
            current_user_id=auth.user_id,
            tenant_id=auth.tenant_id,
            session=session,
        )

        response = ConnectionListSimpleResponse(items=filtered_connections, total=len(filtered_connections))
        return success_response(
            data=response.model_dump(), message=f"Retrieved {len(filtered_connections)} connection(s)"
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in list_connections_endpoint: {str(e)}",
            posthog_context={"function": "list_connections_endpoint"},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while listing connections",
        )


@router.get("/connections/{connection_id}")
async def get_connection_endpoint(
    connection_id: str,
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ConnectionRepository(session)
        connection = await repo.get(connection_id)
        if not connection:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

        response = ConnectionUpdateResponse.model_validate(connection)

        schema = ConnectionService.get_cached_schema(connection)
        if schema:
            response.database_schema = schema

        return success_response(
            data=response.model_dump(), message=f"Connection details retrieved for {connection.type} database"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_connection_endpoint: {str(e)}",
            posthog_context={"function": "get_connection_endpoint", "connection_id": connection_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while retrieving connection",
        )


@router.post("/connections/{connection_id}/refresh-schema")
async def refresh_connection_schema_endpoint(
    connection_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.CONNECTION_UPDATE, Scope.CONNECTION_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ConnectionRepository(session)
        connection = await repo.get(connection_id)
        if not connection:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

        if not auth.has_scope(Scope.CONNECTION_UPDATE):
            if connection.created_by is None or str(connection.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only update connections you created",
                )

        connection, schema = await ConnectionService.refresh_connection_schema(
            connection_id=connection_id,
            session=session,
        )

        response = ConnectionUpdateResponse.model_validate(connection)
        response.database_schema = schema
        return success_response(data=response.model_dump(), message="Database schema refreshed successfully")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "Database Connection Failed", "message": str(e), "type": "connection_error"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Schema Fetch Failed",
                "message": f"Failed to fetch schema: {str(e)}",
                "type": "schema_error",
            },
        )


@router.put("/connections/{connection_id}")
async def update_connection_endpoint(
    connection_id: str,
    payload: ConnectionCreate,
    auth: AuthContext = Depends(require_any_scope(Scope.CONNECTION_UPDATE, Scope.CONNECTION_UPDATE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    if payload.type not in ALLOWED_CONN_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid connection type: {payload.type}. Allowed types: {', '.join(ALLOWED_CONN_TYPES)}",
        )

    try:
        repo = ConnectionRepository(session)
        connection = await repo.get(connection_id)
        if not connection:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

        if not auth.has_scope(Scope.CONNECTION_UPDATE):
            if connection.created_by is None or str(connection.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only update connections you created",
                )

        connection, schema = await ConnectionService.update_connection_with_schema(
            connection_id=connection_id,
            connection_type=payload.type,
            connection_name=payload.name or "",
            connection_obj=payload.connection_obj,
            session=session,
        )

        if payload.is_public is not None:
            connection.is_public = payload.is_public
            await session.commit()
            await session.refresh(connection)

        response = ConnectionUpdateResponse.model_validate(connection)
        response.database_schema = schema
        return success_response(
            data=response.model_dump(), message=f"Connection updated successfully for {connection.type} database"
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "Database Connection Failed",
                "message": str(e),
                "type": "connection_error",
                "connection_saved": True,
            },
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Schema Fetch Failed",
                "message": f"Connection saved but failed to fetch schema: {str(e)}",
                "type": "schema_error",
                "connection_saved": True,
            },
        )


@router.delete("/connections/{connection_id}")
async def delete_connection_endpoint(
    connection_id: str,
    auth: AuthContext = Depends(require_any_scope(Scope.CONNECTION_DELETE, Scope.CONNECTION_DELETE_OWN)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        repo = ConnectionRepository(session)
        connection = await repo.get(connection_id)
        if not connection:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found")

        # If user only has DELETE_OWN scope, verify ownership
        if not auth.has_scope(Scope.CONNECTION_DELETE):
            if connection.created_by is None or str(connection.created_by) != str(auth.user_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only delete connections you created",
                )

        await repo.delete(connection_id)
        await session.commit()

        return success_response(data={"id": connection_id}, message="Connection deleted successfully")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_connection_endpoint: {str(e)}",
            posthog_context={"function": "delete_connection_endpoint", "connection_id": connection_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while deleting connection",
        )


@router.get("/connections/schema/{notebook_id}")
async def get_database_schema_by_notebook(
    notebook_id: str,
    db_type: str | None = Query(None, description="Filter by database type (pg, mysql, mongo, sqlite, mssql)"),
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        schema_info = await DatabaseOperationsService.get_database_schema_by_notebook_id(
            session=session, notebook_id=notebook_id, db_type=db_type
        )
        return success_response(
            data=schema_info,
            message=f"Database schema retrieved for {schema_info.get('datasource_type', 'unknown')} database",
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "Database Connection Failed", "message": str(e), "type": "connection_error"},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in get_database_schema: {str(e)}",
            posthog_context={
                "function": "get_database_schema_by_notebook",
                "notebook_id": notebook_id,
                "db_type": db_type,
            },
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Internal Server Error",
                "message": "An unexpected error occurred while fetching the database schema. Please try again later.",
                "type": "internal_error",
            },
        )


@router.get("/connections/health/{notebook_id}")
async def check_database_health_by_notebook(
    notebook_id: str,
    db_type: str | None = Query(None, description="Filter by database type (pg, mysql, mongo, sqlite, mssql)"),
    auth: AuthContext = Depends(require_scope(Scope.CONNECTION_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        schema_info = await DatabaseOperationsService.get_database_schema_by_notebook_id(
            session=session, notebook_id=notebook_id, db_type=db_type
        )

        return success_response(
            data={
                "notebook_id": notebook_id,
                "datasource_type": schema_info.get("datasource_type"),
                "datasource_name": schema_info.get("datasource_name"),
                "status": "healthy",
            },
            message="Database connection is healthy",
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ConnectionError as e:
        return success_response(
            data={"notebook_id": notebook_id, "datasource_type": db_type, "status": "unhealthy", "error": str(e)},
            message="Database connection is unhealthy",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
