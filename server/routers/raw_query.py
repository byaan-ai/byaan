import csv
import io
from typing import TYPE_CHECKING

import sqlglot
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.repositories.connections import ConnectionRepository
from server.schemas.raw_query import (
    ErrorCategory,
    ErrorDetail,
    ErrorSeverity,
    RawQueryRequest,
    RawQueryResponse,
)
from server.services.dataset import DatasetService
from server.services.file_operations import DataFrameFileService
from server.services.raw_query import AsyncRawQueryService
from server.utils.custom_logger import get_logger

if TYPE_CHECKING:  # pragma: no cover
    pass

logger = get_logger(__name__)
router = APIRouter()


DISALLOWED_NODES = (
    exp.Delete,
    exp.Insert,
    exp.Update,
    exp.Create,
    exp.Alter,
    exp.Drop,
)


def validate_sql_query(query: str, dialect: str = None) -> str:
    try:
        # Preprocess the query to handle escaped characters and formatting
        processed_query = query.replace("\\n", "\n").replace("\\t", "\t").strip()

        # Remove trailing semicolon if present
        if processed_query.endswith(";"):
            processed_query = processed_query[:-1].strip()

        # Parse with the appropriate dialect for accurate validation
        expressions = sqlglot.parse(processed_query, dialect=dialect)

        for tree in expressions:
            for disallowed in DISALLOWED_NODES:
                if tree.find(disallowed):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"🚨 Unsafe query detected: {disallowed.__name__.upper()} is not allowed. "
                        f"Only read-only queries are permitted.",
                    )

        return processed_query

    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"❌ SQL parsing failed: {str(e)}")


DIALECT_MAP = {
    "pg": "postgres",
    "mysql": "mysql",
    "sqlite": None,  # SQLite uses standard SQL, no specific dialect needed
    "mssql": "tsql",  # SQL Server / T-SQL
}


@router.post("/raw-query", response_model=RawQueryResponse)
async def execute_raw_query(
    request: RawQueryRequest,
    auth: AuthContext = Depends(require_scope(Scope.QUERY_EXECUTE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        if not (1 <= request.limit <= 10000):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Limit must be between 1 and 10000",
            )

        if not request.notebook_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="notebook_id is required",
            )

        datasets = await DatasetService.get_datasets_by_notebook(session, request.notebook_id)
        if not datasets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No datasets found for the provided notebook_id",
            )

        # Find the correct dataset by connection_id (required for multi-datasource notebooks)
        if not request.connection_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="connection_id is required for multi-datasource notebooks",
            )

        dataset = None
        # Find dataset by connection_id (for database connections)
        for ds in datasets:
            if ds.type == "connection" and str(ds.connection_id) == str(request.connection_id):
                dataset = ds
                logger.info(f"Found dataset {ds.id} for connection_id {request.connection_id}")
                break
            # Or by dataset ID directly (for file datasets)
            elif str(ds.id) == str(request.connection_id):
                dataset = ds
                logger.info(f"Found dataset {ds.id} matching dataset_id {request.connection_id}")
                break

        if dataset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dataset with connection_id '{request.connection_id}' not found in this notebook",
            )

        if dataset.type == "file":
            result = await DataFrameFileService.execute_duckdb_query_on_dataset(
                session=session,
                dataset_id=dataset.id,
                query=request.query,
                limit=request.limit,
            )
        elif dataset.type == "connection":
            conn_repo = ConnectionRepository(session)
            connection = await conn_repo.get(dataset.connection_id)

            if not connection:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Connection not found for dataset",
                )

            connection_obj = await connection.get_decrypted_connection_obj(session)
            if not connection_obj:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to decrypt connection object",
                )

            db_type = connection.type

            if db_type == "mongo":
                result = await AsyncRawQueryService.execute_raw_query(
                    query=request.query,
                    db_type="mongo",
                    connection_id=connection.id,
                    connection_obj=connection_obj,
                    limit=request.limit,
                )
            elif db_type == "dynamodb":
                result = await AsyncRawQueryService.execute_raw_query(
                    query=request.query,
                    db_type="dynamodb",
                    connection_id=connection.id,
                    connection_obj=connection_obj,
                    limit=request.limit,
                )
            elif db_type == "databricks":
                result = await AsyncRawQueryService.execute_raw_query(
                    query=request.query,
                    db_type="databricks",
                    connection_id=connection.id,
                    connection_obj=connection_obj,
                    limit=request.limit,
                )
            elif db_type in DIALECT_MAP:
                dialect = DIALECT_MAP.get(db_type)
                safe_query = validate_sql_query(request.query, dialect=dialect)
                result = await AsyncRawQueryService.execute_raw_query(
                    query=safe_query,
                    db_type=db_type,
                    connection_id=connection.id,
                    connection_obj=connection_obj,
                    limit=request.limit,
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported database type: {db_type}",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported dataset type: {dataset.type}",
            )

        if "error" in result and not result.get("success"):
            error_detail_dict = result.get("error_detail")
            error_detail = None
            if error_detail_dict:
                error_detail = ErrorDetail(**error_detail_dict)

            return RawQueryResponse(success=False, error=result["error"], error_detail=error_detail)

        return RawQueryResponse(
            success=True,
            result=result.get("result", result),
            total_count=result.get("total_count"),
            returned_count=result.get("returned_count"),
            limited=result.get("limited"),
        )
    except Exception as e:
        logger.error(
            f"Unexpected error in execute_raw_query: {str(e)}",
            posthog_context={
                "function": "execute_raw_query",
                "notebook_id": request.notebook_id,
                "db_type": db_type if "db_type" in locals() else None,
                "limit": request.limit,
            },
        )
        return RawQueryResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
            error_detail=ErrorDetail(
                message=str(e),
                category=ErrorCategory.UNKNOWN,
                severity=ErrorSeverity.ERROR,
                original_query=request.query,
            ),
        )


@router.post("/raw-query/export/csv")
async def export_raw_query_csv(
    request: RawQueryRequest,
    auth: AuthContext = Depends(require_scope(Scope.QUERY_EXECUTE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        if not request.notebook_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="notebook_id is required",
            )

        # Get datasets for notebook
        datasets = await DatasetService.get_datasets_by_notebook(session, request.notebook_id)
        if not datasets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No datasets found for the provided notebook_id",
            )

        # Find the correct dataset by connection_id (required for multi-datasource notebooks)
        if not request.connection_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="connection_id is required for multi-datasource notebooks",
            )

        dataset = None
        # Find dataset by connection_id (for database connections)
        for ds in datasets:
            if ds.type == "connection" and str(ds.connection_id) == str(request.connection_id):
                dataset = ds
                logger.info(f"Found dataset {ds.id} for connection_id {request.connection_id}")
                break
            # Or by dataset ID directly (for file datasets)
            elif str(ds.id) == str(request.connection_id):
                dataset = ds
                logger.info(f"Found dataset {ds.id} matching dataset_id {request.connection_id}")
                break

        if dataset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Dataset with connection_id '{request.connection_id}' not found in this notebook",
            )

        async def generate_csv():
            if dataset.type == "file":
                result = await DataFrameFileService.execute_duckdb_query_on_dataset(
                    session=session,
                    dataset_id=dataset.id,
                    query=request.query,
                    limit=None,
                )
            elif dataset.type == "connection":
                conn_repo = ConnectionRepository(session)
                connection = await conn_repo.get(dataset.connection_id)

                if not connection:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Connection not found for dataset",
                    )

                connection_obj = await connection.get_decrypted_connection_obj(session)
                db_type = connection.type

                if db_type == "mongo":
                    result = await AsyncRawQueryService.execute_raw_query(
                        query=request.query,
                        db_type="mongo",
                        connection_id=connection.id,
                        connection_obj=connection_obj,
                    )
                elif db_type == "dynamodb":
                    result = await AsyncRawQueryService.execute_raw_query(
                        query=request.query,
                        db_type="dynamodb",
                        connection_id=connection.id,
                        connection_obj=connection_obj,
                    )
                elif db_type == "databricks":
                    result = await AsyncRawQueryService.execute_raw_query(
                        query=request.query,
                        db_type="databricks",
                        connection_id=connection.id,
                        connection_obj=connection_obj,
                    )
                elif db_type in DIALECT_MAP:
                    dialect = DIALECT_MAP.get(db_type)
                    safe_query = validate_sql_query(request.query, dialect=dialect)
                    result = await AsyncRawQueryService.execute_raw_query(
                        query=safe_query,
                        db_type=db_type,
                        connection_id=connection.id,
                        connection_obj=connection_obj,
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Unsupported database type: {db_type}",
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported dataset type: {dataset.type}",
                )

            if "error" in result and not result.get("success"):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=result["error"],
                )

            data = result.get("result", [])
            if not data:
                yield "No data found\n"
                return

            fieldnames = list(data[0].keys())
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)
            buffer.seek(0)
            yield buffer.getvalue()

        return StreamingResponse(
            generate_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=query_export.csv"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in export_raw_query_csv: {str(e)}",
            posthog_context={
                "function": "export_raw_query_csv",
                "notebook_id": request.notebook_id,
                "db_type": request.db_type,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while exporting query results to CSV",
        )
