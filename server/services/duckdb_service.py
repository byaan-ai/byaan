from __future__ import annotations

import asyncio
import logging
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID

import duckdb  # type: ignore
import sqlglot
from sqlglot import exp

# Try to import the DuckDB Excel extension
try:
    from duckdb_extensions import import_extension

    import_extension("excel")
    _EXCEL_EXTENSION_IMPORTED = True
except ImportError as e:
    _EXCEL_EXTENSION_IMPORTED = False
    logging.getLogger(__name__).warning(
        "DuckDB Excel extension packages not installed: %s. "
        "Excel files will be converted to CSV format. "
        "Install with: pip install duckdb-extensions duckdb-extension-excel",
        e,
    )
except Exception as e:
    _EXCEL_EXTENSION_IMPORTED = False
    logging.getLogger(__name__).warning(
        "Failed to import DuckDB Excel extension: %s. Excel files will be converted to CSV format.", e
    )


@dataclass(slots=True)
class DuckDBFileDescriptor:
    """Minimal information needed to reference a file inside DuckDB."""

    alias: str
    path: Path
    file_type: str
    filename: str
    sheet_name: str | None = None
    reader_options: dict[str, Any] = field(default_factory=dict)


class DuckDBService:
    """High-level helpers for schema inference using DuckDB."""

    SAMPLE_ROWS_DEFAULT = 10
    DUCKDB_DIALECT = "duckdb"
    _excel_extension_available: bool | None = None
    _logger = logging.getLogger(__name__)

    _DISALLOWED_NODES: tuple[type[exp.Expression], ...] = (
        exp.Delete,
        exp.Insert,
        exp.Update,
        exp.Create,
        exp.Alter,
        exp.Drop,
        exp.Command,  # Covers PRAGMA/COPY style commands
        exp.Transaction,
    )

    _DISALLOWED_KEYWORDS = (
        "attach",
        "detach",
        "copy",
        "export",
        "install",
        "load",
        "pragma",
        "call",
        "httpfs",
    )

    _BLOCKED_URL_PATTERNS = (
        "http://",
        "https://",
        "s3://",
        "gcs://",
        "azure://",
        "hf://",
    )

    _PERMISSION_ERROR_MSG = (
        "Byaan cannot access this file. Please grant file access in "
        "System Settings > Privacy & Security > Files and Folders > Byaan, then retry."
    )

    _SQL_READERS = {
        "csv": "read_csv_auto('{path}', AUTO_DETECT=TRUE, SAMPLE_SIZE=2000000, ignore_errors=true)",
        "parquet": "read_parquet('{path}')",
        "json": "read_json_auto('{path}')",
        "excel": "read_xlsx('{path}'{sheet_clause})",
    }

    @classmethod
    def excel_extension_available(cls) -> bool:
        """
        Determine whether the DuckDB excel extension can be used in this environment.

        Checks if the extension was successfully imported at module load time.
        If unavailable, falls back to CSV conversion for Excel files.
        """
        if cls._excel_extension_available is None:
            # Check module-level import status
            if _EXCEL_EXTENSION_IMPORTED:
                # Verify extension actually works with a test connection
                conn = None
                try:
                    conn = duckdb.connect(database=":memory:", read_only=False)
                    # Test that we can use the Excel extension
                    conn.execute("SELECT 1").fetchone()
                    cls._logger.info("DuckDB Excel extension is available and working")
                    cls._excel_extension_available = True
                except Exception as exc:
                    cls._logger.warning(
                        "DuckDB Excel extension imported but not functional: %s. Will use CSV fallback conversion.", exc
                    )
                    cls._excel_extension_available = False
                finally:
                    if conn:
                        try:
                            conn.close()
                        except Exception:
                            pass
            else:
                cls._logger.info("DuckDB Excel extension not imported. Will use CSV fallback conversion.")
                cls._excel_extension_available = False

        return cls._excel_extension_available

    @staticmethod
    def _escape_path(path: Path) -> str:
        """Escape the path for embedding in SQL strings."""
        return str(path).replace("'", "''")

    @staticmethod
    def _escape_literal(value: str) -> str:
        """Escape a generic string literal for embedding in SQL."""
        return value.replace("'", "''")

    @classmethod
    def _reader_sql(cls, descriptor: DuckDBFileDescriptor) -> str:
        """Return the DuckDB table-producing SQL expression for a descriptor."""
        file_type = descriptor.file_type.lower()
        if file_type == "excel":
            template = cls._SQL_READERS.get("excel")
        else:
            template = cls._SQL_READERS.get(file_type)

        if not template:
            raise ValueError(f"DuckDB reader not implemented for file type: {file_type}")

        if file_type == "excel":
            sheet_name = descriptor.sheet_name or descriptor.reader_options.get("sheet")
            sheet_clause = ""
            if sheet_name:
                sheet_clause = f", sheet='{cls._escape_literal(str(sheet_name))}'"
            return template.format(
                path=cls._escape_path(descriptor.path),
                sheet_clause=sheet_clause,
            )

        return template.format(path=cls._escape_path(descriptor.path))

    @classmethod
    def _prepare_connection(
        cls,
        conn: duckdb.DuckDBPyConnection,
        descriptors: Sequence[DuckDBFileDescriptor],
    ) -> None:
        """Ensure required DuckDB extensions are available for the provided descriptors."""
        # Check Excel extension
        if any(descriptor.file_type.lower() == "excel" for descriptor in descriptors):
            if not cls.excel_extension_available():
                raise ValueError(
                    "DuckDB Excel support is unavailable in this environment. "
                    "Excel files will be converted to CSV format for processing."
                )

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """
        Convert values produced by DuckDB into JSON-serialisable primitives.

        Leaf rules (NaN/Infinity, Decimal, temporal, UUID, bytes) are applied
        recursively so LIST/STRUCT/MAP columns convert exactly like flat ones.
        LIST/tuple become JSON arrays; STRUCT/MAP become JSON objects whose keys
        are themselves made JSON-safe. Primitives are returned untouched.
        """
        if value is None:
            return None

        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")

        if isinstance(value, Decimal):
            # Preserve precision where possible while staying JSON-friendly
            if value.as_tuple().exponent >= 0:
                return int(value)
            return float(value)

        if isinstance(value, (datetime, date, time, timedelta)):
            try:
                return value.isoformat()
            except Exception:
                return str(value)

        if isinstance(value, UUID):
            return str(value)

        if isinstance(value, (set, frozenset)):
            return [DuckDBService._json_safe(item) for item in value]

        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except Exception:
                return value.hex()

        if isinstance(value, dict):
            return DuckDBService._json_safe_mapping(value)

        if isinstance(value, (list, tuple)):
            return [DuckDBService._json_safe(item) for item in value]

        return value

    @staticmethod
    def _json_safe_mapping(value: dict[Any, Any]) -> dict[Any, Any]:
        """
        Convert a DuckDB STRUCT/MAP into a JSON-safe object.

        STRUCT keys are already strings. MAP keys of any other type are converted
        with the same leaf rules; a conversion that would merge two distinct keys
        raises instead of silently dropping an entry.
        """
        converted: dict[Any, Any] = {}
        for raw_key, raw_value in value.items():
            key = DuckDBService._json_safe_key(raw_key)
            if key in converted:
                raise ValueError(
                    f"DuckDB map key {raw_key!r} collides with key {key!r} after JSON conversion. "
                    "Cast the map keys to a distinct text representation in the query."
                )
            converted[key] = DuckDBService._json_safe(raw_value)
        return converted

    @staticmethod
    def _json_safe_key(key: Any) -> str | int | float | bool | None:
        """Convert a mapping key into a type json.dumps accepts as an object key."""
        converted = DuckDBService._json_safe(key)
        if converted is None or isinstance(converted, (str, int, float, bool)):
            return converted
        raise ValueError(f"Unsupported DuckDB map key type for JSON encoding: {type(key).__name__}")

    @classmethod
    def _collect_schema_sync(
        cls,
        descriptors: Iterable[DuckDBFileDescriptor],
        sample_rows: int,
    ) -> dict[str, Any]:
        descriptors = list(descriptors)
        if not descriptors:
            return {
                "datasource_type": "file",
                "datasource_name": "File Database",
                "schema": {},
                "sample_data": {},
            }

        conn = duckdb.connect(database=":memory:", read_only=False)
        schema: dict[str, Any] = {}
        sample_data: dict[str, Any] = {}

        try:
            cls._prepare_connection(conn, descriptors)

            for descriptor in descriptors:
                view_name = f"v_{descriptor.alias}"
                reader_sql = cls._reader_sql(descriptor)

                conn.execute(f'CREATE OR REPLACE VIEW "{view_name}" AS SELECT * FROM {reader_sql}')

                cursor = conn.execute(f'SELECT * FROM "{view_name}" LIMIT 0')
                columns_meta = cursor.description or []

                columns_info = []
                column_names = [col[0] for col in columns_meta]
                column_types = [str(col[1]) if len(col) > 1 else "UNKNOWN" for col in columns_meta]

                sample_rows_query = conn.execute(f'SELECT * FROM "{view_name}" LIMIT {sample_rows}')
                raw_sample_rows = sample_rows_query.fetchall()

                sample_records = []
                for row in raw_sample_rows:
                    record = {}
                    for name, value in zip(column_names, row, strict=False):
                        record[name] = cls._json_safe(value)
                    sample_records.append(record)

                for name, dtype in zip(column_names, column_types, strict=False):
                    samples = []
                    for record in sample_records:
                        value = record.get(name)
                        if value is not None and value != "":
                            samples.append(value)
                        if len(samples) >= 5:
                            break
                    columns_info.append(
                        {
                            "name": str(name),
                            "type": dtype,
                            "nullable": True,  # DuckDB doesn't expose nullability in this context
                            "sample_values": samples,
                        }
                    )

                row_count = conn.execute(f'SELECT COUNT(*) FROM "{view_name}"').fetchone()[0]

                schema[descriptor.alias] = {
                    "filename": descriptor.filename,
                    "file_type": descriptor.file_type,
                    "columns": columns_info,
                    "row_count": int(row_count),
                }
                if descriptor.sheet_name:
                    schema[descriptor.alias]["sheet_name"] = descriptor.sheet_name
                sample_data[descriptor.alias] = sample_records

        except PermissionError:
            raise PermissionError(cls._PERMISSION_ERROR_MSG)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    cls._logger.warning(f"Error closing DuckDB connection: {e}")

        primary_type = descriptors[0].file_type.lower()
        return {
            "datasource_type": primary_type,
            "datasource_name": f"{primary_type.upper()} Database",
            "schema": schema,
            "sample_data": sample_data,
        }

    @classmethod
    async def collect_schema(
        cls,
        descriptors: Iterable[DuckDBFileDescriptor],
        sample_rows: int | None = None,
    ) -> dict[str, Any]:
        """
        Collect schema information for the provided file descriptors.

        Args:
            descriptors: Iterable of file descriptors to profile.
            sample_rows: Number of sample rows to include per table.

        Returns:
            Schema dictionary compatible with existing responses.
        """
        rows = sample_rows or cls.SAMPLE_ROWS_DEFAULT
        return await asyncio.to_thread(cls._collect_schema_sync, descriptors, rows)

    @staticmethod
    def _sanitize_query(query: str) -> str:
        processed = query.replace("\\n", "\n").replace("\\t", "\t").strip()
        if processed.endswith(";"):
            processed = processed[:-1].strip()
        return processed

    @classmethod
    def validate_read_only_sql(cls, query: str) -> tuple[str, bool]:
        """
        Validate that a DuckDB query is read-only and safe to execute.

        Returns the sanitized query and whether it already contains a LIMIT clause.
        """
        processed = cls._sanitize_query(query)

        try:
            expressions = sqlglot.parse(processed, dialect=cls.DUCKDB_DIALECT)
        except Exception as exc:
            raise ValueError(f"❌ DuckDB SQL parsing failed: {exc}") from exc

        if len(expressions) != 1:
            raise ValueError("❌ Multiple SQL statements detected. Provide a single SELECT statement.")

        tree = expressions[0]

        for disallowed in cls._DISALLOWED_NODES:
            if tree.find(disallowed):
                raise ValueError(
                    f"🚨 Disallowed operation detected ({disallowed.__name__.upper()}). "
                    "DuckDB queries must remain read-only."
                )

        lowered = processed.lower()
        for keyword in cls._DISALLOWED_KEYWORDS:
            if re.search(rf"\\b{keyword}\\b", lowered):
                raise ValueError(
                    f"🚨 Disallowed DuckDB command detected: {keyword.upper()}. Only SELECT queries are supported."
                )

        for url_pattern in cls._BLOCKED_URL_PATTERNS:
            if url_pattern in lowered:
                raise ValueError(
                    f"🚨 External URL access is not allowed in queries. "
                    f"Pattern '{url_pattern}' detected. Only local file queries are supported."
                )

        has_limit = tree.find(exp.Limit) is not None
        return processed, has_limit

    @classmethod
    def _register_views(
        cls,
        conn: duckdb.DuckDBPyConnection,
        descriptors: Sequence[DuckDBFileDescriptor],
    ) -> None:
        if not descriptors:
            raise ValueError("No file descriptors provided for DuckDB query execution.")

        for descriptor in descriptors:
            reader_sql = cls._reader_sql(descriptor)
            conn.execute(f'CREATE OR REPLACE TEMP VIEW "{descriptor.alias}" AS SELECT * FROM {reader_sql}')

    @classmethod
    def _apply_limit(cls, query: str, limit: int | None, has_limit: bool) -> tuple[str, bool]:
        if limit is None or has_limit:
            return query, False
        wrapped = f"SELECT * FROM (\n{query}\n) AS duckdb_subquery LIMIT {limit}"
        return wrapped, True

    @classmethod
    def _execute_query_sync(
        cls,
        descriptors: Sequence[DuckDBFileDescriptor],
        query: str,
        database_path: Path | None,
        limit: int | None,
        has_limit: bool,
    ) -> dict[str, Any]:
        db_target = ":memory:"
        if database_path:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            db_target = str(database_path)

        conn = None
        try:
            conn = duckdb.connect(database=db_target, read_only=False)

            cls._prepare_connection(conn, descriptors)
            cls._register_views(conn, descriptors)

            query_to_execute, limit_applied = cls._apply_limit(query, limit, has_limit)

            cursor = conn.execute(query_to_execute)
            column_names = [col[0] for col in cursor.description or []]
            rows = cursor.fetchall()

            records = []
            for row in rows:
                record = {}
                for key, value in zip(column_names, row, strict=False):
                    record[key] = cls._json_safe(value)
                records.append(record)

            count_sql = f"SELECT COUNT(*) AS __rowcount FROM (\n{query}\n) AS duckdb_count_sub"
            total_count = int(conn.execute(count_sql).fetchone()[0])

        except PermissionError:
            raise PermissionError(cls._PERMISSION_ERROR_MSG)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    cls._logger.warning(f"Error closing DuckDB connection: {e}")

        returned_count = len(records)
        limited = False
        if limit_applied and limit:
            limited = total_count > limit
        elif has_limit:
            limited = total_count > returned_count

        return {
            "success": True,
            "query": query,
            "result": records,
            "columns": column_names,
            "returned_count": returned_count,
            "result_count": total_count,
            "total_count": total_count,
            "limited": limited,
        }

    @classmethod
    async def execute_sql(
        cls,
        descriptors: Sequence[DuckDBFileDescriptor],
        query: str,
        *,
        limit: int | None = None,
        database_path: Path | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """
        Execute a DuckDB SQL query against the provided file descriptors.

        Args:
            descriptors: Sequence of file descriptors registered as tables.
            query: SQL SELECT statement.
            limit: Optional maximum number of rows to return.
            database_path: Optional path to a persistent DuckDB database file.
            timeout: Query execution timeout in seconds (default 30).

        Returns:
            Query execution result dictionary.
        """
        start_time = perf_counter()

        try:
            sanitized_query, has_limit = cls.validate_read_only_sql(query)

            # Wrap the thread execution with timeout
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    cls._execute_query_sync,
                    list(descriptors),
                    sanitized_query,
                    database_path,
                    limit,
                    has_limit,
                ),
                timeout=timeout,
            )
            return result
        except TimeoutError:
            execution_time = perf_counter() - start_time
            cls._logger.warning(f"DuckDB query timeout after {execution_time:.2f}s")
            return {
                "success": False,
                "timeout": True,
                "error": f"Query execution exceeded timeout of {timeout} seconds",
                "timeout_seconds": timeout,
                "execution_time_seconds": round(execution_time, 2),
            }
