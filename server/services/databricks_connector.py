from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from databricks import sql

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

_LIMIT_RE = re.compile(r"\blimit\s+\d+\b", re.IGNORECASE)

TokenRefreshCallback = Callable[[dict[str, Any]], Awaitable[None]]


class AsyncDatabricksConnector:
    """Async wrapper over the sync databricks-sql-connector driver.

    Auth: OAuth access_token sourced from ``connection_obj["oauth"]``. The connector
    transparently refreshes the access token via the OAuth service when it's near
    expiry and invokes ``on_token_refresh`` so callers can persist the rotated
    refresh token back to the Connection row.
    """

    def __init__(
        self,
        connection_obj: dict[str, Any],
        on_token_refresh: TokenRefreshCallback | None = None,
    ):
        self.connection_obj = connection_obj
        self._connected = False
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self._on_token_refresh = on_token_refresh

    def _required(self) -> tuple[str, str]:
        host = self.connection_obj.get("server_hostname")
        http_path = self.connection_obj.get("http_path")
        if not host:
            raise ValueError("server_hostname is required for Databricks connection")
        if not http_path:
            raise ValueError("http_path is required for Databricks connection")
        return host, http_path

    async def _ensure_fresh_token(self) -> str:
        """Refresh the OAuth access token if within skew of expiry, persist via callback."""
        from server.services import databricks_oauth_service

        self._required()
        oauth = self.connection_obj.get("oauth") or {}
        if not oauth.get("access_token"):
            raise ValueError("OAuth access_token is required for Databricks connection")

        if not databricks_oauth_service.is_oauth_block_expired(oauth):
            return oauth["access_token"]

        if not oauth.get("refresh_token"):
            logger.warning("[DATABRICKS] Access token expired and no refresh_token present")
            return oauth["access_token"]

        # Need a DB session to read OAuth client credentials. Open a short-lived one.
        from server.db.session import AsyncSessionFactory

        async with AsyncSessionFactory() as session:
            client_id, client_secret = await databricks_oauth_service.get_oauth_credentials(session)
        if not client_id or not client_secret:
            raise ValueError("Databricks OAuth credentials are not configured")

        new_oauth = await databricks_oauth_service.refresh_databricks_token(oauth, client_id, client_secret)
        self.connection_obj["oauth"] = new_oauth
        if self._on_token_refresh is not None:
            try:
                await self._on_token_refresh(new_oauth)
            except Exception:
                logger.error("[DATABRICKS] on_token_refresh callback failed", exc_info=True)
        # New token means existing sync connection holds a stale Authorization header.
        self._reset_conn_sync()
        return new_oauth["access_token"]

    def _open_sync(self, access_token: str):
        host, http_path = self._required()
        kwargs: dict[str, Any] = {
            "server_hostname": host,
            "http_path": http_path,
            "access_token": access_token,
        }
        catalog = self.connection_obj.get("catalog")
        schema = self.connection_obj.get("schema")
        if catalog:
            kwargs["catalog"] = catalog
        if schema:
            kwargs["schema"] = schema
        return sql.connect(**kwargs)

    def _ensure_conn_sync(self, access_token: str):
        if self._conn is None:
            self._conn = self._open_sync(access_token)
        return self._conn

    def _reset_conn_sync(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                logger.debug("Ignoring Databricks connection close error during reset", exc_info=True)
            self._conn = None

    async def connect(self) -> None:
        async with self._lock:
            token = await self._ensure_fresh_token()

            def _probe():
                conn = self._ensure_conn_sync(token)
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        cur.fetchall()
                except Exception:
                    self._reset_conn_sync()
                    raise

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

        start = time.perf_counter()
        try:
            async with self._lock:
                token = await self._ensure_fresh_token()

                def _run():
                    conn = self._ensure_conn_sync(token)
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
    MAX_TYPE_LEN = 120
    SYSTEM_CATALOGS = ("system", "__databricks_internal")
    SYSTEM_SCHEMAS = ("information_schema",)

    @staticmethod
    def _shorten_type(t: str, limit: int = 120) -> str:
        """Trim deeply-nested STRUCT/ARRAY/MAP type strings so they don't bloat the agent prompt.

        Databricks columns can carry types like `struct<a:int,b:struct<...>>` that run hundreds
        of characters. The agent only needs the top-level shape; if it needs deeper detail it
        can DESCRIBE on demand.
        """
        if not t or len(t) <= limit:
            return t
        return t[:limit] + "…"

    def _list_catalogs_sync(self, conn) -> list[str]:
        with conn.cursor() as cur:
            cur.execute("SHOW CATALOGS")
            return [r[0] for r in cur.fetchall() if r[0] not in self.SYSTEM_CATALOGS]

    def _list_schemas_sync(self, conn, cat: str) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(f"SHOW SCHEMAS IN `{cat}`")
            return [r[0] for r in cur.fetchall() if r[0] not in self.SYSTEM_SCHEMAS]

    def _list_tables_sync(self, conn, cat: str, sch: str) -> list[str]:
        with conn.cursor() as cur:
            cur.execute(f"SHOW TABLES IN `{cat}`.`{sch}`")
            rows = cur.fetchall()
        if not rows:
            return []
        return [r[1] for r in rows] if len(rows[0]) >= 2 else [r[0] for r in rows]

    def _describe_sync(self, conn, cat: str, sch: str, tbl: str) -> list[dict[str, str]]:
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
                cols.append({"name": cname, "type": self._shorten_type(ctype, self.MAX_TYPE_LEN)})
            return cols
        except Exception as e:
            logger.warning(f"DESCRIBE failed for {cat}.{sch}.{tbl}: {e}")
            return []

    async def list_catalog_tree(self) -> list[dict[str, Any]]:
        """List non-system catalogs and their non-system schemas in one round-trip block.

        Used by the add-connection wizard to populate a (catalog, schema) picker without
        creating a Connection row.
        """

        async with self._lock:
            token = await self._ensure_fresh_token()

            def _walk():
                conn = self._ensure_conn_sync(token)
                try:
                    out: list[dict[str, Any]] = []
                    for cat in self._list_catalogs_sync(conn):
                        try:
                            schemas = self._list_schemas_sync(conn, cat)
                        except Exception as e:
                            logger.warning(f"SHOW SCHEMAS failed for catalog {cat}: {e}")
                            schemas = []
                        out.append({"name": cat, "schemas": schemas})
                    return out
                except Exception:
                    self._reset_conn_sync()
                    raise

            return await asyncio.to_thread(_walk)

    async def get_schema(self) -> dict[str, Any]:
        catalog = self.connection_obj.get("catalog")
        schema_name = self.connection_obj.get("schema")

        def _fetch():
            conn = self._ensure_conn_sync(token)
            try:
                catalogs_list: list[str] = []
                schemas_list: list[str] = []
                tables_out: list[dict[str, Any]] = []
                truncated = False

                if catalog and schema_name:
                    catalogs_list = [catalog]
                    schemas_list = [schema_name]
                    try:
                        table_names = self._list_tables_sync(conn, catalog, schema_name)
                    except Exception as e:
                        logger.warning(f"SHOW TABLES failed for {catalog}.{schema_name}: {e}")
                        table_names = []
                    if not table_names:
                        logger.info(f"No tables visible in {catalog}.{schema_name} (token may lack USE/SELECT)")
                    for tname in table_names:
                        if len(tables_out) >= self.MAX_TABLES:
                            truncated = True
                            break
                        tables_out.append(
                            {
                                "name": tname,
                                "qualified_name": f"{catalog}.{schema_name}.{tname}",
                                "catalog": catalog,
                                "schema": schema_name,
                                "columns": self._describe_sync(conn, catalog, schema_name, tname),
                            }
                        )

                elif catalog:
                    catalogs_list = [catalog]
                    schemas_list = self._list_schemas_sync(conn, catalog)
                    for sch in schemas_list:
                        if len(tables_out) >= self.MAX_TABLES:
                            truncated = True
                            break
                        for tname in self._list_tables_sync(conn, catalog, sch):
                            if len(tables_out) >= self.MAX_TABLES:
                                truncated = True
                                break
                            tables_out.append(
                                {
                                    "name": f"{sch}.{tname}",
                                    "qualified_name": f"{catalog}.{sch}.{tname}",
                                    "catalog": catalog,
                                    "schema": sch,
                                    "columns": self._describe_sync(conn, catalog, sch, tname),
                                }
                            )

                else:
                    catalogs_list = self._list_catalogs_sync(conn)
                    for cat in catalogs_list:
                        if len(tables_out) >= self.MAX_TABLES:
                            truncated = True
                            break
                        try:
                            sub_schemas = self._list_schemas_sync(conn, cat)
                        except Exception as e:
                            logger.warning(f"SHOW SCHEMAS failed for catalog {cat}: {e}")
                            continue
                        schemas_list.extend(f"{cat}.{s}" for s in sub_schemas)
                        for sch in sub_schemas:
                            if len(tables_out) >= self.MAX_TABLES:
                                truncated = True
                                break
                            try:
                                for tname in self._list_tables_sync(conn, cat, sch):
                                    if len(tables_out) >= self.MAX_TABLES:
                                        truncated = True
                                        break
                                    tables_out.append(
                                        {
                                            "name": f"{cat}.{sch}.{tname}",
                                            "qualified_name": f"{cat}.{sch}.{tname}",
                                            "catalog": cat,
                                            "schema": sch,
                                            "columns": self._describe_sync(conn, cat, sch, tname),
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
            token = await self._ensure_fresh_token()
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
