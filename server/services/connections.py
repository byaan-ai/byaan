from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.connections import Connection
from server.models.datasets import Dataset
from server.models.tenant_member import TenantMember
from server.repositories.connections import ConnectionRepository
from server.services.database_operations import DatabaseOperationsService
from server.services.file_operations import DataFrameFileService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


class ConnectionService:
    @staticmethod
    async def fetch_schema(connection: Connection, session: AsyncSession) -> dict[str, Any]:
        """Fetch database schema without saving to database. Pure function."""
        try:
            connection_obj = await connection.get_decrypted_connection_obj(session)
            if not connection_obj:
                raise ValueError("Failed to decrypt connection object")

            if connection.type == "mongo":
                schema = await DatabaseOperationsService.get_mongo_schema_async(connection_obj)
            elif connection.type == "dynamodb":
                schema = await DatabaseOperationsService.get_dynamodb_schema_async(connection_obj)
            elif connection.type == "databricks":
                schema = await DatabaseOperationsService.get_databricks_schema_async(connection_obj)
            elif connection.type in ["pg", "mysql", "sqlite", "mssql"]:
                # Use unified SQL schema method for all SQL databases
                schema = await DatabaseOperationsService.get_sql_schema_async(connection_obj, db_type=connection.type)
            elif connection.type in ("csv", "excel", "parquet", "json"):
                # Get schema from file (CSV/Excel/Parquet/JSON)
                # Handle both multi-file and legacy single-file formats
                if "files" in connection_obj:
                    # Multi-file format
                    schema = await DataFrameFileService.get_file_schema_multi(
                        connection_obj["files"],
                        session=session,
                    )
                else:
                    # Legacy single-file format
                    file_path = connection_obj.get("file_path")
                    if not file_path:
                        raise ValueError(f"No file path found in {connection.type} connection object")
                    schema = await DataFrameFileService.get_file_schema(
                        None,
                        Path(file_path).name,
                        connection.type,
                        file_path=file_path,
                    )
            else:
                raise ValueError(f"Unsupported database type: {connection.type}")

            logger.info(f"Successfully fetched schema for {connection.type} database")
            return schema

        except ConnectionError as e:
            logger.error(
                f"Connection error while fetching schema: {str(e)}",
                posthog_context={
                    "function": "ConnectionService.fetch_schema",
                    "connection_id": connection.id if hasattr(connection, "id") else None,
                    "connection_type": connection.type,
                    "error_type": "connection_error",
                },
            )
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error fetching schema: {str(e)}",
                posthog_context={
                    "function": "ConnectionService.fetch_schema",
                    "connection_id": connection.id if hasattr(connection, "id") else None,
                    "connection_type": connection.type,
                    "error_type": "unexpected_error",
                },
            )
            raise

    @staticmethod
    async def create_connection_with_schema(
        connection_type: str,
        connection_name: str,
        connection_obj: dict[str, Any],
        session: AsyncSession,
        tenant_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> tuple[Connection, dict[str, Any]]:
        """
        Create a new connection with schema validation.
        Only saves to database if connection and schema fetch succeed.

        Args:
            connection_type: Type of connection (pg, mysql, mongo, etc.)
            connection_name: Display name for the connection
            connection_obj: Connection configuration dictionary
            session: Database session
            tenant_id: Optional tenant ID to associate with the connection

        Returns: (connection, schema)
        Raises: ConnectionError, ValueError, or other exceptions on failure
        """
        try:
            # Create connection instance (not in DB yet)
            connection = Connection(
                type=connection_type, name=connection_name, tenant_id=tenant_id, created_by=created_by
            )
            await connection.set_encrypted_connection_obj(connection_obj, session)

            # Validate connection by fetching schema
            schema = await ConnectionService.fetch_schema(connection, session)

            # Only save if validation succeeded
            connection.schema_cache = json.dumps(schema)
            connection.schema_updated_at = datetime.utcnow()
            session.add(connection)
            await session.commit()
            await session.refresh(connection)

            # Create Dataset wrapper for unified datasources architecture
            dataset = Dataset(
                type="connection",
                connection_id=connection.id,
                name=connection_name or None,
                tenant_id=tenant_id,
                created_by=created_by,
            )
            session.add(dataset)
            await session.commit()

            logger.info(
                f"Successfully created connection {connection.id} with dataset wrapper for {connection.type} database"
            )
            return connection, schema
        except ConnectionError:
            raise
        except Exception as e:
            logger.error(
                f"Failed to create connection with schema: {str(e)}",
                posthog_context={
                    "function": "ConnectionService.create_connection_with_schema",
                    "connection_type": connection_type,
                    "connection_name": connection_name,
                },
            )
            raise

    @staticmethod
    async def update_connection_with_schema(
        connection_id: str,
        connection_type: str,
        connection_name: str,
        connection_obj: dict[str, Any],
        session: AsyncSession,
        tenant_id: UUID | None = None,
    ) -> tuple[Connection, dict[str, Any]]:
        """
        Update existing connection with schema validation.
        First saves connection changes, then fetches and saves schema.

        Returns: (connection, schema)
        Raises: ValueError if connection not found, ConnectionError on connection failure
        """
        try:
            repo = ConnectionRepository(session)
            connection = await repo.get(connection_id)
            if not connection:
                raise ValueError(f"Connection {connection_id} not found")

            # Update connection details
            connection.type = connection_type
            connection.name = connection_name
            await connection.set_encrypted_connection_obj(connection_obj, session)

            # Save connection updates first
            await session.commit()
            await session.refresh(connection)

            # Fetch schema to validate new connection details
            schema = await ConnectionService.fetch_schema(connection, session)

            # Save schema
            connection.schema_cache = json.dumps(schema)
            connection.schema_updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(connection)

            logger.info(f"Successfully updated connection {connection.id} for {connection.type} database")
            return connection, schema
        except (ValueError, ConnectionError):
            raise
        except Exception as e:
            logger.error(
                f"Failed to update connection with schema: {str(e)}",
                posthog_context={
                    "function": "ConnectionService.update_connection_with_schema",
                    "connection_id": connection_id,
                    "connection_type": connection_type,
                },
            )
            raise

    @staticmethod
    async def refresh_connection_schema(
        connection_id: str,
        session: AsyncSession,
    ) -> tuple[Connection, dict[str, Any]]:
        """
        Refresh schema for existing connection.

        Returns: (connection, schema)
        Raises: ValueError if connection not found, ConnectionError on connection failure
        """
        try:
            repo = ConnectionRepository(session)
            connection = await repo.get(connection_id)
            if not connection:
                raise ValueError(f"Connection {connection_id} not found")

            # Fetch fresh schema
            schema = await ConnectionService.fetch_schema(connection, session)

            # Save updated schema
            connection.schema_cache = json.dumps(schema)
            connection.schema_updated_at = datetime.utcnow()
            await session.commit()
            await session.refresh(connection)

            logger.info(f"Successfully refreshed schema for connection {connection.id}")
            return connection, schema
        except (ValueError, ConnectionError):
            raise
        except Exception as e:
            logger.error(
                f"Failed to refresh connection schema: {str(e)}",
                posthog_context={
                    "function": "ConnectionService.refresh_connection_schema",
                    "connection_id": connection_id,
                },
            )
            raise

    @staticmethod
    def get_cached_schema(connection: Connection) -> dict[str, Any] | None:
        try:
            if connection.schema_cache:
                try:
                    from server.utils.schema_utils import unwrap_nested_schema

                    schema_data = json.loads(connection.schema_cache)
                    return unwrap_nested_schema(schema_data, preserve_metadata=True)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.error(
                        f"Failed to parse cached schema for connection {connection.id}: {e}",
                        exc_info=True,
                        posthog_context={
                            "function": "ConnectionService.get_cached_schema",
                            "connection_id": connection.id,
                        },
                    )
                    return None
            return None
        except Exception as e:
            logger.error(
                f"Failed to get cached schema: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "ConnectionService.get_cached_schema",
                    "connection_id": connection.id if hasattr(connection, "id") else None,
                },
            )
            return None

    @staticmethod
    async def get_connections_with_names(session: AsyncSession) -> list[dict[str, str]]:
        """Get list of connections with decrypted database names and hosts."""
        try:
            repo = ConnectionRepository(session)
            connections = await repo.list()

            result = []
            for connection in connections:
                try:
                    # Get decrypted connection object to extract database name and host
                    connection_obj = await connection.get_decrypted_connection_obj(session)

                    # Extract database name from connection object
                    # Prioritize the name field if available
                    database_name = connection.name if connection.name else "Unknown Database"
                    host_name = "Unknown Host"

                    if connection_obj:
                        # If name field is empty, try common database name fields
                        if not connection.name:
                            if connection.type in ("csv", "excel", "parquet", "json"):
                                # For file-based connections, use filename or file type
                                database_name = connection_obj.get("filename") or f"{connection.type.upper()} File"
                            else:
                                database_name = (
                                    connection_obj.get("database")
                                    or connection_obj.get("dbname")
                                    or connection_obj.get("db")
                                    or f"{connection.type.upper()} Database"
                                )

                        # Try common host fields
                        if connection.type in ("csv", "excel", "parquet", "json"):
                            # For file-based connections, show filename as host
                            host_name = connection_obj.get("filename") or f"{connection.type.upper()} File"
                        else:
                            host_name = (
                                connection_obj.get("host")
                                or connection_obj.get("hostname")
                                or connection_obj.get("server")
                                or connection_obj.get("url")
                                or "localhost"
                            )

                    result.append(
                        {
                            "id": connection.id,
                            "name": database_name,
                            "host": host_name,
                            "type": connection.type,
                            "created_at": connection.created_at,
                        }
                    )

                except Exception as e:
                    logger.warning(f"Failed to decrypt connection {connection.id}: {str(e)}")
                    # Fallback to showing connection type if decryption fails
                    result.append(
                        {
                            "id": connection.id,
                            "name": f"{connection.type.upper()} Database",
                            "host": "Unknown Host",
                            "type": connection.type,
                            "created_at": connection.created_at,
                        }
                    )

            return result
        except Exception as e:
            logger.error(
                f"Failed to get connections with names: {str(e)}",
                posthog_context={"function": "ConnectionService.get_connections_with_names"},
            )
            raise

    @staticmethod
    async def filter_connections_by_creator_role(
        connections_list: list[dict[str, Any]],
        current_user_id: UUID,
        tenant_id: UUID,
        session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """
        Filter connections based on creator role:
        - Owner/Admin created connections: visible to everyone
        - Member created connections: only visible to that member

        Args:
            connections_list: List of connection dicts from get_connections_with_names
            current_user_id: Current user's ID
            tenant_id: Current tenant ID
            session: Database session

        Returns:
            Filtered list of connections
        """
        try:
            if not connections_list:
                return []

            # Fetch all tenant members for this tenant in one query
            result = await session.execute(select(TenantMember).where(TenantMember.tenant_id == tenant_id))
            tenant_members = {str(tm.user_id): tm.role for tm in result.scalars().all()}

            # Filter connections
            filtered = []
            for conn_dict in connections_list:
                # Get the full connection object to check created_by
                conn_result = await session.execute(
                    select(Connection).where(Connection.id == UUID(str(conn_dict["id"])))
                )
                conn = conn_result.scalar_one_or_none()

                if not conn:
                    continue

                # If no creator, show to everyone (legacy data)
                if not conn.created_by:
                    filtered.append(conn_dict)
                    continue

                creator_role = tenant_members.get(str(conn.created_by))

                # Owner/Admin creations: visible to everyone
                if creator_role in ("owner", "admin"):
                    filtered.append(conn_dict)
                # Member creations: only visible to creator
                elif creator_role == "member":
                    if str(conn.created_by) == str(current_user_id):
                        filtered.append(conn_dict)
                # Unknown role: show to creator only (safe default)
                else:
                    if str(conn.created_by) == str(current_user_id):
                        filtered.append(conn_dict)

            return filtered

        except Exception as e:
            logger.error(
                f"Failed to filter connections by creator role: {str(e)}",
                posthog_context={"function": "ConnectionService.filter_connections_by_creator_role"},
            )
            raise
