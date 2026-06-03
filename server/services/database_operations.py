from __future__ import annotations

import ast
import asyncio
import json
import re
import urllib.parse
from datetime import UTC, date, datetime, time
from time import perf_counter
from typing import Any
from urllib.parse import quote_plus, urlparse

import asyncpg
import certifi
import sqlglot
from bson import Binary, Code, DBRef, Decimal128, Int64, MaxKey, MinKey, ObjectId, Timestamp
from bson import json_util as bson_json_util
from bson import regex as bson_regex
from dateutil import parser as date_parser
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import errors as sqlglot_errors
from sqlglot import exp

from server.repositories.connections import ConnectionRepository
from server.services.dataset import DatasetService
from server.services.file_operations import DataFrameFileService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


class MongoConnector:
    WRITE_OPERATIONS = {
        "insertOne",
        "insertMany",
        "insert",
        "updateOne",
        "updateMany",
        "update",
        "replaceOne",
        "findOneAndUpdate",
        "findOneAndReplace",
        "findAndModify",
        "deleteOne",
        "deleteMany",
        "remove",
        "findOneAndDelete",
        "findOneAndRemove",
        "drop",
        "dropCollection",
        "dropDatabase",
        "createIndex",
        "createIndexes",
        "dropIndex",
        "dropIndexes",
        "reIndex",
        "ensureIndex",
        "rename",
        "renameCollection",
        "create",
        "createCollection",
        "bulkWrite",
        "initializeOrderedBulkOp",
        "initializeUnorderedBulkOp",
        "save",
        "mapReduce",
    }

    READ_OPERATIONS = {
        "find",
        "findOne",
        "count",
        "countDocuments",
        "estimatedDocumentCount",
        "distinct",
        "aggregate",
    }

    ALLOWED_MODIFIERS = {
        "sort",
        "limit",
        "skip",
        "project",
        "collation",
        "hint",
    }

    BLOCKED_PIPELINE_OPERATORS = {"$out", "$merge"}

    def __init__(self, connection_obj: dict[str, Any]):
        self.connection_obj = connection_obj

    def _extract_parenthesized(self, text: str) -> tuple[str, str]:
        segment = text.lstrip()
        if not segment.startswith("("):
            raise ValueError("Expected '(' segment")

        depth = 0
        in_string: str | None = None
        escape = False
        start = None

        for index, char in enumerate(segment):
            if start is None:
                if char != "(":
                    raise ValueError("Invalid parenthesized segment")
                start = index
                depth = 1
                continue

            if escape:
                escape = False
                continue

            if char == "\\":
                escape = True
                continue

            if in_string:
                if char == in_string:
                    in_string = None
                continue

            if char in {'"', "'"}:
                in_string = char
                continue

            if char == "(":
                depth += 1
                continue

            if char == ")":
                depth -= 1
                if depth == 0 and start is not None:
                    return segment[start + 1 : index], segment[index + 1 :]

        raise ValueError("Unbalanced parentheses in query")

    def _pipeline_contains_blocked_stage(self, pipeline: Any) -> str | None:
        queue: list[Any] = [pipeline]
        while queue:
            current = queue.pop()
            if isinstance(current, dict):
                for key, value in current.items():
                    normalized = key.lower() if isinstance(key, str) else key
                    if normalized in self.BLOCKED_PIPELINE_OPERATORS:
                        return key
                    if isinstance(value, (dict, list)):
                        queue.append(value)
            elif isinstance(current, list):
                queue.extend(current)
        return None

    def _parse_query(self, query: str) -> dict[str, Any] | None:
        if not query:
            return None

        text = query.strip().rstrip(";")
        if not text:
            return None

        lowered = text.lower()
        write_pattern = r"\.\s*(" + "|".join(op.lower() for op in self.WRITE_OPERATIONS) + r")\s*\("
        if re.search(write_pattern, lowered):
            match = re.search(write_pattern, lowered)
            return {
                "collection": "unknown",
                "operation": match.group(1) if match else "write",
                "args": [],
                "is_write_operation": True,
            }

        prefix_match = re.match(r"^[A-Za-z_][\w]*\.", text)
        if not prefix_match:
            return None

        remainder = text[prefix_match.end() :].lstrip()
        collection_name: str | None = None

        if remainder.startswith("getCollection("):
            get_coll_match = re.match(r"getCollection\(['\"]([^'\"]+)['\"]\)\.", remainder)
            if not get_coll_match:
                return None
            collection_name = get_coll_match.group(1)
            remainder = remainder[get_coll_match.end() :].lstrip()
        else:
            coll_match = re.match(r"([A-Za-z_][\w]*)\.", remainder)
            if not coll_match:
                return None
            collection_name = coll_match.group(1)
            remainder = remainder[coll_match.end() :].lstrip()

        op_match = re.match(r"([A-Za-z_][\w]*)", remainder)
        if not op_match:
            return None
        operation = op_match.group(1)
        remainder = remainder[op_match.end() :]

        if operation in self.WRITE_OPERATIONS:
            return {
                "collection": collection_name,
                "operation": operation,
                "args": [],
                "is_write_operation": True,
            }

        if operation not in self.READ_OPERATIONS:
            return {"collection": collection_name, "operation": operation, "args": [], "error": "Unsupported operation"}

        try:
            args_segment, chain_segment = self._extract_parenthesized(remainder)
        except ValueError:
            return None

        try:
            args = self._parse_arguments(args_segment) if args_segment.strip() else []
        except Exception:
            return None

        modifiers: list[dict[str, Any]] = []
        chain = chain_segment.strip()
        while chain:
            if not chain.startswith("."):
                return None
            chain = chain[1:].lstrip()
            modifier_match = re.match(r"([A-Za-z_][\w]*)", chain)
            if not modifier_match:
                return None
            modifier = modifier_match.group(1)
            chain = chain[modifier_match.end() :]

            if modifier in self.WRITE_OPERATIONS:
                return {
                    "collection": collection_name,
                    "operation": modifier,
                    "args": [],
                    "is_write_operation": True,
                }

            if modifier not in self.ALLOWED_MODIFIERS:
                return {
                    "collection": collection_name,
                    "operation": modifier,
                    "args": [],
                    "error": f"Unsupported modifier: {modifier}",
                }

            try:
                modifier_args_segment, chain = self._extract_parenthesized(chain.lstrip())
            except ValueError:
                return None

            try:
                modifier_args = self._parse_arguments(modifier_args_segment) if modifier_args_segment.strip() else []
            except Exception:
                return None

            if len(modifier_args) <= 1:
                value = modifier_args[0] if modifier_args else None
            else:
                value = modifier_args
            modifiers.append({"method": modifier, "args": value})

            chain = chain.strip()

        if operation == "aggregate" and args:
            pipeline = args[0] if isinstance(args, list) else args
            if isinstance(pipeline, list):
                blocked = self._pipeline_contains_blocked_stage(pipeline)
                if blocked:
                    return {
                        "collection": collection_name,
                        "operation": operation,
                        "args": [],
                        "is_write_operation": True,
                        "blocked_stage": blocked,
                    }

        return {
            "collection": collection_name,
            "operation": operation,
            "args": args,
            "modifiers": modifiers,
        }

    def _parse_arguments(self, args_str: str) -> list[Any]:
        if not args_str.strip():
            return []
        processed_args = self._preprocess_mongo_syntax(args_str)
        try:
            parsed = ast.literal_eval(f"[{processed_args}]")
            return self._post_process_parsed_args(parsed)
        except (ValueError, SyntaxError):
            try:
                json_str = self._convert_to_json(processed_args)
                parsed = json.loads(f"[{json_str}]")
                return self._post_process_parsed_args(parsed)
            except json.JSONDecodeError:
                raise

    def _preprocess_mongo_syntax(self, args_str: str) -> str:
        processed = args_str

        # Normalize all quote variations to straight quotes
        # Handle curly/smart quotes from copy-paste or text editors
        quote_map = {
            "\u201c": '"',  # " LEFT DOUBLE QUOTATION MARK
            "\u201d": '"',  # " RIGHT DOUBLE QUOTATION MARK
            "\u2018": "'",  # ' LEFT SINGLE QUOTATION MARK
            "\u2019": "'",  # ' RIGHT SINGLE QUOTATION MARK
            "\u201e": '"',  # „ DOUBLE LOW-9 QUOTATION MARK
            "\u201a": "'",  # ‚ SINGLE LOW-9 QUOTATION MARK
            "\u00ab": '"',  # « LEFT-POINTING DOUBLE ANGLE QUOTATION MARK
            "\u00bb": '"',  # » RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK
            "\u2039": "'",  # ‹ SINGLE LEFT-POINTING ANGLE QUOTATION MARK
            "\u203a": "'",  # › SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
        }
        for smart_quote, straight_quote in quote_map.items():
            processed = processed.replace(smart_quote, straight_quote)

        processed = re.sub(r"\btrue\b", "True", processed)
        processed = re.sub(r"\bfalse\b", "False", processed)
        processed = re.sub(r"\bnull\b", "None", processed)
        processed = re.sub(r"ObjectId\((['\"])([^'\"]+)\1\)", r"{'__oid__': '\2'}", processed)
        processed = re.sub(r"ObjectId\(\)", r"{'__oid__': 'new'}", processed)
        processed = re.sub(r"new Date\(\)", r"{'__date__': 'now'}", processed)
        processed = re.sub(r"new Date\((['\"])([^'\"]+)\1\)", r"{'__date__': '\2'}", processed)
        processed = re.sub(r"ISODate\((['\"])([^'\"]+)\1\)", r"{'__date__': '\2'}", processed)

        def regex_replacer(match):
            pattern = match.group(1)
            flags = match.group(2) if match.group(2) else ""
            return f"{{'__regex__': '{pattern}', '__flags__': '{flags}'}}"

        processed = re.sub(r"/([^/]+)/([igms]*)", regex_replacer, processed)
        return processed

    def _post_process_parsed_args(self, args: list[Any]) -> list[Any]:
        def convert_value(value):
            if isinstance(value, dict):
                if "__oid__" in value:
                    if value["__oid__"] == "new":
                        return ObjectId()
                    return ObjectId(value["__oid__"])

                # Extended JSON emitted by bson.json_util.dumps for ObjectId
                if "$oid" in value and len(value) == 1:
                    oid_value = value["$oid"]
                    if isinstance(oid_value, str):
                        try:
                            return ObjectId(oid_value)
                        except Exception:
                            return oid_value
                    return oid_value

                if "__date__" in value:
                    if value["__date__"] == "now":
                        return datetime.now()
                    try:
                        return date_parser.parse(value["__date__"])
                    except:
                        return value["__date__"]

                if "$date" in value:
                    date_val = value["$date"]
                    if isinstance(date_val, str):
                        try:
                            return date_parser.parse(date_val)
                        except:
                            return value
                    elif isinstance(date_val, dict) and "$numberLong" in date_val:
                        try:
                            return datetime.fromtimestamp(int(date_val["$numberLong"]) / 1000)
                        except:
                            return value
                    elif isinstance(date_val, (int, float)):
                        try:
                            return datetime.fromtimestamp(date_val / 1000)
                        except:
                            return value
                    return value

                # Extended JSON emitted by bson.json_util.dumps for regex values
                if "$regularExpression" in value and len(value) == 1:
                    regex_payload = value["$regularExpression"]
                    if isinstance(regex_payload, dict):
                        pattern = str(regex_payload.get("pattern", ""))
                        flags = str(regex_payload.get("options", ""))
                        regex_flags = 0
                        if "i" in flags:
                            regex_flags |= re.IGNORECASE
                        if "m" in flags:
                            regex_flags |= re.MULTILINE
                        if "s" in flags:
                            regex_flags |= re.DOTALL
                        return bson_regex.Regex(pattern, regex_flags)

                if "__regex__" in value:
                    pattern = value["__regex__"]
                    flags = value.get("__flags__", "")
                    regex_flags = 0
                    if "i" in flags:
                        regex_flags |= re.IGNORECASE
                    if "m" in flags:
                        regex_flags |= re.MULTILINE
                    if "s" in flags:
                        regex_flags |= re.DOTALL
                    return bson_regex.Regex(pattern, regex_flags)

                return {k: convert_value(v) for k, v in value.items()}

            elif isinstance(value, list):
                return [convert_value(v) for v in value]

            return value

        return [convert_value(arg) for arg in args]

    def _convert_to_json(self, args_str: str) -> str:
        result = re.sub(r"(\$\w+)\s*:\s*", r'"\1": ', args_str)
        result = re.sub(r"(?<!\$)([a-zA-Z_]\w*)\s*:\s*", r'"\1": ', result)
        result = re.sub(r"'([^']*)'", r'"\1"', result)
        result = re.sub(r",\s*([}\]])", r"\1", result)
        result = re.sub(r"\bTrue\b", "true", result)
        result = re.sub(r"\bFalse\b", "false", result)
        result = re.sub(r"\bNone\b", "null", result)
        return result


class AsyncMongoConnector:
    """Async MongoDB connector using motor."""

    def __init__(self, connection_obj: dict[str, Any]):
        self.connection_obj = connection_obj
        self.client: AsyncIOMotorClient | None = None
        self.database_name: str | None = None

    async def connect(self):
        """Establish async MongoDB connection."""
        try:
            conn_str = self.connection_obj.get("connection_string")
            if not conn_str:
                raise ValueError("No connection string provided for MongoDB")

            # Normalize connection string for Docker environment
            conn_str = AsyncSQLConnector._normalize_mongo_connection_string(conn_str)

            # Parse and encode password if needed
            parsed_uri = urlparse(conn_str)
            if parsed_uri.password:
                encoded_password = quote_plus(parsed_uri.password)
                if parsed_uri.username:
                    conn_str = conn_str.replace(
                        f"{parsed_uri.username}:{parsed_uri.password}@", f"{parsed_uri.username}:{encoded_password}@"
                    )

            client_options = {
                "serverSelectionTimeoutMS": 30000,
                "connectTimeoutMS": 30000,
                "socketTimeoutMS": 30000,
                "retryWrites": True,
                "retryReads": True,
                "maxPoolSize": 10,
                "minPoolSize": 1,
                "maxIdleTimeMS": 120000,
                "waitQueueTimeoutMS": 30000,
                "heartbeatFrequencyMS": 10000,
            }

            if "mongodb+srv://" in conn_str or "mongodb.net" in conn_str:
                client_options.update(
                    {
                        "tls": True,
                        "tlsCAFile": certifi.where(),
                        "tlsAllowInvalidCertificates": False,
                        "tlsAllowInvalidHostnames": False,
                        "directConnection": False,
                        "w": "majority",
                        "journal": True,
                        "readPreference": "primaryPreferred",
                        "compressors": ["snappy", "zlib", "zstd"],
                    }
                )
            elif "localhost" in conn_str or "127.0.0.1" in conn_str or "host.docker.internal" in conn_str:
                client_options.update({"tls": False, "directConnection": True})

            try:
                self.client = AsyncIOMotorClient(conn_str, **client_options)

                # Test connection
                try:
                    await self.client.admin.command("ping")
                except Exception as ping_error:
                    if "No replica set members match selector" in str(ping_error):
                        pass  # Read operations should still work
                    else:
                        raise ConnectionError(f"MongoDB connection test failed: {str(ping_error)}")

                # Extract database name
                parsed = urlparse(conn_str)
                if parsed.path and parsed.path != "/":
                    self.database_name = parsed.path.lstrip("/")
                else:
                    self.database_name = self.connection_obj.get("database")
                    if not self.database_name:
                        raise ValueError("Database name must be specified")

            except ConnectionError:
                raise
            except Exception as e:
                raise ConnectionError(f"Cannot connect to MongoDB: {str(e)}")
        except (ValueError, ConnectionError):
            raise
        except Exception as e:
            logger.error(
                f"Failed to connect to MongoDB: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "AsyncMongoConnector.connect",
                    "database_name": self.database_name if hasattr(self, "database_name") else None,
                },
            )
            raise ConnectionError(f"Cannot connect to MongoDB: {str(e)}")

    def get_database(self):
        """Get async database instance."""
        if not self.client or not self.database_name:
            raise RuntimeError("MongoDB not connected")
        return self.client[self.database_name]

    async def close(self):
        """Close async MongoDB connection."""
        if self.client:
            self.client.close()
            self.client = None

    def _apply_cursor_modifiers(self, cursor: Any, modifiers: list[dict[str, Any]]) -> tuple[Any, int | None]:
        modifier_limit: int | None = None

        for modifier in modifiers:
            method = modifier.get("method")
            value = modifier.get("args")

            if method == "sort":
                if isinstance(value, dict):
                    sort_value = list(value.items())
                elif isinstance(value, (list, tuple)):
                    sort_value = []
                    for entry in value:
                        if isinstance(entry, dict):
                            if len(entry) != 1:
                                raise ValueError("sort modifier expects single-field documents")
                            sort_value.extend(entry.items())
                        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                            sort_value.append((entry[0], int(entry[1])))
                        else:
                            raise ValueError("Unsupported sort modifier format")
                else:
                    raise ValueError("sort modifier expects dict or list of pairs")
                cursor = cursor.sort(sort_value)
            elif method == "limit":
                if value is None:
                    raise ValueError("limit modifier requires a value")
                modifier_limit = self._ensure_positive_int(value)
                cursor = cursor.limit(modifier_limit)
            elif method == "skip":
                if value is None:
                    raise ValueError("skip modifier requires a value")
                cursor = cursor.skip(self._ensure_positive_int(value, allow_zero=True))
            elif method == "project":
                if not isinstance(value, dict):
                    raise ValueError("project modifier expects document")
                cursor = cursor.project(value)
            elif method == "collation":
                if not isinstance(value, dict):
                    raise ValueError("collation modifier expects document")
                cursor = cursor.collation(value)
            elif method == "hint":
                if value is None:
                    raise ValueError("hint modifier requires value")
                cursor = cursor.hint(value)
            else:  # pragma: no cover - guarded earlier
                raise ValueError(f"Unsupported modifier: {method}")

        return cursor, modifier_limit

    @staticmethod
    def _ensure_positive_int(value: Any, *, allow_zero: bool = False) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Numeric value required")
        int_value = int(value)
        if int_value < 0 or (int_value == 0 and not allow_zero):
            raise ValueError("Value must be positive")
        return int_value

    async def execute_query(
        self,
        collection_name: str,
        operation: str,
        args: Any,
        limit: int | None = None,
        modifiers: list[dict[str, Any]] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        start_time = perf_counter()

        try:
            async with asyncio.timeout(timeout):
                db = self.get_database()
                collection = db[collection_name]

                total_count = None
                result = None
                applied_limit = None
                modifiers = modifiers or []

                if operation == "find":
                    arg_list = list(args) if isinstance(args, (list, tuple)) else ([args] if args else [])
                    filter_doc = arg_list[0] if arg_list else {}
                    total_count = await collection.count_documents(filter_doc)
                    cursor = collection.find(*arg_list)

                    modifier_limit = None
                    if modifiers:
                        cursor, modifier_limit = self._apply_cursor_modifiers(cursor, modifiers)

                    effective_limit = limit
                    if modifier_limit is not None:
                        effective_limit = min(modifier_limit, effective_limit) if effective_limit else modifier_limit

                    if effective_limit:
                        cursor = cursor.limit(effective_limit)
                        applied_limit = effective_limit

                    fetch_length = applied_limit if applied_limit else None
                    result = await cursor.to_list(length=fetch_length)
                elif operation == "findOne":
                    doc = await collection.find_one(*args if isinstance(args, (list, tuple)) else [args])
                    result = [doc] if doc else []  # Wrap in list for consistency with find operation
                    total_count = 1 if doc else 0
                elif operation == "countDocuments":
                    result = await collection.count_documents(*args if isinstance(args, (list, tuple)) else [args])
                    total_count = result
                elif operation == "count":
                    filter_doc = args[0] if isinstance(args, (list, tuple)) and args else (args or {})
                    result = await collection.count_documents(filter_doc)
                    total_count = result
                elif operation == "estimatedDocumentCount":
                    result = await collection.estimated_document_count()
                    total_count = result
                elif operation == "distinct":
                    result = await collection.distinct(*args if isinstance(args, (list, tuple)) else args)
                    total_count = len(result) if isinstance(result, list) else 0
                elif operation == "aggregate":
                    pipeline = args[0] if isinstance(args, (list, tuple)) and args else args
                    options = {}
                    if isinstance(args, (list, tuple)) and len(args) > 1:
                        for option in args[1:]:
                            if isinstance(option, dict):
                                options.update(option)
                    if not isinstance(pipeline, list):
                        raise ValueError("Aggregate pipeline must be a list")

                    pipeline_to_run = list(pipeline)
                    if limit:
                        pipeline_to_run.append({"$limit": limit})
                        applied_limit = limit

                    cursor = collection.aggregate(pipeline_to_run, **options)
                    result = await cursor.to_list(length=None)
                    total_count = len(result) if applied_limit is None else None
                else:
                    raise ValueError(f"Unsupported operation: {operation}")

                execution_time = perf_counter() - start_time
                safe_result = DatabaseOperationsService.serialize_bson(result)
                return {
                    "success": True,
                    "collection": collection_name,
                    "operation": operation,
                    "result": safe_result,
                    "total_count": total_count,
                    "returned_count": len(result) if isinstance(result, list) else (1 if result else 0),
                    "limited": applied_limit is not None and isinstance(result, list) and len(result) == applied_limit,
                    "execution_time_seconds": round(execution_time, 2),
                }

        except TimeoutError:
            execution_time = perf_counter() - start_time
            logger.warning(
                f"Async MongoDB query timeout after {execution_time:.2f}s",
                posthog_context={
                    "function": "AsyncMongoConnector.execute_query",
                    "collection": collection_name,
                    "operation": operation,
                    "database_name": self.database_name,
                    "timeout_seconds": timeout,
                    "execution_time_seconds": round(execution_time, 2),
                },
            )
            return {
                "success": False,
                "timeout": True,
                "error": f"Query execution exceeded timeout of {timeout} seconds",
                "timeout_seconds": timeout,
                "execution_time_seconds": round(execution_time, 2),
                "collection": collection_name,
                "operation": operation,
            }
        except Exception as e:
            execution_time = perf_counter() - start_time
            logger.error(
                f"Async MongoDB query error: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "AsyncMongoConnector.execute_query",
                    "collection": collection_name,
                    "operation": operation,
                    "database_name": self.database_name,
                    "execution_time_seconds": round(execution_time, 2),
                },
            )
            raise


class AsyncSQLConnector:
    """Unified async SQL connector using SQLAlchemy for PostgreSQL, MySQL, SQLite, and MSSQL."""

    # Dialect mapping for different SQL databases
    DIALECT_MAP = {
        "pg": "postgresql+asyncpg",
        "mysql": "mysql+aiomysql",
        "sqlite": "sqlite+aiosqlite",
        "mssql": "mssql+aioodbc",
    }

    def __init__(self, connection_obj: dict[str, Any], db_type: str = "pg"):
        self.connection_obj = connection_obj
        self.db_type = db_type
        self.engine = None

    @staticmethod
    def _normalize_sqlite_path(database_path: str) -> str:
        """
        Normalize SQLite database path to work in Docker environments.
        Converts absolute host paths to Docker container paths.

        Examples:
            /Users/dev/byaan/server/.data/app.db -> /app/server/.data/app.db
            server/.data/app.db -> /app/server/.data/app.db
            /app/server/.data/app.db -> /app/server/.data/app.db (unchanged)
        """
        import os

        if not AsyncSQLConnector._running_inside_container():
            # Not in Docker, return path as-is
            return database_path

        # If path is already a Docker path (starts with /app), return as-is
        if database_path.startswith("/app/"):
            return database_path

        # Handle special case for in-memory database
        if database_path in (":memory:", ""):
            return database_path

        # If it's an absolute path, try to extract the project-relative portion
        if os.path.isabs(database_path):
            # Look for common project markers (server, client, etc.)
            parts = database_path.split(os.sep)

            # Try to find where the project structure starts
            # Look for "server" in the path and take everything from there
            if "server" in parts:
                server_index = parts.index("server")
                relative_parts = parts[server_index:]
                normalized = "/app/" + "/".join(relative_parts)
                return normalized

            # If no server directory found, check if path points to a relative location
            # This handles cases where the path might be project-relative already
            return database_path

        # It's a relative path - normalize it to /app/ prefix
        # Remove leading ./ if present
        clean_path = database_path.lstrip("./")

        # Ensure it starts with /app/
        if not clean_path.startswith("app/"):
            return f"/app/{clean_path}"
        else:
            return f"/{clean_path}"

    @staticmethod
    def _running_inside_container() -> bool:
        """Detect if code executes inside a containerized environment."""
        import os

        if os.environ.get("IN_DOCKER") == "1" or os.environ.get("RUNNING_IN_DOCKER") == "1":
            return True

        return os.path.exists("/.dockerenv") or os.path.exists("/app")

    @staticmethod
    def _find_host_gateway_alias() -> str | None:
        """Return a host alias that resolves to the Docker host, if available."""
        import os
        import socket

        candidates = [
            os.environ.get("DOCKER_HOST_GATEWAY"),
            os.environ.get("HOST_GATEWAY"),
            os.environ.get("HOST_DOCKER_INTERNAL"),
            "host.docker.internal",
            "gateway.docker.internal",
            "docker.for.mac.host.internal",
            "docker.for.win.host.internal",
            "host-gateway",
        ]

        for alias in candidates:
            if not alias:
                continue
            try:
                socket.gethostbyname(alias)
                return alias
            except socket.gaierror:
                continue

        return None

    @classmethod
    def _normalize_sql_host(cls, host: str) -> str:
        """Translate local hosts to Docker host aliases when running in containers."""
        if not host:
            return host

        normalized = host.strip()
        if normalized not in {"localhost", "127.0.0.1"}:
            return normalized

        if not cls._running_inside_container():
            return normalized

        gateway_alias = cls._find_host_gateway_alias()
        if gateway_alias:
            return gateway_alias

        # Fall back to plain localhost so compose overrides (if any) can take effect
        if normalized == "127.0.0.1":
            return "localhost"

        return normalized

    @classmethod
    def _normalize_mongo_connection_string(cls, conn_str: str) -> str:
        """Normalize MongoDB connection string to work inside Docker containers."""
        if not conn_str:
            return conn_str

        # Don't normalize cloud/SRV connections
        if "mongodb+srv://" in conn_str or "mongodb.net" in conn_str:
            return conn_str

        # Check if we're in a container
        if not cls._running_inside_container():
            return conn_str

        # Parse the connection string
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(conn_str)

        # Extract host from netloc (which may include port)
        netloc = parsed.netloc
        if not netloc:
            return conn_str

        # Handle auth in netloc (user:pass@host:port)
        if "@" in netloc:
            auth_part, host_part = netloc.rsplit("@", 1)
        else:
            auth_part = None
            host_part = netloc

        # Extract host and port
        if ":" in host_part:
            host, port = host_part.rsplit(":", 1)
        else:
            host = host_part
            port = None

        # Normalize the host
        if host in {"localhost", "127.0.0.1"}:
            gateway_alias = cls._find_host_gateway_alias()
            if gateway_alias:
                host = gateway_alias
            elif host == "127.0.0.1":
                host = "localhost"

        # Rebuild host_part
        if port:
            new_host_part = f"{host}:{port}"
        else:
            new_host_part = host

        # Rebuild netloc
        if auth_part:
            new_netloc = f"{auth_part}@{new_host_part}"
        else:
            new_netloc = new_host_part

        # Rebuild the URI
        normalized = urlunparse((parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

        return normalized

    def _build_connection_url(self) -> str:
        """Build SQLAlchemy connection URL based on db_type."""
        dialect = self.DIALECT_MAP.get(self.db_type)
        if not dialect:
            raise ValueError(f"Unsupported database type: {self.db_type}")

        # Handle connection string if provided
        if "connection_string" in self.connection_obj:
            conn_str = self.connection_obj["connection_string"]
            # Replace dialect prefix if needed
            if "://" in conn_str:
                conn_str = dialect + "://" + conn_str.split("://", 1)[1]
            return conn_str

        # Build connection URL from components
        if self.db_type == "sqlite":
            database = self.connection_obj.get("database", ":memory:")
            # Normalize SQLite path for Docker environments
            database = self._normalize_sqlite_path(database)
            return f"{dialect}:///{database}"

        host = self.connection_obj.get("host", "localhost")
        host = self._normalize_sql_host(host)
        port = self.connection_obj.get("port")
        database = self.connection_obj.get("database")
        user = self.connection_obj.get("user") or self.connection_obj.get("username")
        password = self.connection_obj.get("password", "")

        # Set default ports
        if not port:
            port = {"pg": 5432, "mysql": 3306, "mssql": 1433}.get(self.db_type, 5432)

        if not database:
            raise ValueError(f"Database name is required for {self.db_type}")

        # URL encode credentials
        if user:
            user = urllib.parse.quote_plus(user)
        if password:
            password = urllib.parse.quote_plus(password)

        # Build URL
        if user and password:
            base_url = f"{dialect}://{user}:{password}@{host}:{port}/{database}"
        elif user:
            base_url = f"{dialect}://{user}@{host}:{port}/{database}"
        else:
            base_url = f"{dialect}://{host}:{port}/{database}"

        # Add MSSQL-specific ODBC parameters
        if self.db_type == "mssql":
            base_url += "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes&Encrypt=no"

        return base_url

    async def connect(self):
        """Create async SQLAlchemy engine."""
        try:
            from sqlalchemy.ext.asyncio import create_async_engine

            connection_url = self._build_connection_url()

            # Create async engine
            self.engine = create_async_engine(
                connection_url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=3600,
            )

            # Test connection
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))

        except Exception as e:
            logger.error(
                f"Failed to connect to {self.db_type.upper()}: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "AsyncSQLConnector.connect",
                    "db_type": self.db_type,
                    "database": self.connection_obj.get("database") if self.connection_obj else None,
                },
            )
            raise ConnectionError(f"Cannot connect to {self.db_type.upper()}: {str(e)}")

    def _apply_limit_to_query(self, query: str, limit: int) -> str:
        """Apply LIMIT clause to query based on database type."""
        query_upper = query.upper().strip()

        # Check if limit already exists
        if "LIMIT" in query_upper or "TOP" in query_upper or "FETCH" in query_upper:
            return query

        if self.db_type == "mssql":
            # MSSQL uses TOP syntax
            # Insert TOP right after SELECT
            if query_upper.startswith("SELECT"):
                # Handle SELECT DISTINCT
                if query_upper.startswith("SELECT DISTINCT"):
                    return query[:15] + f" TOP {limit}" + query[15:]
                else:
                    return query[:6] + f" TOP {limit}" + query[6:]
            return query
        else:
            # PostgreSQL, MySQL, SQLite use LIMIT
            return f"{query} LIMIT {limit}"

    async def execute_query(
        self,
        query: str,
        limit: int = None,
        timeout: int = 30,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.engine:
            raise RuntimeError(f"{self.db_type.upper()} not connected")

        start_time = perf_counter()

        try:
            async with asyncio.timeout(timeout):
                async with self.engine.connect() as conn:
                    total_count = None

                    # Get total count for SELECT queries with limit
                    if limit and query.strip().upper().startswith("SELECT"):
                        try:
                            count_query = f"SELECT COUNT(*) FROM ({query}) AS count_subquery"
                            count_result = await conn.execute(text(count_query), params or {})
                            total_count = count_result.scalar()
                        except Exception:
                            # If count query fails, continue without total count
                            pass

                        # Apply limit based on database type
                        query = self._apply_limit_to_query(query, limit)

                    # Execute query
                    result_proxy = await conn.execute(text(query), params or {})
                    rows = result_proxy.fetchall()

                    # Convert rows to dicts
                    if rows and hasattr(result_proxy, "keys"):
                        columns = result_proxy.keys()
                        result = [dict(zip(columns, row, strict=False)) for row in rows]
                    else:
                        result = []

                    execution_time = perf_counter() - start_time

                    # Serialize result to handle non-JSON-serializable types
                    safe_result = DatabaseOperationsService.serialize_sql_result(result)

                    return {
                        "success": True,
                        "result": safe_result,
                        "query": query,
                        "total_count": total_count,
                        "returned_count": len(result),
                        "limited": limit is not None
                        and total_count is not None
                        and len(result) == limit
                        and total_count > limit,
                        "execution_time_seconds": round(execution_time, 2),
                    }

        except TimeoutError:
            execution_time = perf_counter() - start_time
            logger.warning(
                f"Async {self.db_type.upper()} query timeout after {execution_time:.2f}s",
                posthog_context={
                    "function": "AsyncSQLConnector.execute_query",
                    "db_type": self.db_type,
                    "query": query[:200] if query else None,
                    "timeout_seconds": timeout,
                    "execution_time_seconds": round(execution_time, 2),
                },
            )
            return {
                "success": False,
                "timeout": True,
                "error": f"Query execution exceeded timeout of {timeout} seconds",
                "timeout_seconds": timeout,
                "execution_time_seconds": round(execution_time, 2),
                "db_type": self.db_type,
            }
        except Exception as e:
            execution_time = perf_counter() - start_time
            logger.error(
                f"Async {self.db_type.upper()} query error: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "AsyncSQLConnector.execute_query",
                    "db_type": self.db_type,
                    "query": query[:200] if query else None,
                    "execution_time_seconds": round(execution_time, 2),
                },
            )
            return {
                "success": False,
                "error": str(e),
                "query": query,
                "execution_time_seconds": round(execution_time, 2),
            }

    async def close(self):
        """Close SQLAlchemy engine."""
        if self.engine:
            await self.engine.dispose()
            self.engine = None


# Keep AsyncPostgresConnector as alias for backward compatibility
class AsyncPostgresConnector(AsyncSQLConnector):
    """Backward compatibility alias for AsyncPostgresConnector."""

    def __init__(self, connection_obj: dict[str, Any]):
        super().__init__(connection_obj, db_type="pg")


class AsyncDynamoDBConnector:
    """Async DynamoDB connector using boto3 with asyncio.to_thread."""

    ALLOWED_READ_OPERATIONS = {"get_item", "query", "scan", "batch_get_item", "describe_table"}

    def __init__(self, connection_obj: dict[str, Any]):
        self.connection_obj = connection_obj
        self.client = None
        self.query_mode: str = connection_obj.get("query_mode", "partiql")

    async def connect(self):
        import boto3

        kwargs: dict[str, Any] = {
            "region_name": self.connection_obj.get("region"),
            "aws_access_key_id": self.connection_obj.get("access_key_id"),
            "aws_secret_access_key": self.connection_obj.get("secret_access_key"),
        }
        endpoint_url = self.connection_obj.get("endpoint_url")
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        try:
            self.client = await asyncio.to_thread(lambda: boto3.client("dynamodb", **kwargs))
            await asyncio.to_thread(self.client.list_tables, Limit=1)
        except Exception as e:
            raise ConnectionError(f"Cannot connect to DynamoDB: {str(e)}")

    @staticmethod
    def _deserialize_items(items: list[dict]) -> list[dict]:
        from boto3.dynamodb.types import TypeDeserializer

        deserializer = TypeDeserializer()
        result = []
        for item in items:
            deserialized = {}
            for key, value in item.items():
                try:
                    deserialized[key] = deserializer.deserialize(value)
                except Exception:
                    deserialized[key] = str(value)
            result.append(deserialized)
        return result

    @staticmethod
    def _convert_decimals(obj: Any) -> Any:
        from decimal import Decimal

        if isinstance(obj, Decimal):
            return float(obj) if obj % 1 else int(obj)
        if isinstance(obj, dict):
            return {k: AsyncDynamoDBConnector._convert_decimals(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [AsyncDynamoDBConnector._convert_decimals(i) for i in obj]
        if isinstance(obj, set):
            return [AsyncDynamoDBConnector._convert_decimals(i) for i in obj]
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return obj

    async def execute_partiql_query(self, statement: str, limit: int = 50, timeout: int = 30) -> dict[str, Any]:
        start_time = perf_counter()
        try:
            async with asyncio.timeout(timeout):
                params: dict[str, Any] = {"Statement": statement}
                if limit:
                    params["Limit"] = limit

                response = await asyncio.to_thread(self.client.execute_statement, **params)
                raw_items = response.get("Items", [])
                items = self._deserialize_items(raw_items)
                items = self._convert_decimals(items)

                execution_time = perf_counter() - start_time
                return {
                    "success": True,
                    "result": items,
                    "returned_count": len(items),
                    "execution_time_seconds": round(execution_time, 2),
                }
        except TimeoutError:
            execution_time = perf_counter() - start_time
            return {
                "success": False,
                "timeout": True,
                "error": f"Query execution exceeded timeout of {timeout} seconds",
                "execution_time_seconds": round(execution_time, 2),
            }
        except Exception as e:
            execution_time = perf_counter() - start_time
            logger.error(f"DynamoDB PartiQL query error: {str(e)}", exc_info=True)
            raise

    async def execute_native_query(
        self, query_spec: dict[str, Any], limit: int = 50, timeout: int = 30
    ) -> dict[str, Any]:
        start_time = perf_counter()
        try:
            async with asyncio.timeout(timeout):
                operation = query_spec.get("operation", "").lower()
                table_name = query_spec.get("table", "")

                if operation == "scan":
                    params = {"TableName": table_name}
                    if limit:
                        params["Limit"] = limit
                    if query_spec.get("filter_expression"):
                        params["FilterExpression"] = query_spec["filter_expression"]
                    if query_spec.get("expression_attribute_names"):
                        params["ExpressionAttributeNames"] = query_spec["expression_attribute_names"]
                    if query_spec.get("expression_attribute_values"):
                        params["ExpressionAttributeValues"] = query_spec["expression_attribute_values"]
                    if query_spec.get("projection_expression"):
                        params["ProjectionExpression"] = query_spec["projection_expression"]
                    response = await asyncio.to_thread(self.client.scan, **params)
                    raw_items = response.get("Items", [])

                elif operation == "query":
                    params = {"TableName": table_name}
                    if limit:
                        params["Limit"] = limit
                    if query_spec.get("key_condition_expression"):
                        params["KeyConditionExpression"] = query_spec["key_condition_expression"]
                    if query_spec.get("filter_expression"):
                        params["FilterExpression"] = query_spec["filter_expression"]
                    if query_spec.get("expression_attribute_names"):
                        params["ExpressionAttributeNames"] = query_spec["expression_attribute_names"]
                    if query_spec.get("expression_attribute_values"):
                        params["ExpressionAttributeValues"] = query_spec["expression_attribute_values"]
                    if query_spec.get("projection_expression"):
                        params["ProjectionExpression"] = query_spec["projection_expression"]
                    if query_spec.get("index_name"):
                        params["IndexName"] = query_spec["index_name"]
                    if query_spec.get("scan_index_forward") is not None:
                        params["ScanIndexForward"] = query_spec["scan_index_forward"]
                    response = await asyncio.to_thread(self.client.query, **params)
                    raw_items = response.get("Items", [])

                elif operation == "get_item":
                    params = {"TableName": table_name, "Key": query_spec.get("key", {})}
                    if query_spec.get("projection_expression"):
                        params["ProjectionExpression"] = query_spec["projection_expression"]
                    if query_spec.get("expression_attribute_names"):
                        params["ExpressionAttributeNames"] = query_spec["expression_attribute_names"]
                    response = await asyncio.to_thread(self.client.get_item, **params)
                    item = response.get("Item")
                    raw_items = [item] if item else []

                elif operation == "batch_get_item":
                    request_items = query_spec.get("request_items", {})
                    response = await asyncio.to_thread(self.client.batch_get_item, RequestItems=request_items)
                    raw_items = []
                    for table_items in response.get("Responses", {}).values():
                        raw_items.extend(table_items)

                elif operation == "describe_table":
                    response = await asyncio.to_thread(self.client.describe_table, TableName=table_name)
                    table_desc = response.get("Table", {})
                    execution_time = perf_counter() - start_time
                    return {
                        "success": True,
                        "result": [self._convert_decimals(table_desc)],
                        "returned_count": 1,
                        "execution_time_seconds": round(execution_time, 2),
                    }

                else:
                    raise ValueError(f"Unsupported DynamoDB operation: {operation}")

                items = self._deserialize_items(raw_items)
                items = self._convert_decimals(items)

                execution_time = perf_counter() - start_time
                return {
                    "success": True,
                    "result": items,
                    "returned_count": len(items),
                    "execution_time_seconds": round(execution_time, 2),
                }
        except TimeoutError:
            execution_time = perf_counter() - start_time
            return {
                "success": False,
                "timeout": True,
                "error": f"Query execution exceeded timeout of {timeout} seconds",
                "execution_time_seconds": round(execution_time, 2),
            }
        except Exception as e:
            execution_time = perf_counter() - start_time
            logger.error(f"DynamoDB native query error: {str(e)}", exc_info=True)
            raise

    async def close(self):
        self.client = None


class AsyncDatabaseService:
    """Service for async database operations."""

    # Connection pools cache
    _connection_pools: dict[str, Any] = {}
    _pool_lock = asyncio.Lock()

    @classmethod
    async def get_or_create_mongo_connector(
        cls, connection_id: str, connection_obj: dict[str, Any]
    ) -> AsyncMongoConnector:
        """Get or create cached MongoDB connector."""
        async with cls._pool_lock:
            if connection_id not in cls._connection_pools:
                connector = AsyncMongoConnector(connection_obj)
                await connector.connect()
                cls._connection_pools[connection_id] = connector
            return cls._connection_pools[connection_id]

    @classmethod
    async def get_or_create_sql_connector(
        cls, connection_id: str, connection_obj: dict[str, Any], db_type: str = "pg"
    ) -> AsyncSQLConnector:
        """Get or create cached SQL connector for any SQL database (PostgreSQL, MySQL, SQLite, MSSQL)."""
        async with cls._pool_lock:
            if connection_id not in cls._connection_pools:
                connector = AsyncSQLConnector(connection_obj, db_type=db_type)
                await connector.connect()
                cls._connection_pools[connection_id] = connector
            return cls._connection_pools[connection_id]

    @classmethod
    async def get_or_create_dynamodb_connector(
        cls, connection_id: str, connection_obj: dict[str, Any]
    ) -> AsyncDynamoDBConnector:
        """Get or create cached DynamoDB connector."""
        async with cls._pool_lock:
            if connection_id not in cls._connection_pools:
                connector = AsyncDynamoDBConnector(connection_obj)
                await connector.connect()
                cls._connection_pools[connection_id] = connector
            return cls._connection_pools[connection_id]

    @classmethod
    async def get_or_create_databricks_connector(cls, connection_id: str, connection_obj: dict[str, Any]):
        """Get or create cached Databricks connector."""
        from server.services.databricks_connector import AsyncDatabricksConnector

        async with cls._pool_lock:
            if connection_id not in cls._connection_pools:
                connector = AsyncDatabricksConnector(connection_obj)
                await connector.connect()
                cls._connection_pools[connection_id] = connector
            return cls._connection_pools[connection_id]

    @classmethod
    async def get_or_create_postgres_connector(
        cls, connection_id: str, connection_obj: dict[str, Any]
    ) -> AsyncPostgresConnector:
        """Get or create cached PostgreSQL connector (backward compatibility)."""
        return await cls.get_or_create_sql_connector(connection_id, connection_obj, db_type="pg")

    @classmethod
    async def close_connection(cls, connection_id: str):
        """Close and remove connection from cache."""
        async with cls._pool_lock:
            if connection_id in cls._connection_pools:
                connector = cls._connection_pools[connection_id]
                if hasattr(connector, "close"):
                    await connector.close()
                del cls._connection_pools[connection_id]

    @classmethod
    async def close_all_connections(cls):
        """Close all cached connections."""
        async with cls._pool_lock:
            for connection_id, connector in cls._connection_pools.items():
                if hasattr(connector, "close"):
                    await connector.close()
            cls._connection_pools.clear()


class DatabaseOperationsService:
    DATE_ONLY_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    @staticmethod
    def _flatten_json_schema(schema: dict[str, Any], prefix: str = "") -> list[str]:
        """Flatten a JSON schema into a list of field paths with types."""
        lines: list[str] = []
        if not schema:
            return lines
        st = schema.get("type")
        types = st if isinstance(st, list) else [st] if isinstance(st, str) else []
        type_label = ",".join([t for t in types if t]) or "unknown"
        if prefix:
            lines.append(f"{prefix}: {type_label}")
        if "object" in types and isinstance(schema.get("properties"), dict):
            for k, v in schema["properties"].items():
                lines.extend(DatabaseOperationsService._flatten_json_schema(v, f"{prefix + '.' if prefix else ''}{k}"))
        if "array" in types and isinstance(schema.get("items"), dict):
            item_prefix = f"{prefix}[]" if prefix else "[]"
            lines.extend(DatabaseOperationsService._flatten_json_schema(schema["items"], item_prefix))
        return lines

    TYPE_ABBREV = {
        "objectid": "oid",
        "string": "str",
        "varchar": "str",
        "character varying": "str",
        "text": "str",
        "integer": "int",
        "bigint": "int",
        "smallint": "int",
        "serial": "int",
        "bigserial": "int",
        "timestamp": "ts",
        "timestamp without time zone": "ts",
        "timestamp with time zone": "ts",
        "timestamptz": "ts",
        "decimal": "dec",
        "numeric": "dec",
        "boolean": "bool",
        "double precision": "float",
        "real": "float",
        "double": "float",
        "jsonb": "json",
        "json": "json",
        "uuid": "uuid",
        "date": "date",
        "time": "time",
        "bytea": "bytes",
        "array": "arr",
    }

    @staticmethod
    def _abbreviate_type(full_type: str) -> str:
        normalized = full_type.lower().strip()
        base_type = normalized.split("(")[0].strip()
        return DatabaseOperationsService.TYPE_ABBREV.get(base_type, base_type)

    @staticmethod
    def _format_nested_compact(schema: dict[str, Any], max_depth: int = 2) -> str:
        if not schema:
            return "obj"

        schema_type = schema.get("type", "")
        types = schema_type if isinstance(schema_type, list) else [schema_type] if schema_type else []

        if "object" in types:
            if max_depth <= 0:
                return "obj"
            props = schema.get("properties", {})
            if not props:
                return "obj"
            parts = []
            for name, prop_schema in list(props.items())[:10]:
                nested_type = DatabaseOperationsService._format_nested_compact(prop_schema, max_depth - 1)
                parts.append(f"{name}({nested_type})")
            return "{" + ", ".join(parts) + "}"

        elif "array" in types:
            if max_depth <= 0:
                return "arr"
            items = schema.get("items", {})
            inner = DatabaseOperationsService._format_nested_compact(items, max_depth - 1)
            return f"[{inner}]"

        else:
            type_str = types[0] if types else "unknown"
            return DatabaseOperationsService._abbreviate_type(type_str)

    @staticmethod
    def _format_column_compact(col: dict[str, Any]) -> str:
        col_name = col.get("name", "unknown")
        col_type = DatabaseOperationsService._abbreviate_type(col.get("type", "unknown"))
        nullable = col.get("nullable", True)
        not_null_marker = "!" if not nullable else ""

        nested_schema = col.get("nested_schema")
        if nested_schema and col.get("type", "").lower() in ("json", "jsonb"):
            nested_str = DatabaseOperationsService._format_nested_compact(nested_schema)
            return f"{col_name}({col_type}){nested_str}{not_null_marker}"

        annotation = col.get("annotation", "")
        if annotation:
            return f"{col_name}({col_type}{not_null_marker}) [{annotation}]"

        return f"{col_name}({col_type}{not_null_marker})"

    @staticmethod
    def _format_mongo_compact(schema_data: dict[str, Any]) -> str:
        lines = []
        db_name = schema_data.get("database_name", "unknown")
        lines.append(f"[MongoDB:{db_name}]")

        for coll_name, coll_info in schema_data.get("schema", {}).items():
            if coll_info.get("redacted_table"):
                continue

            nested_schema = coll_info.get("nested_schema", {})
            redacted_fields = set(coll_info.get("redacted_fields", []))
            if nested_schema:
                fields = DatabaseOperationsService._format_nested_compact(nested_schema, max_depth=3)
                if fields.startswith("{") and fields.endswith("}"):
                    fields = fields[1:-1]
            else:
                sample_fields = coll_info.get("sample_fields", [])
                if sample_fields:
                    visible = [f for f in sample_fields if f not in redacted_fields]
                    fields = ", ".join(visible) if visible else "(empty)"
                else:
                    fields = "(empty)"

            desc = coll_info.get("description", "")
            line = f"{coll_name}: {fields}"
            if desc:
                line += f" // {desc}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _format_sql_compact(schema_data: dict[str, Any]) -> str:
        lines = []
        db_type = schema_data.get("datasource_type", "SQL").upper()
        if db_type == "PG":
            db_type = "PostgreSQL"
        db_name = schema_data.get("datasource_name", "unknown")
        lines.append(f"[{db_type}:{db_name}]")

        for table_name, table_info in schema_data.get("schema", {}).items():
            if table_info.get("redacted_table"):
                continue

            cols = []
            for col in table_info.get("columns", []):
                if col.get("redacted"):
                    continue
                col_str = DatabaseOperationsService._format_column_compact(col)
                cols.append(col_str)

            fks = table_info.get("foreign_keys", [])
            fk_str = ""
            if fks:
                fk_parts = []
                for fk in fks:
                    if isinstance(fk, dict):
                        fk_parts.append(f"{fk.get('column', '')}→{fk.get('ref_table', '')}")
                    elif isinstance(fk, str):
                        fk_parts.append(fk)
                if fk_parts:
                    fk_str = f" | FK:{','.join(fk_parts)}"

            desc = table_info.get("description", "")
            line = f"{table_name}: {', '.join(cols)}{fk_str}"
            if desc:
                line += f" // {desc}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _format_duckdb_compact(schema_data: dict[str, Any]) -> str:
        lines = []
        dataset_name = schema_data.get("database_name", "unknown")
        schema = schema_data.get("schema", {})

        if len(schema) == 1:
            table_name, table_info = next(iter(schema.items()))
            if table_info.get("redacted_table"):
                return ""

            row_count = table_info.get("row_count", 0)
            filename = table_info.get("filename", dataset_name)
            lines.append(f"[DuckDB:{filename} ({row_count} rows)]")

            cols = []
            for col in table_info.get("columns", []):
                if col.get("redacted"):
                    continue
                col_str = DatabaseOperationsService._format_column_compact(col)
                cols.append(col_str)

            desc = table_info.get("description", "")
            line = f"{table_name}: {', '.join(cols)}"
            if desc:
                line += f" // {desc}"
            lines.append(line)
        else:
            lines.append(f"[DuckDB:{dataset_name}]")
            for table_name, table_info in schema.items():
                if table_info.get("redacted_table"):
                    continue

                row_count = table_info.get("row_count", 0)
                cols = []
                for col in table_info.get("columns", []):
                    if col.get("redacted"):
                        continue
                    col_str = DatabaseOperationsService._format_column_compact(col)
                    cols.append(col_str)

                desc = table_info.get("description", "")
                line = f"{table_name} ({row_count} rows): {', '.join(cols)}"
                if desc:
                    line += f" // {desc}"
                lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _format_dynamodb_compact(schema_data: dict[str, Any]) -> str:
        lines = []
        db_name = schema_data.get("database_name", "DynamoDB")
        query_mode = schema_data.get("query_mode", "partiql")
        lines.append(f"[DynamoDB:{db_name} (mode:{query_mode})]")

        for table_name, table_info in schema_data.get("schema", {}).items():
            if table_info.get("redacted_table"):
                continue

            key_schema = table_info.get("key_schema", [])
            key_parts = []
            attr_types = {
                ad["AttributeName"]: ad["AttributeType"] for ad in table_info.get("attribute_definitions", [])
            }
            for ks in key_schema:
                name = ks.get("AttributeName", "")
                key_type = "PK" if ks.get("KeyType") == "HASH" else "SK"
                attr_type = attr_types.get(name, "?")
                key_parts.append(f"{name}({attr_type},{key_type})")

            sample_fields = table_info.get("sample_fields", [])
            nested = table_info.get("nested_schema", {})
            field_parts = []
            props = nested.get("properties", {})
            for field in sample_fields:
                field_type = props.get(field, {}).get("type", "?")
                if isinstance(field_type, list):
                    field_type = [t for t in field_type if t != "null"]
                    field_type = field_type[0] if len(field_type) == 1 else "/".join(field_type)
                field_parts.append(f"{field}:{field_type}")

            gsis = table_info.get("global_secondary_indexes", [])
            gsi_str = ""
            if gsis:
                gsi_names = [g.get("IndexName", "") for g in gsis]
                gsi_str = f" | GSI:{','.join(gsi_names)}"

            keys_str = ", ".join(key_parts)
            fields_str = ", ".join(field_parts)
            desc = table_info.get("description", "")
            line = f"{table_name}: Keys[{keys_str}] Fields[{fields_str}]{gsi_str}"
            if desc:
                line += f" // {desc}"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def format_schema_compact(schema_data: dict[str, Any], db_type: str) -> str:
        if not schema_data:
            return ""

        normalized_db_type = (db_type or "").lower()

        if normalized_db_type in ("duckdb", "file"):
            return DatabaseOperationsService._format_duckdb_compact(schema_data)
        elif normalized_db_type in ("sql", "pg", "mysql", "sqlite", "mssql", "postgresql", "databricks"):
            return DatabaseOperationsService._format_sql_compact(schema_data)
        elif normalized_db_type == "mongo":
            return DatabaseOperationsService._format_mongo_compact(schema_data)
        elif normalized_db_type == "dynamodb":
            return DatabaseOperationsService._format_dynamodb_compact(schema_data)

        return ""

    @staticmethod
    async def annotate_schema_with_user_annotations(
        datasource_id: str, schema_data: dict[str, Any], session: AsyncSession
    ) -> dict[str, Any]:
        """
        Annotate schema data with user annotations (table descriptions and column annotations).

        Returns annotated schema where:
        - Tables have 'description' field with table_description annotation
        - Columns have 'annotation' field with column_annotation
        """
        from server.repositories.datasource_annotations import DatasourceAnnotationRepository

        try:
            repo = DatasourceAnnotationRepository(session)
            annotations = await repo.get_all_by_datasource(datasource_id)

            if not annotations:
                return schema_data

            table_descriptions = {}
            column_annotations = {}
            column_redactions: set[tuple[str, str]] = set()
            table_redactions: set[str] = set()

            for ann in annotations:
                if ann.annotation_type == "table_description" and ann.column_name is None:
                    table_descriptions[ann.table_name] = ann.content
                elif ann.annotation_type == "column_annotation" and ann.column_name:
                    key = (ann.table_name, ann.column_name)
                    column_annotations[key] = ann.content
                elif ann.annotation_type == "column_redaction" and ann.column_name:
                    column_redactions.add((ann.table_name, ann.column_name))
                elif ann.annotation_type == "table_redaction" and ann.column_name is None:
                    table_redactions.add(ann.table_name)

            import copy

            annotated_schema = copy.deepcopy(schema_data)
            schema = annotated_schema.get("schema", {})

            for table_name, table_info in schema.items():
                if table_name in table_redactions:
                    table_info["redacted_table"] = True
                if table_name in table_descriptions:
                    table_info["description"] = table_descriptions[table_name]

                columns = table_info.get("columns", [])
                for col in columns:
                    col_name = col.get("name")
                    if col_name and (table_name, col_name) in column_annotations:
                        col["annotation"] = column_annotations[(table_name, col_name)]
                    if col_name and (table_name, col_name) in column_redactions:
                        col["redacted"] = True

                sample_fields = table_info.get("sample_fields", [])
                if sample_fields:
                    field_annotations = {}
                    redacted_fields = []
                    for field_name in sample_fields:
                        if (table_name, field_name) in column_annotations:
                            field_annotations[field_name] = column_annotations[(table_name, field_name)]
                        if (table_name, field_name) in column_redactions:
                            redacted_fields.append(field_name)
                    if field_annotations:
                        table_info["field_annotations"] = field_annotations
                    if redacted_fields:
                        table_info["redacted_fields"] = redacted_fields

            return annotated_schema

        except Exception as e:
            logger.error(f"Error annotating schema with user annotations: {e}")
            return schema_data

    @staticmethod
    def format_schema_for_prompt(schema_data: dict[str, Any], db_type: str) -> str:
        """Format database schema into a compact, token-efficient string for AI prompts."""
        if not schema_data:
            return ""

        return DatabaseOperationsService.format_schema_compact(schema_data, db_type)

    @staticmethod
    async def get_database_schema_by_notebook_id(
        session: AsyncSession, notebook_id: str, db_type: str | None = None
    ) -> dict[str, Any]:
        """
        Get database schema(s) for a notebook from datasets (connections or files).

        Returns:
            - If single dataset or db_type filter: Returns single schema dict
            - If multiple datasets: Returns dict with 'databases' key containing list of schemas
        """
        try:
            datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)

            if not datasets:
                raise ValueError(f"No datasets found for notebook {notebook_id}")

            if db_type:
                normalized_db = db_type.lower()
                if normalized_db in ("duckdb", "file"):
                    datasets = [d for d in datasets if d.type == "file"]
                else:
                    # For connection datasets, filter by connection type
                    connection_repo = ConnectionRepository(session)
                    filtered_datasets = []
                    for dataset in datasets:
                        if dataset.type == "connection":
                            connection = await connection_repo.get(dataset.connection_id)
                            if connection and connection.type == db_type:
                                filtered_datasets.append(dataset)
                    datasets = filtered_datasets

                if not datasets:
                    raise ValueError(f"No datasets of type '{db_type}' found for notebook {notebook_id}")

            # If single dataset, return simple format for backward compatibility
            if len(datasets) == 1:
                dataset = datasets[0]

                if dataset.type == "file":
                    dataset_with_files = await DatasetService.get_dataset(session, dataset.id)
                    if not dataset_with_files or not dataset_with_files.files:
                        raise ValueError(f"No files found in dataset {dataset.id}")

                    # Try to use cached schema first, fallback to regenerating and caching
                    file_schema = await DataFrameFileService.get_file_schema_multi(
                        dataset_with_files.files,
                        session=session,
                        dataset=dataset_with_files,
                        use_cache=True,
                        save_to_cache=True,
                    )

                    # Annotate with user annotations if available
                    annotated_schema = await DatabaseOperationsService.annotate_schema_with_user_annotations(
                        datasource_id=dataset_with_files.id,
                        schema_data={
                            "datasource_type": "duckdb",
                            "datasource_name": dataset_with_files.name or f"Dataset {dataset.id}",
                            "database_name": dataset_with_files.name or dataset_with_files.id,
                            "schema": file_schema.get("schema", {}),
                            "sample_data": file_schema.get("sample_data", {}),
                        },
                        session=session,
                    )

                    return annotated_schema

                elif dataset.type == "connection":
                    connection_repo = ConnectionRepository(session)
                    connection = await connection_repo.get(dataset.connection_id)

                    if not connection:
                        raise ValueError(f"Connection {dataset.connection_id} not found for dataset {dataset.id}")

                    connection_obj = await connection.get_decrypted_connection_obj(session)
                    if not connection_obj:
                        raise ValueError(f"Failed to decrypt connection object for connection {dataset.connection_id}")

                    if connection.type == "mongo":
                        schema = await DatabaseOperationsService.get_mongo_schema_async(connection_obj)
                    elif connection.type == "databricks":
                        schema = await DatabaseOperationsService.get_databricks_schema_async(connection_obj)
                    elif connection.type in ["pg", "mysql", "sqlite", "mssql"]:
                        schema = await DatabaseOperationsService.get_sql_schema_async(
                            connection_obj, db_type=connection.type
                        )
                    else:
                        raise ValueError(f"Unsupported database type: {connection.type}")

                    # Add dataset metadata to single dataset response
                    schema["dataset_id"] = dataset.id
                    schema["connection_id"] = connection.id
                    schema["connection_name"] = connection.name
                    return schema
                else:
                    raise ValueError(f"Unsupported dataset type: {dataset.type}")

            # Multiple datasets - return new format with all datasets
            databases = []
            connection_repo = ConnectionRepository(session)

            for idx, dataset in enumerate(datasets, start=1):
                try:
                    if dataset.type == "file":
                        dataset_with_files = await DatasetService.get_dataset(session, dataset.id)
                        if not dataset_with_files or not dataset_with_files.files:
                            logger.warning(f"No files found in dataset {dataset.id}, skipping")
                            continue

                        file_schema = await DataFrameFileService.get_file_schema_multi(
                            dataset_with_files.files,
                            session=session,
                            dataset=dataset_with_files,
                            use_cache=True,
                            save_to_cache=True,
                        )

                        annotated_schema = await DatabaseOperationsService.annotate_schema_with_user_annotations(
                            datasource_id=dataset_with_files.id,
                            schema_data={
                                "datasource_type": "duckdb",
                                "datasource_name": dataset_with_files.name or f"Dataset {dataset.id}",
                                "database_name": dataset_with_files.name or dataset_with_files.id,
                                "schema": file_schema.get("schema", {}),
                                "sample_data": file_schema.get("sample_data", {}),
                            },
                            session=session,
                        )

                        databases.append(
                            {
                                "database_number": idx,
                                "dataset_id": dataset.id,
                                "dataset_type": "file",
                                "database_type": "duckdb",
                                "database_name": dataset_with_files.name or dataset_with_files.id,
                                "schema": annotated_schema.get("schema", {}),
                            }
                        )

                    elif dataset.type == "connection":
                        connection = await connection_repo.get(dataset.connection_id)
                        if not connection:
                            logger.warning(
                                f"Connection {dataset.connection_id} not found for dataset {dataset.id}, skipping"
                            )
                            continue

                        connection_obj = await connection.get_decrypted_connection_obj(session)
                        if not connection_obj:
                            logger.warning(f"Failed to decrypt connection {dataset.connection_id}, skipping")
                            continue

                        if connection.type == "mongo":
                            schema = await DatabaseOperationsService.get_mongo_schema_async(connection_obj)
                        elif connection.type == "databricks":
                            schema = await DatabaseOperationsService.get_databricks_schema_async(connection_obj)
                        elif connection.type in ["pg", "mysql", "sqlite", "mssql"]:
                            schema = await DatabaseOperationsService.get_sql_schema_async(
                                connection_obj, db_type=connection.type
                            )
                        else:
                            logger.warning(f"Unsupported database type: {connection.type}, skipping")
                            continue

                        databases.append(
                            {
                                "database_number": idx,
                                "dataset_id": dataset.id,
                                "connection_id": connection.id,
                                "connection_name": connection.name,
                                "dataset_type": "connection",
                                "database_type": schema.get("database_type"),
                                "database_name": schema.get("database_name"),
                                "schema": schema.get("schema", {}),
                            }
                        )
                    else:
                        logger.warning(f"Unsupported dataset type: {dataset.type}, skipping")
                        continue

                except Exception as e:
                    logger.error(f"Failed to get schema for dataset {dataset.id}: {str(e)}")
                    # Continue with other datasets even if one fails

            if not databases:
                raise ValueError("Failed to retrieve schemas for any dataset")

            return {"notebook_id": notebook_id, "total_databases": len(databases), "databases": databases}

        except ValueError:
            raise
        except Exception as e:
            logger.error(
                f"Failed to get database schema by notebook ID: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "DatabaseOperationsService.get_database_schema_by_notebook_id",
                    "notebook_id": notebook_id,
                    "db_type": db_type,
                },
            )
            raise

    @staticmethod
    def serialize_bson(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: DatabaseOperationsService.serialize_bson(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DatabaseOperationsService.serialize_bson(v) for v in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, bson_regex.Regex):
            return {"$regex": value.pattern, "$options": value.flags}
        return value

    @staticmethod
    def serialize_sql_result(value: Any) -> Any:
        """Serialize SQL query results to JSON-compatible types."""
        from decimal import Decimal
        from uuid import UUID

        if isinstance(value, dict):
            return {k: DatabaseOperationsService.serialize_sql_result(v) for k, v in value.items()}
        if isinstance(value, list):
            return [DatabaseOperationsService.serialize_sql_result(v) for v in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, UUID):
            return str(value)
        # Handle date objects (without time)
        if hasattr(value, "isoformat") and callable(value.isoformat):
            return value.isoformat()
        return value

    @staticmethod
    def apply_filters_to_sql(query: str, filters: list[Any], db_type: str = "pg") -> tuple[str, dict[str, Any]]:
        """
        Apply filters to a SQL query using AST-aware WHERE insertion.
        Returns the modified query and named parameters for safe execution.
        """
        if not filters:
            return query, {}

        dialect = DatabaseOperationsService._sqlglot_dialect(db_type)
        alias_expression_map: dict[str, exp.Expression] | None = None
        scope_table_aliases: set[str] | None = None
        try:
            parsed_query = sqlglot.parse_one(query, read=dialect)
            alias_expression_map = DatabaseOperationsService._build_select_alias_expression_map(parsed_query)
            scope_table_aliases = DatabaseOperationsService._build_select_scope_table_aliases(parsed_query)
            condition_expression, params = DatabaseOperationsService._build_sql_filter_expression(
                filters,
                db_type,
                alias_expression_map=alias_expression_map,
                scope_table_aliases=scope_table_aliases,
            )
            if not condition_expression:
                return query, {}

            updated_query = parsed_query.where(condition_expression).sql(dialect=dialect)
            return DatabaseOperationsService._normalize_named_placeholders(updated_query), params
        except Exception as ast_error:
            logger.warning(f"AST SQL filter insertion failed, using fallback insertion: {ast_error}")

            if not alias_expression_map and not scope_table_aliases:
                alias_expression_map, scope_table_aliases = DatabaseOperationsService._extract_sql_filter_context(
                    query, db_type
                )

            condition_expression, params = DatabaseOperationsService._build_sql_filter_expression(
                filters,
                db_type,
                alias_expression_map=alias_expression_map,
                scope_table_aliases=scope_table_aliases,
            )
            if not condition_expression:
                return query, {}

            condition_sql = DatabaseOperationsService._normalize_named_placeholders(
                condition_expression.sql(dialect=dialect)
            )
            where_pos = DatabaseOperationsService._find_top_level_keyword(query, "WHERE")
            group_by_pos = DatabaseOperationsService._find_top_level_keyword(query, "GROUP BY")
            order_by_pos = DatabaseOperationsService._find_top_level_keyword(query, "ORDER BY")
            limit_pos = DatabaseOperationsService._find_top_level_keyword(query, "LIMIT")
            clause_positions = [pos for pos in [group_by_pos, order_by_pos, limit_pos] if pos > -1]
            insert_pos = min(clause_positions) if clause_positions else len(query)

            if where_pos > -1 and where_pos < insert_pos:
                modified_query = query[:insert_pos] + f" AND ({condition_sql}) " + query[insert_pos:]
            else:
                modified_query = query[:insert_pos] + f" WHERE ({condition_sql}) " + query[insert_pos:]
            return modified_query, params

    @staticmethod
    def apply_filters_to_mongo(query_str: str, filters: list[Any]) -> str:
        """
        Apply filters to a MongoDB query while preserving existing query semantics.
        """
        logger.info(f"apply_filters_to_mongo called with query: {query_str[:200]}...")
        logger.info(f"Filters to apply: {filters}")

        if not filters:
            return query_str

        try:
            parser = MongoConnector({})
            parsed = parser._parse_query(query_str.strip())
            if not parsed or parsed.get("error") or parsed.get("is_write_operation"):
                logger.warning(f"Could not parse Mongo query for filter injection: {query_str[:100]}")
                return query_str

            collection = parsed.get("collection")
            operation = parsed.get("operation")
            args = list(parsed.get("args") or [])
            modifiers = parsed.get("modifiers") or []
            filter_doc = DatabaseOperationsService._build_mongo_filter_doc(filters)

            if operation == "find":
                existing_query = args[0] if args and isinstance(args[0], dict) else {}
                merged_query = DatabaseOperationsService._merge_mongo_query_docs(existing_query, filter_doc)
                if args:
                    args[0] = merged_query
                else:
                    args = [merged_query]
            elif operation == "aggregate":
                pipeline = args[0] if args and isinstance(args[0], list) else []
                if not isinstance(pipeline, list):
                    return query_str
                if filter_doc:
                    if pipeline and isinstance(pipeline[0], dict) and "$match" in pipeline[0]:
                        pipeline[0]["$match"] = DatabaseOperationsService._merge_mongo_query_docs(
                            pipeline[0]["$match"], filter_doc
                        )
                    else:
                        pipeline.insert(0, {"$match": filter_doc})
                if args:
                    args[0] = pipeline
                else:
                    args = [pipeline]
            else:
                return query_str

            rebuilt = DatabaseOperationsService._build_mongo_query_string(collection, operation, args, modifiers)
            logger.info(f"Result after applying Mongo filters: {rebuilt[:300]}...")
            return rebuilt
        except Exception as e:
            logger.error(f"Error applying filters to MongoDB query: {e}")
            return query_str

    @staticmethod
    def _build_sql_filter_conditions(filters: list[Any], db_type: str = "pg") -> tuple[str, dict[str, Any]]:
        """Build SQL filter conditions and named params."""
        condition_expression, params = DatabaseOperationsService._build_sql_filter_expression(filters, db_type)
        if not condition_expression:
            return "", {}

        condition_sql = condition_expression.sql(dialect=DatabaseOperationsService._sqlglot_dialect(db_type))
        return DatabaseOperationsService._normalize_named_placeholders(condition_sql), params

    @staticmethod
    def _build_sql_filter_expression(
        filters: list[Any],
        db_type: str = "pg",
        alias_expression_map: dict[str, exp.Expression] | None = None,
        scope_table_aliases: set[str] | None = None,
    ) -> tuple[exp.Expression | None, dict[str, Any]]:
        """Build a sqlglot condition expression and named parameters for filters."""
        conditions: list[exp.Expression] = []
        params: dict[str, Any] = {}
        param_index = 1

        for filter_obj in filters:
            field_name = filter_obj.field
            field_expression = DatabaseOperationsService._build_sqlglot_field_expression(
                field_name,
                alias_expression_map=alias_expression_map,
                scope_table_aliases=scope_table_aliases,
            )
            operator = filter_obj.operator.lower()
            value = DatabaseOperationsService._coerce_sql_filter_param_value(filter_obj, operator, filter_obj.value)

            if operator == "eq":
                if value is None:
                    conditions.append(exp.Is(this=field_expression, expression=exp.Null()))
                else:
                    param_name = f"p{param_index}"
                    conditions.append(exp.EQ(this=field_expression, expression=exp.Placeholder(this=param_name)))
                    params[param_name] = value
                    param_index += 1
            elif operator == "ne":
                if value is None:
                    conditions.append(exp.Is(this=field_expression, expression=exp.Not(this=exp.Null())))
                else:
                    param_name = f"p{param_index}"
                    conditions.append(exp.NEQ(this=field_expression, expression=exp.Placeholder(this=param_name)))
                    params[param_name] = value
                    param_index += 1
            elif operator == "gt":
                param_name = f"p{param_index}"
                conditions.append(exp.GT(this=field_expression, expression=exp.Placeholder(this=param_name)))
                params[param_name] = value
                param_index += 1
            elif operator == "lt":
                param_name = f"p{param_index}"
                conditions.append(exp.LT(this=field_expression, expression=exp.Placeholder(this=param_name)))
                params[param_name] = value
                param_index += 1
            elif operator == "gte":
                param_name = f"p{param_index}"
                conditions.append(exp.GTE(this=field_expression, expression=exp.Placeholder(this=param_name)))
                params[param_name] = value
                param_index += 1
            elif operator == "lte":
                param_name = f"p{param_index}"
                conditions.append(exp.LTE(this=field_expression, expression=exp.Placeholder(this=param_name)))
                params[param_name] = value
                param_index += 1
            elif operator in {"like", "contains"}:
                param_name = f"p{param_index}"
                param_value = f"%{value}%" if operator == "contains" else value
                placeholder = exp.Placeholder(this=param_name)
                if db_type == "pg":
                    conditions.append(exp.ILike(this=field_expression, expression=placeholder))
                else:
                    conditions.append(
                        exp.Like(
                            this=exp.Lower(this=field_expression),
                            expression=exp.Lower(this=placeholder),
                        )
                    )
                params[param_name] = param_value
                param_index += 1
            elif operator == "in":
                values = value if isinstance(value, list) else [value]
                if not values:
                    conditions.append(exp.false())
                    continue

                placeholders: list[exp.Expression] = []
                for entry in values:
                    param_name = f"p{param_index}"
                    placeholders.append(exp.Placeholder(this=param_name))
                    params[param_name] = entry
                    param_index += 1
                conditions.append(exp.In(this=field_expression, expressions=placeholders))
            elif operator == "between":
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError(f"between operator requires exactly 2 values for field '{field_name}'")

                param_name_low = f"p{param_index}"
                param_name_high = f"p{param_index + 1}"
                conditions.append(
                    exp.Between(
                        this=field_expression,
                        low=exp.Placeholder(this=param_name_low),
                        high=exp.Placeholder(this=param_name_high),
                    )
                )
                params[param_name_low] = value[0]
                params[param_name_high] = value[1]
                param_index += 2
            else:
                raise ValueError(f"Unsupported filter operator '{operator}'")

        if not conditions:
            return None, {}
        if len(conditions) == 1:
            return conditions[0], params

        return exp.and_(*conditions), params

    @staticmethod
    def _is_date_ui_filter(filter_obj: Any) -> bool:
        ui_type = str(getattr(filter_obj, "ui_type", "") or "").strip().lower()
        return ui_type in {"date", "date_range", "datetime", "timestamp"}

    @staticmethod
    def _should_coerce_sql_date_filter(filter_obj: Any, operator: str) -> bool:
        if DatabaseOperationsService._is_date_ui_filter(filter_obj):
            return True
        if operator in {"gt", "lt", "gte", "lte", "between"}:
            field = str(getattr(filter_obj, "field", "") or "")
            return DatabaseOperationsService._is_date_field_name(field)
        return False

    @staticmethod
    def _coerce_sql_date_scalar(value: Any, is_upper_bound: bool) -> Any:
        if value is None:
            return None

        if isinstance(value, datetime):
            coerced = value
        elif isinstance(value, date):
            coerced = datetime.combine(value, time.max if is_upper_bound else time.min)
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return value
            if DatabaseOperationsService.DATE_ONLY_REGEX.fullmatch(stripped):
                if is_upper_bound:
                    return datetime.fromisoformat(f"{stripped}T23:59:59.999999")
                return datetime.fromisoformat(f"{stripped}T00:00:00")
            try:
                parsed = date_parser.isoparse(stripped)
            except Exception:
                try:
                    parsed = date_parser.parse(stripped)
                except Exception:
                    return value
            coerced = parsed
        else:
            return value

        if isinstance(coerced, datetime) and coerced.tzinfo is not None:
            coerced = coerced.astimezone(UTC).replace(tzinfo=None)
        return coerced

    @staticmethod
    def _coerce_sql_filter_param_value(filter_obj: Any, operator: str, value: Any) -> Any:
        if not DatabaseOperationsService._should_coerce_sql_date_filter(filter_obj, operator):
            return value

        if operator == "between":
            if not isinstance(value, list):
                return value
            coerced_values = [
                DatabaseOperationsService._coerce_sql_date_scalar(item, is_upper_bound=index == 1)
                for index, item in enumerate(value)
            ]
            return coerced_values

        return DatabaseOperationsService._coerce_sql_date_scalar(value, is_upper_bound=operator == "lte")

    @staticmethod
    def _sqlglot_dialect(db_type: str) -> str:
        mapping = {
            "pg": "postgres",
            "postgres": "postgres",
            "postgresql": "postgres",
            "mysql": "mysql",
            "sqlite": "sqlite",
            "mssql": "tsql",
        }
        return mapping.get((db_type or "pg").lower(), "postgres")

    @staticmethod
    def _extract_sql_filter_context(query: str, db_type: str) -> tuple[dict[str, exp.Expression], set[str]]:
        """
        Best-effort extraction of SELECT alias and table-scope context for filter field resolution.

        This is primarily used by fallback insertion when strict AST rewriting fails.
        """
        dialect = DatabaseOperationsService._sqlglot_dialect(db_type)
        parse_attempts = [
            {"read": dialect},
            {"read": dialect, "error_level": sqlglot_errors.ErrorLevel.IGNORE},
            {"error_level": sqlglot_errors.ErrorLevel.IGNORE},
        ]

        for attempt in parse_attempts:
            try:
                parsed_query = sqlglot.parse_one(query, **attempt)
            except Exception:
                continue

            if not isinstance(parsed_query, exp.Expression):
                continue

            alias_expression_map = DatabaseOperationsService._build_select_alias_expression_map(parsed_query)
            scope_table_aliases = DatabaseOperationsService._build_select_scope_table_aliases(parsed_query)
            return alias_expression_map, scope_table_aliases

        return {}, set()

    @staticmethod
    def _find_top_level_keyword(query: str, keyword: str) -> int:
        """
        Return the first position of a keyword at top-level SQL scope.

        This avoids matching keywords inside subqueries or quoted strings.
        """
        upper_query = query.upper()
        keyword_upper = keyword.upper()
        keyword_len = len(keyword_upper)
        depth = 0
        in_single = False
        in_double = False
        i = 0

        while i < len(query):
            ch = query[i]

            if ch == "'" and not in_double:
                if in_single and i + 1 < len(query) and query[i + 1] == "'":
                    i += 2
                    continue
                in_single = not in_single
                i += 1
                continue

            if ch == '"' and not in_single:
                if in_double and i + 1 < len(query) and query[i + 1] == '"':
                    i += 2
                    continue
                in_double = not in_double
                i += 1
                continue

            if not in_single and not in_double:
                if ch == "(":
                    depth += 1
                elif ch == ")" and depth > 0:
                    depth -= 1
                elif depth == 0 and upper_query.startswith(keyword_upper, i):
                    prev_char = upper_query[i - 1] if i > 0 else " "
                    next_idx = i + keyword_len
                    next_char = upper_query[next_idx] if next_idx < len(query) else " "
                    prev_ok = not (prev_char.isalnum() or prev_char == "_")
                    next_ok = not (next_char.isalnum() or next_char == "_")
                    if prev_ok and next_ok:
                        return i
            i += 1

        return -1

    @staticmethod
    def _normalize_named_placeholders(query: str) -> str:
        # sqlglot renders PostgreSQL placeholders as %(name)s, but SQLAlchemy text() expects :name
        return re.sub(r"%\((\w+)\)s", r":\1", query)

    @staticmethod
    def _normalize_sql_identifier(identifier: str) -> str:
        token = str(identifier or "").strip()
        if not token:
            raise ValueError("SQL filter field identifier cannot be empty")

        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            token = token[1:-1].replace('""', '"')
        elif token.startswith("`") and token.endswith("`") and len(token) >= 2:
            token = token[1:-1].replace("``", "`")
        elif token.startswith("[") and token.endswith("]") and len(token) >= 2:
            token = token[1:-1].replace("]]", "]")

        token = token.strip()
        if not token:
            raise ValueError("SQL filter field identifier cannot be empty")
        if any(ch in token for ch in ("\x00", "\n", "\r")):
            raise ValueError("SQL filter field identifier contains invalid control characters")
        return token

    @staticmethod
    def _split_sql_field_reference(field_name: str) -> tuple[str | None, str]:
        raw = str(field_name or "").strip()
        if not raw:
            raise ValueError("SQL filter field name cannot be empty")

        # Block obvious SQL control/comment tokens. Identifier content will be quoted afterwards.
        if any(token in raw for token in (";", "--", "/*", "*/")):
            raise ValueError(f"Unsafe SQL filter field name: '{field_name}'")

        qualifier_match = re.fullmatch(
            r'\s*(?P<table>(?:[A-Za-z_][A-Za-z0-9_]*|"[^"]+"|`[^`]+`|\[[^\]]+\]))\s*\.\s*(?P<column>.+?)\s*',
            raw,
        )
        if qualifier_match:
            table = DatabaseOperationsService._normalize_sql_identifier(qualifier_match.group("table"))
            column = DatabaseOperationsService._normalize_sql_identifier(qualifier_match.group("column"))
            return table, column

        return None, DatabaseOperationsService._normalize_sql_identifier(raw)

    @staticmethod
    def _quote_sql_field(field_name: str, db_type: str) -> str:
        table_name, column_name = DatabaseOperationsService._split_sql_field_reference(field_name)
        parts = [column_name]
        if table_name:
            parts = [table_name, column_name]
        if db_type == "mysql":
            quoted = [f"`{part.replace('`', '``')}`" for part in parts]
        elif db_type == "mssql":
            quoted = [f"[{part.replace(']', ']]')}]" for part in parts]
        else:
            quoted = ['"' + part.replace('"', '""') + '"' for part in parts]
        return ".".join(quoted)

    @staticmethod
    def _build_select_alias_expression_map(parsed_query: exp.Expression) -> dict[str, exp.Expression]:
        alias_map: dict[str, exp.Expression] = {}
        select_node = parsed_query if isinstance(parsed_query, exp.Select) else parsed_query.find(exp.Select)
        if not select_node:
            return alias_map

        for projection in select_node.expressions:
            alias_name = projection.alias_or_name
            if not alias_name:
                continue

            expression = projection.this if isinstance(projection, exp.Alias) else projection
            if not isinstance(expression, exp.Expression):
                continue

            alias_map[str(alias_name)] = expression.copy()
            alias_map[str(alias_name).lower()] = expression.copy()

        return alias_map

    @staticmethod
    def _extract_table_alias_from_source(source: exp.Expression | None) -> str | None:
        if source is None:
            return None

        if isinstance(source, exp.Table):
            alias = source.alias_or_name
            return str(alias).strip() if alias else None

        alias_or_name = getattr(source, "alias_or_name", None)
        if alias_or_name:
            alias_text = str(alias_or_name).strip()
            if alias_text:
                return alias_text

        alias_node = source.args.get("alias") if isinstance(source, exp.Expression) else None
        if isinstance(alias_node, exp.TableAlias):
            alias_name = alias_node.name
            if alias_name:
                return str(alias_name).strip()

        return None

    @staticmethod
    def _build_select_scope_table_aliases(parsed_query: exp.Expression) -> set[str]:
        aliases: set[str] = set()
        select_node = parsed_query if isinstance(parsed_query, exp.Select) else parsed_query.find(exp.Select)
        if not select_node:
            return aliases

        from_clause = select_node.args.get("from")
        if isinstance(from_clause, exp.From):
            from_source = from_clause.this
            from_alias = DatabaseOperationsService._extract_table_alias_from_source(from_source)
            if from_alias:
                aliases.add(from_alias)
            if isinstance(from_source, exp.Expression):
                for table in from_source.find_all(exp.Table):
                    table_alias = DatabaseOperationsService._extract_table_alias_from_source(table)
                    if table_alias:
                        aliases.add(table_alias)

        joins = select_node.args.get("joins") or []
        for join in joins:
            if not isinstance(join, exp.Join):
                continue
            join_source = join.this
            join_alias = DatabaseOperationsService._extract_table_alias_from_source(join_source)
            if join_alias:
                aliases.add(join_alias)
            if isinstance(join_source, exp.Expression):
                for table in join_source.find_all(exp.Table):
                    table_alias = DatabaseOperationsService._extract_table_alias_from_source(table)
                    if table_alias:
                        aliases.add(table_alias)

        return {alias.lower() for alias in aliases if alias}

    @staticmethod
    def _resolve_unscoped_qualified_field_expression(
        field_name: str,
        alias_expression_map: dict[str, exp.Expression] | None,
        scope_table_aliases: set[str] | None,
    ) -> exp.Expression | None:
        if not alias_expression_map or "." not in str(field_name or ""):
            return None

        table_name, column_name = DatabaseOperationsService._split_sql_field_reference(field_name)
        if not table_name:
            return None

        scope_aliases = {alias.lower() for alias in (scope_table_aliases or set())}
        if table_name.lower() in scope_aliases:
            return None

        target_column = column_name.lower()
        candidate_expr: exp.Expression | None = None
        candidate_count = 0
        seen_aliases: set[str] = set()

        for alias_key, alias_expression in alias_expression_map.items():
            alias_text = str(alias_key).strip()
            if not alias_text or "." in alias_text:
                continue
            alias_lower = alias_text.lower()
            if alias_lower in seen_aliases:
                continue
            seen_aliases.add(alias_lower)

            if alias_lower == target_column or alias_lower.endswith(f"_{target_column}"):
                candidate_expr = alias_expression.copy()
                candidate_count += 1
                if candidate_count > 1:
                    return None

        return candidate_expr if candidate_count == 1 else None

    @staticmethod
    def _build_sqlglot_field_expression(
        field_name: str,
        alias_expression_map: dict[str, exp.Expression] | None = None,
        scope_table_aliases: set[str] | None = None,
    ) -> exp.Expression:
        normalized_field_name = str(field_name or "").strip()
        if "." not in normalized_field_name and alias_expression_map:
            alias_expression = alias_expression_map.get(normalized_field_name) or alias_expression_map.get(
                normalized_field_name.lower()
            )
            if alias_expression is not None:
                return alias_expression.copy()

        resolved_unscoped = DatabaseOperationsService._resolve_unscoped_qualified_field_expression(
            normalized_field_name,
            alias_expression_map=alias_expression_map,
            scope_table_aliases=scope_table_aliases,
        )
        if resolved_unscoped is not None:
            return resolved_unscoped

        table_name, column_name = DatabaseOperationsService._split_sql_field_reference(normalized_field_name)
        scope_aliases = {alias.lower() for alias in (scope_table_aliases or set())}
        if table_name and scope_aliases and table_name.lower() not in scope_aliases:
            raise ValueError(
                f"Filter field '{field_name}' references alias '{table_name}' "
                "that is not available in the outer query scope"
            )

        column_identifier = exp.to_identifier(column_name, quoted=True)
        if table_name:
            table_identifier = exp.to_identifier(table_name, quoted=True)
            return exp.Column(this=column_identifier, table=table_identifier)
        return exp.Column(this=column_identifier)

    @staticmethod
    def _build_mongo_query_string(
        collection: str,
        operation: str,
        args: list[Any],
        modifiers: list[dict[str, Any]] | None = None,
    ) -> str:
        args_str = ", ".join(DatabaseOperationsService._serialize_mongo_value(arg) for arg in args)
        query = f"db.{collection}.{operation}({args_str})"
        for modifier in modifiers or []:
            method = modifier.get("method")
            if not method:
                continue
            value = modifier.get("args")
            if value is None:
                query += f".{method}()"
            else:
                query += f".{method}({DatabaseOperationsService._serialize_mongo_value(value)})"
        return query

    @staticmethod
    def _serialize_mongo_value(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return json.dumps(value)
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, ObjectId):
            return f'ObjectId("{str(value)}")'
        if isinstance(value, datetime):
            iso = value.isoformat()
            return f'ISODate("{iso}")'
        if isinstance(value, bson_regex.Regex):
            flags = ""
            if value.flags & re.IGNORECASE:
                flags += "i"
            if value.flags & re.MULTILINE:
                flags += "m"
            if value.flags & re.DOTALL:
                flags += "s"
            escaped_pattern = value.pattern.replace("/", r"\/")
            return f"/{escaped_pattern}/{flags}"
        if isinstance(value, list):
            rendered = ", ".join(DatabaseOperationsService._serialize_mongo_value(item) for item in value)
            return f"[{rendered}]"
        if isinstance(value, dict):
            rendered_items = ", ".join(
                f"{json.dumps(str(key))}: {DatabaseOperationsService._serialize_mongo_value(item)}"
                for key, item in value.items()
            )
            return f"{{{rendered_items}}}"

        # Fallback for BSON-native and unknown values while preserving JSON-serializable output.
        return bson_json_util.dumps(value)

    @staticmethod
    def _merge_mongo_query_docs(existing: Any, additional: dict[str, Any]) -> dict[str, Any]:
        if not additional:
            return existing if isinstance(existing, dict) else {}
        if not isinstance(existing, dict) or not existing:
            return additional
        if "$and" in existing and isinstance(existing["$and"], list):
            return {"$and": [*existing["$and"], additional]}
        return {"$and": [existing, additional]}

    DATE_PATTERNS = [
        r"^\d{4}-\d{2}-\d{2}$",  # YYYY-MM-DD
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",  # ISO 8601 datetime
        r"^\d{4}/\d{2}/\d{2}$",  # YYYY/MM/DD
        r"^\d{2}/\d{2}/\d{4}$",  # MM/DD/YYYY or DD/MM/YYYY
        r"^\d{2}-\d{2}-\d{4}$",  # MM-DD-YYYY or DD-MM-YYYY
    ]
    DATE_REGEX = re.compile("|".join(DATE_PATTERNS))
    DATE_FIELD_KEYWORDS = {
        "date",
        "time",
        "created",
        "updated",
        "timestamp",
        "at",
        "on",
        "start",
        "end",
        "from",
        "to",
        "born",
        "expired",
    }

    @staticmethod
    def _looks_like_date_value(value: str) -> bool:
        """Check if string value matches common date patterns."""
        if not value or not isinstance(value, str):
            return False
        return bool(DatabaseOperationsService.DATE_REGEX.match(value.strip()))

    @staticmethod
    def _is_date_field_name(field: str) -> bool:
        """Check if field name suggests a date field."""
        field_lower = field.lower()
        return any(kw in field_lower for kw in DatabaseOperationsService.DATE_FIELD_KEYWORDS)

    @staticmethod
    def _convert_to_date_if_needed(field: str, value: Any) -> Any:
        """Convert value to datetime if it appears to be a date."""
        if value is None:
            return value

        if isinstance(value, datetime):
            return value

        if isinstance(value, list):
            return [DatabaseOperationsService._convert_to_date_if_needed(field, v) for v in value]

        if not isinstance(value, str):
            return value

        field_lower = field.lower()
        if (
            field_lower.endswith("_id")
            or field_lower.endswith("id")
            or (field_lower != "id" and "id" in field_lower.split("_"))
        ):
            return value

        value_looks_like_date = DatabaseOperationsService._looks_like_date_value(value)
        field_suggests_date = DatabaseOperationsService._is_date_field_name(field)

        if not (value_looks_like_date or field_suggests_date):
            return value

        try:
            parsed = date_parser.parse(value)
            if 1900 <= parsed.year <= 2100:
                return parsed
            return value
        except (ValueError, TypeError, OverflowError):
            return value

    @staticmethod
    def _looks_like_objectid(value: Any) -> bool:
        """Check if value looks like a MongoDB ObjectId (24 hex characters)."""
        if not isinstance(value, str):
            return False
        return len(value) == 24 and all(c in "0123456789abcdefABCDEF" for c in value)

    @staticmethod
    def _is_objectid_field_name(field: str) -> bool:
        """Check if field name suggests it contains ObjectId values."""
        field_lower = field.lower()
        return field_lower == "_id" or field_lower.endswith("_id") or field_lower.endswith("id")

    @staticmethod
    def _convert_to_objectid_if_needed(field: str, value: Any) -> Any:
        """Convert value to ObjectId if it appears to be an ObjectId."""
        if value is None:
            return value

        if isinstance(value, ObjectId):
            return value

        if isinstance(value, list):
            return [DatabaseOperationsService._convert_to_objectid_if_needed(field, v) for v in value]

        if not isinstance(value, str):
            return value

        if DatabaseOperationsService._is_objectid_field_name(field) and DatabaseOperationsService._looks_like_objectid(
            value
        ):
            try:
                return ObjectId(value)
            except Exception:
                return value

        return value

    @staticmethod
    def _build_mongo_filter_doc(filters: list[Any]) -> dict[str, Any]:
        """Build MongoDB filter document from filters."""
        filter_doc: dict[str, Any] = {}
        logger.info(f"Building MongoDB filter doc from filters: {filters}")

        for filter_obj in filters:
            field = filter_obj.field
            if field.lower() == "id":
                field = "_id"
            operator = filter_obj.operator.lower()
            value = filter_obj.value

            original_value = value
            value = DatabaseOperationsService._convert_to_date_if_needed(field, value)
            if value != original_value:
                logger.info(f"Converted date value for field '{field}': {original_value} -> {value}")

            original_value = value
            value = DatabaseOperationsService._convert_to_objectid_if_needed(field, value)
            if value != original_value:
                logger.info(f"Converted ObjectId value for field '{field}': {original_value} -> {value}")

            condition: Any = None
            if operator == "eq":
                condition = {"$eq": value}
            elif operator == "ne":
                condition = {"$ne": value}
            elif operator == "gt":
                condition = {"$gt": value}
            elif operator == "lt":
                condition = {"$lt": value}
            elif operator == "gte":
                condition = {"$gte": value}
            elif operator == "lte":
                condition = {"$lte": value}
            elif operator == "like" or operator == "contains":
                condition = {"$regex": value, "$options": "i"}
            elif operator == "in":
                condition = {"$in": value if isinstance(value, list) else [value]}
            elif operator == "between":
                if isinstance(value, list) and len(value) == 2:
                    condition = {"$gte": value[0], "$lte": value[1]}

            if condition is None:
                continue

            existing = filter_doc.get(field)
            if existing is None:
                filter_doc[field] = condition
            else:
                if not isinstance(existing, dict):
                    existing = {"$eq": existing}
                if not isinstance(condition, dict):
                    condition = {"$eq": condition}
                merged = dict(existing)
                merged.update(condition)
                filter_doc[field] = merged

        logger.info(f"Built MongoDB filter_doc: {filter_doc}")
        return filter_doc

    @staticmethod
    def _find_matching_paren(s: str, start: int) -> int:
        """Find the matching closing parenthesis."""
        count = 0
        for i in range(start, len(s)):
            if s[i] == "(":
                count += 1
            elif s[i] == ")":
                count -= 1
                if count == 0:
                    return i
        return len(s)

    @staticmethod
    async def get_sql_schema_async(connection_obj: dict[str, Any], db_type: str = "pg") -> dict[str, Any]:
        """Get SQL database schema asynchronously for any SQL database (PostgreSQL, MySQL, SQLite, MSSQL)."""
        try:
            from sqlalchemy import inspect as sqlalchemy_inspect
            from sqlalchemy.ext.asyncio import create_async_engine

            # Create temporary connector to build connection URL
            temp_connector = AsyncSQLConnector(connection_obj, db_type=db_type)
            connection_url = temp_connector._build_connection_url()
            # Create temporary async engine for schema introspection
            engine = create_async_engine(connection_url, echo=False)

            try:
                async with engine.connect() as conn:
                    # Run synchronous inspection in async context
                    def get_schema_sync(sync_conn):
                        inspector = sqlalchemy_inspect(sync_conn)
                        schema_info = {}

                        for table_name in inspector.get_table_names():
                            columns = inspector.get_columns(table_name)
                            foreign_keys = inspector.get_foreign_keys(table_name)

                            schema_info[table_name] = {
                                "columns": [
                                    {
                                        "name": col["name"],
                                        "type": str(col["type"]),
                                        "nullable": col["nullable"],
                                    }
                                    for col in columns
                                ],
                                "foreign_keys": [
                                    {"column": fk["constrained_columns"], "ref_table": fk["referred_table"]}
                                    for fk in foreign_keys
                                ],
                            }

                        return schema_info

                    # Execute sync operation in async context
                    schema_info = await conn.run_sync(get_schema_sync)

                database_name = connection_obj.get("database", "")
                return {"datasource_type": db_type, "datasource_name": database_name, "schema": schema_info}

            finally:
                await engine.dispose()

        except Exception as e:
            logger.error(
                f"Failed to retrieve {db_type.upper()} schema: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "DatabaseOperationsService.get_sql_schema_async",
                    "db_type": db_type,
                    "database_name": connection_obj.get("database") if connection_obj else None,
                },
            )
            raise ConnectionError(f"Failed to retrieve {db_type.upper()} schema: {str(e)}")

    @staticmethod
    async def get_postgresql_schema_async(connection_obj: dict[str, Any]) -> dict[str, Any]:
        """Get PostgreSQL schema asynchronously."""
        try:
            # Build async connection URL
            host = connection_obj.get("host", "localhost")
            port = connection_obj.get("port", 5432)
            database = connection_obj.get("database")
            user = connection_obj.get("user") or connection_obj.get("username")
            password = connection_obj.get("password")

            if not all([database, user]):
                raise ValueError("Database and user are required for PostgreSQL connection")

            # Create temporary connection for schema introspection
            conn = await asyncpg.connect(host=host, port=port, database=database, user=user, password=password)

            try:
                # Get all tables
                tables = await conn.fetch("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_type = 'BASE TABLE'
                """)

                schema_info = {}

                for table_row in tables:
                    table_name = table_row["table_name"]

                    # Get columns for each table
                    columns = await conn.fetch(
                        """
                        SELECT
                            column_name,
                            data_type,
                            is_nullable,
                            column_default
                        FROM information_schema.columns
                        WHERE table_name = $1
                        AND table_schema = 'public'
                        ORDER BY ordinal_position
                    """,
                        table_name,
                    )

                    # Get foreign keys
                    foreign_keys = await conn.fetch(
                        """
                        SELECT
                            kcu.column_name,
                            ccu.table_name AS foreign_table_name
                        FROM information_schema.table_constraints AS tc
                        JOIN information_schema.key_column_usage AS kcu
                            ON tc.constraint_name = kcu.constraint_name
                        JOIN information_schema.constraint_column_usage AS ccu
                            ON ccu.constraint_name = tc.constraint_name
                        WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_name = $1
                    """,
                        table_name,
                    )

                    # Build column info and enrich json/jsonb columns with nested schema (sampled)
                    columns_info: list[dict[str, Any]] = []
                    for col in columns:
                        col_info: dict[str, Any] = {
                            "name": col["column_name"],
                            "type": col["data_type"],
                            "nullable": col["is_nullable"] == "YES",
                        }
                        if col_info["type"] in ("json", "jsonb"):
                            # Sample up to 10 non-null JSON values and infer nested schema
                            try:
                                sample_query = (
                                    f'SELECT "{col_info["name"]}" AS v FROM "{table_name}" '
                                    f'WHERE "{col_info["name"]}" IS NOT NULL ORDER BY RANDOM() LIMIT 10'
                                )
                                rows = await conn.fetch(sample_query)
                                merged_schema: dict[str, Any] | None = None
                                for r in rows:
                                    v = r["v"]
                                    # asyncpg may return str for JSON, parse if needed
                                    if isinstance(v, str):
                                        try:
                                            v = json.loads(v)
                                        except Exception:
                                            # Skip invalid JSON text
                                            continue
                                    schema_part = DatabaseOperationsService._infer_schema_from_value(v)
                                    merged_schema = (
                                        schema_part
                                        if merged_schema is None
                                        else DatabaseOperationsService._merge_json_schemas(merged_schema, schema_part)
                                    )
                                if merged_schema is not None:
                                    col_info["nested_schema"] = merged_schema
                            except Exception:
                                # Best-effort; ignore sampling errors for this column
                                pass
                        columns_info.append(col_info)

                    schema_info[table_name] = {
                        "columns": columns_info,
                        "foreign_keys": [
                            {"column": fk["column_name"], "ref_table": fk["foreign_table_name"]} for fk in foreign_keys
                        ],
                    }

                return {"datasource_type": "pg", "datasource_name": database, "schema": schema_info}

            finally:
                await conn.close()

        except Exception as e:
            raise ConnectionError(f"Failed to retrieve PostgreSQL schema: {str(e)}")

    @staticmethod
    async def get_mongo_schema_async(connection_obj: dict[str, Any]) -> dict[str, Any]:
        """Get MongoDB schema asynchronously."""
        try:
            conn_str = connection_obj.get("connection_string")
            if not conn_str:
                raise ValueError("No connection string provided for MongoDB")

            # Normalize connection string for Docker environment
            conn_str = AsyncSQLConnector._normalize_mongo_connection_string(conn_str)

            # Parse and encode password if needed
            parsed_uri = urlparse(conn_str)
            if parsed_uri.password:
                encoded_password = quote_plus(parsed_uri.password)
                if parsed_uri.username:
                    conn_str = conn_str.replace(
                        f"{parsed_uri.username}:{parsed_uri.password}@", f"{parsed_uri.username}:{encoded_password}@"
                    )

            client_options = {
                "serverSelectionTimeoutMS": 30000,
                "connectTimeoutMS": 30000,
                "socketTimeoutMS": 30000,
                "maxPoolSize": 10,
                "minPoolSize": 1,
            }

            if "mongodb+srv://" in conn_str or "mongodb.net" in conn_str:
                client_options["tlsCAFile"] = certifi.where()

            try:
                client = AsyncIOMotorClient(conn_str, **client_options)

                # Test connection
                await client.admin.command("ping")

                # Extract database name
                parsed = urlparse(conn_str)
                if parsed.path and parsed.path != "/":
                    db_name = parsed.path.lstrip("/")
                else:
                    db_name = connection_obj.get("database")
                    if not db_name:
                        raise ValueError("Database name must be specified")

                db = client[db_name]
                schema_info = {}

                # Get collection names
                collection_names = await db.list_collection_names()

                for collection_name in collection_names:
                    # Sample up to 10 random documents and infer a merged nested schema
                    try:
                        cursor = db[collection_name].aggregate([{"$sample": {"size": 10}}])
                        sample_docs = await cursor.to_list(length=10)
                    except Exception:
                        # Fallback to first 10 docs if $sample not allowed
                        sample_docs = await db[collection_name].find().limit(10).to_list(length=10)

                    merged_schema: dict[str, Any] | None = None
                    top_level_fields: set[str] = set()
                    for doc in sample_docs or []:
                        if isinstance(doc, dict):
                            top_level_fields.update(doc.keys())
                            schema_part = DatabaseOperationsService._infer_schema_from_value(doc)
                            merged_schema = (
                                schema_part
                                if merged_schema is None
                                else DatabaseOperationsService._merge_json_schemas(merged_schema, schema_part)
                            )

                    schema_info[collection_name] = {
                        "sample_fields": sorted(top_level_fields),
                        "nested_schema": merged_schema or {"type": "object", "properties": {}},
                    }

                client.close()

                return {"database_type": "mongo", "database_name": db_name, "schema": schema_info}

            except Exception as e:
                raise ConnectionError(f"Cannot connect to MongoDB: {str(e)}")
        except (ValueError, ConnectionError):
            raise
        except Exception as e:
            logger.error(
                f"Failed to get MongoDB schema: {str(e)}",
                exc_info=True,
                posthog_context={
                    "function": "DatabaseOperationsService.get_mongo_schema_async",
                    "database_name": connection_obj.get("database") if connection_obj else None,
                },
            )
            raise ConnectionError(f"Cannot connect to MongoDB: {str(e)}")

    @staticmethod
    async def get_dynamodb_schema_async(connection_obj: dict[str, Any]) -> dict[str, Any]:
        """Get DynamoDB schema asynchronously."""
        import boto3

        try:
            kwargs: dict[str, Any] = {
                "region_name": connection_obj.get("region"),
                "aws_access_key_id": connection_obj.get("access_key_id"),
                "aws_secret_access_key": connection_obj.get("secret_access_key"),
            }
            endpoint_url = connection_obj.get("endpoint_url")
            if endpoint_url:
                kwargs["endpoint_url"] = endpoint_url

            client = await asyncio.to_thread(lambda: boto3.client("dynamodb", **kwargs))

            try:
                table_names: list[str] = []
                last_table = None
                while len(table_names) < 100:
                    params: dict[str, Any] = {"Limit": 100}
                    if last_table:
                        params["ExclusiveStartTableName"] = last_table
                    response = await asyncio.to_thread(client.list_tables, **params)
                    batch = response.get("TableNames", [])
                    table_names.extend(batch)
                    last_table = response.get("LastEvaluatedTableName")
                    if not last_table:
                        break

                schema_info: dict[str, Any] = {}

                for table_name in table_names:
                    try:
                        desc_response = await asyncio.to_thread(client.describe_table, TableName=table_name)
                        table_desc = desc_response.get("Table", {})

                        key_schema = table_desc.get("KeySchema", [])
                        attribute_definitions = table_desc.get("AttributeDefinitions", [])
                        gsis = [
                            {
                                "IndexName": gsi.get("IndexName"),
                                "KeySchema": gsi.get("KeySchema", []),
                            }
                            for gsi in table_desc.get("GlobalSecondaryIndexes", [])
                        ]

                        scan_response = await asyncio.to_thread(client.scan, TableName=table_name, Limit=10)
                        raw_items = scan_response.get("Items", [])
                        sample_items = AsyncDynamoDBConnector._deserialize_items(raw_items)
                        sample_items = AsyncDynamoDBConnector._convert_decimals(sample_items)

                        merged_schema: dict[str, Any] | None = None
                        top_level_fields: set[str] = set()
                        for item in sample_items:
                            if isinstance(item, dict):
                                top_level_fields.update(item.keys())
                                schema_part = DatabaseOperationsService._infer_schema_from_value(item)
                                merged_schema = (
                                    schema_part
                                    if merged_schema is None
                                    else DatabaseOperationsService._merge_json_schemas(merged_schema, schema_part)
                                )

                        schema_info[table_name] = {
                            "key_schema": key_schema,
                            "attribute_definitions": attribute_definitions,
                            "global_secondary_indexes": gsis,
                            "sample_fields": sorted(top_level_fields),
                            "nested_schema": merged_schema or {"type": "object", "properties": {}},
                        }
                    except Exception as e:
                        logger.warning(f"Failed to get schema for DynamoDB table {table_name}: {str(e)}")
                        schema_info[table_name] = {
                            "key_schema": [],
                            "attribute_definitions": [],
                            "global_secondary_indexes": [],
                            "sample_fields": [],
                            "nested_schema": {"type": "object", "properties": {}},
                        }

                region = connection_obj.get("region", "unknown")
                return {
                    "database_type": "dynamodb",
                    "database_name": f"DynamoDB ({region})",
                    "query_mode": connection_obj.get("query_mode", "partiql"),
                    "schema": schema_info,
                }
            finally:
                pass

        except ConnectionError:
            raise
        except Exception as e:
            logger.error(f"Failed to get DynamoDB schema: {str(e)}", exc_info=True)
            raise ConnectionError(f"Cannot connect to DynamoDB: {str(e)}")

    @staticmethod
    async def get_databricks_schema_async(connection_obj: dict[str, Any]) -> dict[str, Any]:
        """Get Databricks schema asynchronously. Returns schema shape matching SQL connectors."""
        from server.services.databricks_connector import AsyncDatabricksConnector

        connector = AsyncDatabricksConnector(connection_obj)
        try:
            await connector.connect()
            raw = await connector.get_schema()
            catalog = raw.get("catalog")
            schema_name = raw.get("schema")

            schema_dict: dict[str, Any] = {}
            for tbl in raw.get("tables", []):
                key = tbl.get("name")
                if not key:
                    continue
                schema_dict[key] = {
                    "columns": [
                        {"name": c["name"], "type": c["type"], "nullable": True} for c in tbl.get("columns", [])
                    ],
                    "foreign_keys": [],
                    "qualified_name": tbl.get("qualified_name", key),
                    "catalog": tbl.get("catalog"),
                    "schema_name": tbl.get("schema"),
                }

            db_name_parts = [p for p in [catalog, schema_name] if p]
            db_name = ".".join(db_name_parts) if db_name_parts else "Databricks"

            return {
                "datasource_type": "databricks",
                "database_type": "databricks",
                "database_name": db_name,
                "schema": schema_dict,
                "catalogs": raw.get("catalogs", []),
                "schemas": raw.get("schemas", []),
                "catalog": catalog,
                "default_schema": schema_name,
            }
        except Exception as e:
            logger.error(f"Failed to get Databricks schema: {str(e)}", exc_info=True)
            raise ConnectionError(f"Cannot connect to Databricks: {str(e)}")
        finally:
            await connector.close()

    # ----------------------------
    # Nested schema inference utils
    # ----------------------------

    @staticmethod
    def _infer_schema_from_value(value: Any) -> dict[str, Any]:
        """Infer a simple JSON-schema-like structure from a Python value."""
        t = DatabaseOperationsService._infer_type(value)
        # Base case primitives and BSON types
        if t in (
            "null",
            "boolean",
            "integer",
            "number",
            "string",
            "objectId",
            "date",
            "decimal",
            "binary",
            "timestamp",
            "long",
            "regex",
            "code",
            "dbRef",
            "minKey",
            "maxKey",
        ):
            return {"type": t}
        if t == "object" and isinstance(value, dict):
            props: dict[str, Any] = {}
            for k, v in value.items():
                try:
                    v_schema = DatabaseOperationsService._infer_schema_from_value(v)
                except Exception:
                    v_schema = {"type": "string"}
                if k in props:
                    props[k] = DatabaseOperationsService._merge_json_schemas(props[k], v_schema)
                else:
                    props[k] = v_schema
            return {"type": "object", "properties": props}
        if t == "array" and isinstance(value, list):
            item_schema: dict[str, Any] | None = None
            for item in value:
                try:
                    s = DatabaseOperationsService._infer_schema_from_value(item)
                except Exception:
                    s = {"type": "string"}
                item_schema = (
                    s if item_schema is None else DatabaseOperationsService._merge_json_schemas(item_schema, s)
                )
            return {"type": "array", "items": item_schema or {"type": "null"}}
        # Fallback
        return {"type": "string"}

    @staticmethod
    def _infer_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        # bool is subclass of int; check bool first
        if isinstance(value, int) and not isinstance(value, bool):
            return "integer"
        if isinstance(value, float):
            return "number"

        # BSON-specific types (check before generic types)
        if isinstance(value, ObjectId):
            return "objectId"
        if isinstance(value, datetime):
            return "date"
        if isinstance(value, Decimal128):
            return "decimal"
        if isinstance(value, Binary):
            return "binary"
        if isinstance(value, Timestamp):
            return "timestamp"
        if isinstance(value, Int64):
            return "long"
        if isinstance(value, bson_regex.Regex):
            return "regex"
        if isinstance(value, Code):
            return "code"
        if isinstance(value, DBRef):
            return "dbRef"
        if isinstance(value, MinKey):
            return "minKey"
        if isinstance(value, MaxKey):
            return "maxKey"

        # Standard JSON types
        if isinstance(value, str):
            return "string"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "array"
        return "string"

    @staticmethod
    def _merge_json_schemas(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        """Merge two simple JSON-schema-like dicts."""

        def as_types(x: Any) -> set[str]:
            if isinstance(x, list):
                return set(x)
            if isinstance(x, str):
                return {x}
            return set()

        a_type = a.get("type")
        b_type = b.get("type")
        merged_types = sorted(as_types(a_type) | as_types(b_type)) if a_type and b_type else a_type or b_type
        result: dict[str, Any] = {"type": merged_types}

        # Merge object properties
        if ("object" in as_types(a_type)) or ("object" in as_types(b_type)):
            props: dict[str, Any] = {}
            a_props = a.get("properties", {}) if isinstance(a.get("properties"), dict) else {}
            b_props = b.get("properties", {}) if isinstance(b.get("properties"), dict) else {}
            for k in set(a_props.keys()) | set(b_props.keys()):
                if k in a_props and k in b_props:
                    props[k] = DatabaseOperationsService._merge_json_schemas(a_props[k], b_props[k])
                else:
                    props[k] = a_props.get(k) or b_props.get(k)
            result["properties"] = props

        # Merge array items
        if ("array" in as_types(a_type)) or ("array" in as_types(b_type)):
            a_items = a.get("items")
            b_items = b.get("items")
            if a_items and b_items:
                result["items"] = DatabaseOperationsService._merge_json_schemas(a_items, b_items)
            else:
                result["items"] = a_items or b_items or {"type": "null"}

        return result
