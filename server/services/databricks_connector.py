from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from databricks import sql

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

_LIMIT_RE = re.compile(r"\blimit\s+\d+\b", re.IGNORECASE)


class AsyncDatabricksConnector:
    """Async wrapper over the sync databricks-sql-connector driver."""

    def __init__(self, connection_obj: dict[str, Any]):
        self.connection_obj = connection_obj
        self._connected = False
        self._conn: Any = None
        self._lock = asyncio.Lock()

    def _required(self) -> tuple[str, str, str]:
        host = self.connection_obj.get("server_hostname")
        http_path = self.connection_obj.get("http_path")
        token = self.connection_obj.get("access_token")
        if not host:
            raise ValueError("server_hostname is required for Databricks connection")
        if not http_path:
            raise ValueError("http_path is required for Databricks connection")
        if not token:
            raise ValueError("access_token is required for Databricks connection")
        return host, http_path, token

    def _open_sync(self):
        host, http_path, token = self._required()
        kwargs: dict[str, Any] = {
            "server_hostname": host,
            "http_path": http_path,
            "access_token": token,
        }
        catalog = self.connection_obj.get("catalog")
        schema = self.connection_obj.get("schema")
        if catalog:
            kwargs["catalog"] = catalog
        if schema:
            kwargs["schema"] = schema
        return sql.connect(**kwargs)

    def _ensure_conn_sync(self):
        if self._conn is None:
            self._conn = self._open_sync()
        return self._conn

    def _reset_conn_sync(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                logger.debug("Ignoring Databricks connection close error during reset", exc_info=True)
            self._conn = None

    async def connect(self) -> None:
        def _probe():
            conn = self._ensure_conn_sync()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchall()
            except Exception:
                self._reset_conn_sync()
                raise

        async with self._lock:
            await asyncio.to_thread(_probe)
            self._connected = True

    @staticmethod
    def _apply_limit(query: str, limit: int | None) -> str:
        if not limit:
            return query
        stripped = query.rstrip().rstrip(";")
        if _LIMIT_RE.search(stripped):
            return stripped
        leading = stripped.lstrip().lower()
        if leading.startswith("select") or leading.startswith("with"):
            return f"{stripped} LIMIT {int(limit)}"
        return stripped

    async def execute_query(
        self,
        query: str,
        limit: int | None = None,
        timeout: int = 120,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sql_text = self._apply_limit(query, limit)

        def _run():
            conn = self._ensure_conn_sync()
            try:
                with conn.cursor() as cur:
                    if params:
                        cur.execute(sql_text, params)
                    else:
                        cur.execute(sql_text)
                    rows = cur.fetchall()
                    cols = [d[0] for d in (cur.description or [])]
                    return cols, rows
            except Exception:
                self._reset_conn_sync()
                raise

        start = time.perf_counter()
        try:
            async with self._lock:
                cols, rows = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
        except TimeoutError:
            return {"success": False, "error": f"Query timed out after {timeout}s"}
        except Exception as e:
            logger.error(f"Databricks query failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

        elapsed = round(time.perf_counter() - start, 2)
        result = [dict(zip(cols, row, strict=False)) for row in rows]
        return {"success": True, "result": result, "execution_time_seconds": elapsed}

    MAX_TABLES = 200
    SYSTEM_CATALOGS = ("system", "__databricks_internal")
    SYSTEM_SCHEMAS = ("information_schema",)

    async def get_schema(self) -> dict[str, Any]:
        catalog = self.connection_obj.get("catalog")
        schema_name = self.connection_obj.get("schema")

        def _list_catalogs(conn) -> list[str]:
            with conn.cursor() as cur:
                cur.execute("SHOW CATALOGS")
                return [r[0] for r in cur.fetchall() if r[0] not in self.SYSTEM_CATALOGS]

        def _list_schemas(conn, cat: str) -> list[str]:
            with conn.cursor() as cur:
                cur.execute(f"SHOW SCHEMAS IN `{cat}`")
                return [r[0] for r in cur.fetchall() if r[0] not in self.SYSTEM_SCHEMAS]

        def _list_tables(conn, cat: str, sch: str) -> list[str]:
            with conn.cursor() as cur:
                cur.execute(f"SHOW TABLES IN `{cat}`.`{sch}`")
                rows = cur.fetchall()
            if not rows:
                return []
            return [r[1] for r in rows] if len(rows[0]) >= 2 else [r[0] for r in rows]

        def _describe(conn, cat: str, sch: str, tbl: str) -> list[dict[str, str]]:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"DESCRIBE TABLE `{cat}`.`{sch}`.`{tbl}`")
                    rows = cur.fetchall()
                cols = []
                for row in rows:
                    cname = row[0] if len(row) > 0 else ""
                    ctype = row[1] if len(row) > 1 else ""
                    if not cname or cname.startswith("#"):
                        break
                    cols.append({"name": cname, "type": ctype})
                return cols
            except Exception as e:
                logger.warning(f"DESCRIBE failed for {cat}.{sch}.{tbl}: {e}")
                return []

        def _fetch():
            conn = self._ensure_conn_sync()
            try:
                catalogs_list: list[str] = []
                schemas_list: list[str] = []
                tables_out: list[dict[str, Any]] = []
                truncated = False

                if catalog and schema_name:
                    catalogs_list = [catalog]
                    schemas_list = [schema_name]
                    for tname in _list_tables(conn, catalog, schema_name):
                        if len(tables_out) >= self.MAX_TABLES:
                            truncated = True
                            break
                        tables_out.append(
                            {
                                "name": tname,
                                "qualified_name": f"{catalog}.{schema_name}.{tname}",
                                "catalog": catalog,
                                "schema": schema_name,
                                "columns": _describe(conn, catalog, schema_name, tname),
                            }
                        )

                elif catalog:
                    catalogs_list = [catalog]
                    schemas_list = _list_schemas(conn, catalog)
                    for sch in schemas_list:
                        if len(tables_out) >= self.MAX_TABLES:
                            truncated = True
                            break
                        for tname in _list_tables(conn, catalog, sch):
                            if len(tables_out) >= self.MAX_TABLES:
                                truncated = True
                                break
                            tables_out.append(
                                {
                                    "name": f"{sch}.{tname}",
                                    "qualified_name": f"{catalog}.{sch}.{tname}",
                                    "catalog": catalog,
                                    "schema": sch,
                                    "columns": _describe(conn, catalog, sch, tname),
                                }
                            )

                else:
                    catalogs_list = _list_catalogs(conn)
                    for cat in catalogs_list:
                        if len(tables_out) >= self.MAX_TABLES:
                            truncated = True
                            break
                        try:
                            sub_schemas = _list_schemas(conn, cat)
                        except Exception as e:
                            logger.warning(f"SHOW SCHEMAS failed for catalog {cat}: {e}")
                            continue
                        schemas_list.extend(f"{cat}.{s}" for s in sub_schemas)
                        for sch in sub_schemas:
                            if len(tables_out) >= self.MAX_TABLES:
                                truncated = True
                                break
                            try:
                                for tname in _list_tables(conn, cat, sch):
                                    if len(tables_out) >= self.MAX_TABLES:
                                        truncated = True
                                        break
                                    tables_out.append(
                                        {
                                            "name": f"{cat}.{sch}.{tname}",
                                            "qualified_name": f"{cat}.{sch}.{tname}",
                                            "catalog": cat,
                                            "schema": sch,
                                            "columns": _describe(conn, cat, sch, tname),
                                        }
                                    )
                            except Exception as e:
                                logger.warning(f"SHOW TABLES failed for {cat}.{sch}: {e}")

                return {
                    "catalogs": catalogs_list,
                    "schemas": schemas_list,
                    "tables": tables_out,
                    "truncated": truncated,
                }
            except Exception:
                self._reset_conn_sync()
                raise

        async with self._lock:
            result = await asyncio.to_thread(_fetch)
        return {
            "catalog": catalog,
            "schema": schema_name,
            "catalogs": result.get("catalogs", []),
            "schemas": result.get("schemas", []),
            "tables": result.get("tables", []),
            "truncated": result.get("truncated", False),
        }

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._reset_conn_sync)
            self._connected = False
