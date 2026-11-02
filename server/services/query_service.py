import asyncio
import json
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from server.repositories.connections import ConnectionRepository
from server.repositories.queries import QueryRepository
from server.repositories.skill_credentials import SkillCredentialRepository
from server.schemas.query import (
    FilterPreflightCompiledFilter,
    FilterPreflightResult,
    QueryFilter,
    SavedQueryResult,
)
from server.services.database_operations import DatabaseOperationsService
from server.services.dataset import DatasetService
from server.services.filter_compiler import FilterCompilationError, FilterCompilerService
from server.services.query_cache import query_result_cache
from server.services.raw_query import AsyncRawQueryService
from server.services.skill_discovery import SkillDiscovery
from server.utils.custom_logger import get_logger
from server.utils.schema_generator import generate_schema_from_response

if TYPE_CHECKING:  # pragma: no cover - helping type checkers
    from server.models.queries import Query

logger = get_logger(__name__)


async def _resolve_skill_credential(
    session: AsyncSession,
    skill_name: str,
    preferred_scope: str | None,
    viewer_user_id: UUID | None,
    creator_user_id: UUID | str | None,
    tenant_id: UUID,
) -> dict | None:
    """
    Resolve credential with fallback:
    1. If preferred_scope="user" AND viewer is creator → use creator's personal key
    2. Else try org credential
    3. Else try viewer's personal credential
    4. Else return None
    """
    repo = SkillCredentialRepository(session)

    creator_uuid = UUID(str(creator_user_id)) if creator_user_id else None

    if preferred_scope == "user" and viewer_user_id and viewer_user_id == creator_uuid:
        cred = await repo.get_by_skill(skill_name, tenant_id, viewer_user_id, "user")
        if cred:
            return await repo.get_decrypted_credentials(cred)

    org_cred = await repo.get_by_skill(skill_name, tenant_id, None, "org")
    if org_cred:
        return await repo.get_decrypted_credentials(org_cred)

    if viewer_user_id:
        user_cred = await repo.get_by_skill(skill_name, tenant_id, viewer_user_id, "user")
        if user_cred:
            return await repo.get_decrypted_credentials(user_cred)

    return None


def _resolve_skill_api_url(
    skill_name: str,
    api_config: dict,
) -> str | None:
    """Resolve the full URL for a skill API call, supporting both endpoint_path (new) and url (legacy) formats."""
    config = SkillDiscovery.get_skill_config(skill_name)
    if config and config.api.type == "aws":
        return None

    if "endpoint_path" in api_config:
        if not config:
            return None
        endpoint_path = api_config["endpoint_path"]
        return config.api.base_url.rstrip("/") + "/" + endpoint_path.lstrip("/")
    url = api_config.get("url", "")
    if url and not url.startswith(("http://", "https://")):
        if config and config.api.base_url:
            return config.api.base_url.rstrip("/") + "/" + url.lstrip("/")
    return url


async def _execute_skill_api_internal(
    skill_name: str,
    credentials: dict,
    api_config: dict,
) -> dict[str, Any]:
    """Execute skill API call and return data."""
    config = SkillDiscovery.get_skill_config(skill_name)
    if not config:
        return {"success": False, "error": f"Unknown skill: {skill_name}"}

    if not credentials.get("domain_active", credentials.get("subdomain_active", True)):
        return {
            "success": False,
            "error": f"{skill_name} domain is not whitelisted. Enable it in Settings > Skills > Whitelisted Domains.",
        }

    if api_config.get("is_aws") or config.api.type == "aws":
        from server.tools.skill_executor import _execute_aws_request

        action = api_config.get("action", api_config.get("endpoint_path", ""))
        params = {}
        params_raw = api_config.get("params", api_config.get("body", ""))
        if params_raw:
            try:
                params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            except (json.JSONDecodeError, TypeError):
                pass
        result_data, error_json = await _execute_aws_request(config, credentials, action, params)
        if error_json:
            return json.loads(error_json)
        if isinstance(result_data, dict):
            for key, value in result_data.items():
                if isinstance(value, list):
                    return {"success": True, "data": value}
            return {"success": True, "data": [result_data] if result_data else []}
        elif isinstance(result_data, list):
            return {"success": True, "data": result_data}
        return {"success": True, "data": [result_data] if result_data else []}

    url = _resolve_skill_api_url(skill_name, api_config)
    if not url:
        return {"success": False, "error": f"Could not resolve API URL for {skill_name}"}

    headers = {"Content-Type": "application/json", **config.api.headers}
    headers.update(config.api.auth_headers(credentials.get("api_key", "")))

    async with httpx.AsyncClient() as client:
        try:
            request_body = None
            if api_config.get("is_graphql"):
                request_body = {"query": api_config["graphql_query"]}
                if api_config.get("graphql_variables"):
                    try:
                        request_body["variables"] = json.loads(api_config["graphql_variables"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                response = await client.post(
                    url,
                    headers=headers,
                    json=request_body,
                    timeout=30.0,
                )
            else:
                if api_config.get("body"):
                    try:
                        request_body = json.loads(api_config["body"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                response = await client.request(
                    method=api_config.get("method", "GET"),
                    url=url,
                    headers=headers,
                    json=request_body,
                    timeout=30.0,
                )

            if response.status_code >= 400:
                error_text = response.text
                try:
                    error_data = response.json()
                    error_text = error_data.get("message", error_data.get("error", response.text))
                except Exception:
                    pass
                return {"success": False, "error": error_text, "status_code": response.status_code}

            data = response.json()

            if api_config.get("is_graphql") and "errors" in data:
                return {"success": False, "errors": data["errors"]}

            result_data = data.get("data") if api_config.get("is_graphql") else data

            if isinstance(result_data, dict):
                for key, value in result_data.items():
                    if isinstance(value, dict) and "nodes" in value:
                        return {"success": True, "data": value["nodes"]}
                    elif isinstance(value, list):
                        return {"success": True, "data": value}
                return {"success": True, "data": [result_data] if result_data else []}
            elif isinstance(result_data, list):
                return {"success": True, "data": result_data}
            else:
                return {"success": True, "data": [result_data] if result_data else []}

        except httpx.TimeoutException:
            return {"success": False, "error": "Request timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}


async def _background_refresh_query(
    query_id: str | UUID,
    filters: list[QueryFilter] | None,
    cache_key: str,
) -> None:
    """Refresh cache entry in background for SWR pattern."""
    try:
        from server.db.session import get_async_session_context

        async with get_async_session_context() as session:
            result = await QueryService._execute_query_internal(session, str(query_id), filters)
            if result.get("success"):
                has_filters = filters is not None and len(filters) > 0
                await query_result_cache.set(
                    cache_key,
                    {"data": result.get("data"), "query_name": result.get("query_name")},
                    query_id=str(query_id),
                    has_filters=has_filters,
                    session=session,
                )
                logger.debug(f"Background cache refresh completed for query {query_id}")
            else:
                try:
                    await session.rollback()
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Background cache refresh failed for query {query_id}: {e}")


class QueryService:
    @staticmethod
    def _contract_filter_summary(filter_contract_json: str | None) -> tuple[list[str], list[str]]:
        if not filter_contract_json:
            return [], []
        try:
            parsed = json.loads(filter_contract_json)
        except json.JSONDecodeError:
            return [], []

        if isinstance(parsed, dict):
            filters = parsed.get("filters", [])
        elif isinstance(parsed, list):
            filters = parsed
        else:
            filters = []

        if not isinstance(filters, list):
            return [], []

        ids: list[str] = []
        fields: list[str] = []
        for item in filters:
            if not isinstance(item, dict):
                continue
            filter_id = str(item.get("id", "")).strip()
            field_name = str(item.get("field_name", "")).strip()
            if filter_id:
                ids.append(filter_id)
            if field_name:
                fields.append(field_name)
        return sorted(set(ids)), sorted(set(fields))

    @staticmethod
    async def preflight_batch_query_filters(
        session: AsyncSession,
        query_ids: list[str] | None = None,
        queries_with_filters: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compile/validate query filters without executing queries."""
        if not queries_with_filters and not query_ids:
            return {
                "success": True,
                "message": "No queries to preflight",
                "data": [],
                "partial_success": False,
                "total_queries": 0,
                "successful_queries": 0,
                "failed_queries": 0,
            }

        normalized_entries: list[dict[str, Any]] = []
        if queries_with_filters:
            normalized_entries = [
                {
                    "query_id": str(entry["query_id"]),
                    "filters": entry.get("filters") or [],
                    "filter_values": entry.get("filter_values") or None,
                }
                for entry in queries_with_filters
            ]
        else:
            normalized_entries = [
                {"query_id": str(query_id), "filters": [], "filter_values": None} for query_id in (query_ids or [])
            ]

        query_repo = QueryRepository(session)
        preflight_results: list[FilterPreflightResult] = []
        successful_queries = 0
        failed_queries = 0

        for entry in normalized_entries:
            query_id = entry["query_id"]
            raw_filters = entry.get("filters") or []
            filter_values = entry.get("filter_values") or None
            warnings: list[str] = []

            saved_query = await query_repo.get(query_id)
            if not saved_query:
                preflight_results.append(
                    FilterPreflightResult(
                        query_id=query_id,
                        query_name="Unknown",
                        success=False,
                        error=f"Query '{query_id}' not found",
                    )
                )
                failed_queries += 1
                continue

            resolved_contract = saved_query.filter_contract
            if filter_values and not resolved_contract:
                resolved_contract = await FilterCompilerService._resolve_contract_from_notebook_config(
                    session=session,
                    query_id=query_id,
                    notebook_id=getattr(saved_query, "notebook_id", None),
                )
                if resolved_contract:
                    warnings.append("query_filter_contract_missing_used_notebook_filters_config_fallback")
                else:
                    warnings.append("query_filter_contract_missing")

            available_filter_ids, available_filter_fields = QueryService._contract_filter_summary(resolved_contract)

            try:
                compiled = FilterCompilerService.compile_with_contract(
                    query_id=query_id,
                    raw_filters=raw_filters,
                    filter_values=filter_values,
                    filter_contract_json=resolved_contract,
                )
                preflight_results.append(
                    FilterPreflightResult(
                        query_id=query_id,
                        query_name=getattr(saved_query, "name", "Unknown") or "Unknown",
                        success=True,
                        compiled_filters=[
                            FilterPreflightCompiledFilter(
                                field=item.field,
                                operator=item.operator,
                                value=item.value,
                                ui_type=item.ui_type,
                                ui_label=item.ui_label,
                            )
                            for item in compiled
                        ],
                        available_filter_ids=available_filter_ids,
                        available_filter_fields=available_filter_fields,
                        warnings=warnings,
                    )
                )
                successful_queries += 1
            except (FilterCompilationError, ValueError) as compile_error:
                preflight_results.append(
                    FilterPreflightResult(
                        query_id=query_id,
                        query_name=getattr(saved_query, "name", "Unknown") or "Unknown",
                        success=False,
                        error=f"Invalid filters: {compile_error}",
                        available_filter_ids=available_filter_ids,
                        available_filter_fields=available_filter_fields,
                        warnings=warnings,
                    )
                )
                failed_queries += 1

        total_queries = len(preflight_results)
        partial_success = successful_queries > 0 and failed_queries > 0
        overall_success = failed_queries == 0

        message = "All query filters validated successfully"
        if partial_success:
            message = f"Partial filter validation success: {successful_queries}/{total_queries} queries valid"
        elif failed_queries == total_queries:
            message = "All query filter validations failed"

        return {
            "success": overall_success,
            "message": message,
            "data": [result.model_dump() for result in preflight_results],
            "partial_success": partial_success,
            "total_queries": total_queries,
            "successful_queries": successful_queries,
            "failed_queries": failed_queries,
        }

    @staticmethod
    async def execute_and_save_query(
        session: AsyncSession,
        query: str,
        connection_id: str,
        notebook_id: str,
        db_type: str,
        name: str,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute and save a query for either connection or file datasets.

        Now that queries reference datasets (not connections), this method:
        1. Finds the dataset for the notebook
        2. Executes the query (SQL for connections, DuckDB SQL for file datasets)
        3. Saves the query with dataset_id
        """
        try:
            connection_obj = None
            dataset_id = None

            # Get datasets for the notebook
            datasets = await DatasetService.get_datasets_by_notebook(session, notebook_id)

            if not datasets:
                return {
                    "success": False,
                    "error": f"No datasource found for notebook {notebook_id}. Please connect a database or upload files.",
                }

            # Find the correct dataset by connection_id (required for multi-datasource notebooks)
            if not connection_id:
                return {
                    "success": False,
                    "error": "connection_id is required for multi-datasource notebooks",
                }

            dataset = None
            # Find dataset by connection_id (for database connections)
            for ds in datasets:
                if ds.type == "connection" and str(ds.connection_id) == str(connection_id):
                    dataset = ds
                    logger.info(f"Found dataset {ds.id} for connection_id {connection_id}")
                    break
                # Or by dataset ID directly (for file datasets)
                elif str(ds.id) == str(connection_id):
                    dataset = ds
                    logger.info(f"Found dataset {ds.id} matching dataset_id {connection_id}")
                    break

            if not dataset:
                return {
                    "success": False,
                    "error": f"Dataset with connection_id '{connection_id}' not found in this notebook",
                }

            dataset_id = dataset.id

            if dataset.type == "connection" and dataset.connection_id:
                # Connection-type dataset
                conn_repo = ConnectionRepository(session)
                connection = await conn_repo.get(dataset.connection_id)
                if connection:
                    connection_obj = await connection.get_decrypted_connection_obj(session)
                    connection_id = connection.id  # For raw_query execution
                    db_type = connection.type  # Use actual connection type, not context db_type (multi-datasource fix)
                else:
                    return {
                        "success": False,
                        "error": f"Connection not found for dataset {dataset.id}",
                    }

            elif dataset.type == "file":
                # File-type dataset - build connection_obj from files
                dataset_with_files = await DatasetService.get_dataset(session, dataset.id)
                if dataset_with_files and dataset_with_files.files:
                    connection_obj = {
                        "dataset_id": dataset.id,
                        "dataset_type": "file",
                        "db_type": "duckdb",
                        "files": [
                            {"id": f.id, "name": f.name, "type": f.type, "size": f.size}
                            for f in dataset_with_files.files
                        ],
                    }
                    connection_id = dataset.id  # For raw_query execution
                    logger.info(f"Using file dataset {dataset.id} for query execution")
                    db_type = "duckdb"
                else:
                    return {
                        "success": False,
                        "error": f"No files found in dataset {dataset.id}",
                    }

            if not connection_obj:
                return {
                    "success": False,
                    "error": f"Failed to load datasource for notebook {notebook_id}.",
                }

            result = await AsyncRawQueryService.execute_raw_query(
                query=query,
                db_type=db_type,
                connection_id=connection_id,
                connection_obj=connection_obj,
            )

            if "error" in result and not result.get("success"):
                error_payload = {"success": False, "error": result["error"]}
                for key in ("timeout", "timeout_seconds", "execution_time_seconds", "db_type", "query"):
                    if key in result:
                        error_payload[key] = result[key]
                return error_payload

            generated_schema = None
            if result.get("success") and "result" in result:
                try:
                    generated_schema = generate_schema_from_response(result["result"])
                except Exception as e:
                    generated_schema = {"error": f"Failed to generate schema: {str(e)}"}

            query_repo = QueryRepository(session)
            saved_query = await query_repo.create(
                {
                    "name": name,
                    "query": query,
                    "output_schema": (json.dumps(generated_schema) if generated_schema else "{}"),
                    "dataset_id": dataset_id,  # Now queries reference datasets (not connections)
                    "notebook_id": notebook_id,
                    "created_by": created_by,
                },
            )

            db_data = result.get("result")

            try:
                from server.services.redaction_service import RedactionService

                redacted_columns = await RedactionService.get_redacted_columns(str(dataset_id), session)
                redacted_tables = await RedactionService.get_redacted_tables(str(dataset_id), session)
                if redacted_columns or redacted_tables:
                    RedactionService.redact_result_rows(db_data, redacted_columns, redacted_tables)
            except Exception as redact_err:
                logger.warning("Failed to apply redaction in execute_and_save_query: %s", redact_err)

            try:
                cache_key = query_result_cache.generate_key(str(saved_query.id))
                await query_result_cache.invalidate(cache_key, session=session)
                await query_result_cache.set(
                    cache_key,
                    {
                        "data": db_data,
                        "query_name": name,
                        "generated_schema": generated_schema,
                    },
                    query_id=str(saved_query.id),
                    session=session,
                )
            except Exception as cache_error:
                try:
                    await session.rollback()
                except Exception:
                    pass
                logger.warning(
                    "Failed to update query cache for %s: %s",
                    saved_query.id,
                    cache_error,
                )

            return {
                "success": True,
                "message": "Request processed successfully",
                "data": db_data,
                "generated_schema": generated_schema,
                "query_id": saved_query.id,
            }
        except Exception as e:
            logger.error(
                f"Failed to execute and save query: {str(e)}",
                posthog_context={
                    "function": "QueryService.execute_and_save_query",
                    "connection_id": (connection_id if "connection_id" in locals() else None),
                    "notebook_id": notebook_id,
                    "db_type": db_type,
                },
            )
            return {
                "success": False,
                "error": f"Failed to execute and save query: {str(e)}",
            }

    @staticmethod
    async def _execute_api_query(
        session: AsyncSession, saved_query: "Query", filters: list[QueryFilter] | None = None
    ) -> dict[str, Any]:
        """Execute an API-type query using skill credentials."""
        if not saved_query.skill_name:
            return {"success": False, "error": "API query missing skill_name"}

        if not saved_query.api_config:
            return {"success": False, "error": "API query missing api_config"}

        cred_repo = SkillCredentialRepository(session)
        scope = saved_query.skill_scope or "user"

        credential = None
        if scope == "org":
            credential = await cred_repo.get_by_skill(
                skill_name=saved_query.skill_name,
                tenant_id=saved_query.tenant_id,
                user_id=None,
                scope="org",
            )
        else:
            credential = await cred_repo.get_by_skill(
                skill_name=saved_query.skill_name,
                tenant_id=saved_query.tenant_id,
                user_id=saved_query.created_by,
                scope="user",
            )
            if not credential:
                credential = await cred_repo.get_by_skill(
                    skill_name=saved_query.skill_name,
                    tenant_id=saved_query.tenant_id,
                    user_id=None,
                    scope="org",
                )

        if not credential:
            return {"success": False, "error": f"{saved_query.skill_name} credentials not found for scope '{scope}'"}

        try:
            config = json.loads(saved_query.api_config)
        except json.JSONDecodeError:
            return {"success": False, "error": "Invalid api_config JSON"}

        credentials = await cred_repo.get_decrypted_credentials(credential)
        if not credentials:
            return {"success": False, "error": f"Failed to decrypt {saved_query.skill_name} credentials"}

        if not credentials.get("domain_active", credentials.get("subdomain_active", True)):
            return {
                "success": False,
                "error": f"{saved_query.skill_name} domain is not whitelisted. Enable it in Settings > Skills > Whitelisted Domains.",
            }

        api_key = credentials.get("api_key", "")

        skill_config = SkillDiscovery.get_skill_config(saved_query.skill_name)
        headers = {"Content-Type": "application/json"}
        if skill_config and skill_config.api.headers:
            headers.update(skill_config.api.headers)

        if skill_config:
            headers.update(skill_config.api.auth_headers(api_key))
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        url = _resolve_skill_api_url(saved_query.skill_name, config)
        if not url:
            return {"success": False, "error": f"Could not resolve API URL for {saved_query.skill_name}"}

        request_body = None
        is_graphql = config.get("is_graphql", False)

        if is_graphql:
            request_body = {"query": config.get("graphql_query", "")}
            if config.get("graphql_variables"):
                request_body["variables"] = config["graphql_variables"]
        elif config.get("body"):
            try:
                request_body = json.loads(config["body"]) if isinstance(config["body"], str) else config["body"]
            except json.JSONDecodeError:
                request_body = config["body"]

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(
                    method="POST" if is_graphql else config.get("method", "GET").upper(),
                    url=url,
                    headers=headers,
                    json=request_body,
                    timeout=30.0,
                )

                if response.status_code >= 400:
                    error_text = response.text
                    try:
                        error_data = response.json()
                        error_text = error_data.get("message", error_data.get("error", response.text))
                    except Exception:
                        pass
                    return {"success": False, "error": error_text, "status_code": response.status_code}

                data = response.json()

                if is_graphql and "errors" in data:
                    return {"success": False, "error": str(data["errors"])}

                result_data = data.get("data") if is_graphql else data

                if filters and isinstance(result_data, list):
                    result_data = QueryService._apply_filters_to_api_result(result_data, filters)

                return {
                    "success": True,
                    "message": "Request processed successfully",
                    "data": result_data,
                    "query_name": saved_query.name,
                    "query_id": saved_query.id,
                }

            except httpx.TimeoutException:
                return {"success": False, "error": "API request timed out"}
            except Exception as e:
                logger.error(f"API query execution failed: {e}")
                return {"success": False, "error": str(e)}

    @staticmethod
    def _apply_filters_to_api_result(data: list[dict], filters: list[QueryFilter]) -> list[dict]:
        """Apply filters to API result data (server-side filtering)."""
        if not filters:
            return data

        def make_matcher(field: str, op: str, value: Any):
            def matches(item: dict) -> bool:
                item_value = item.get(field)
                if item_value is None:
                    return False

                if op == "eq":
                    return item_value == value
                elif op == "ne":
                    return item_value != value
                elif op == "gt":
                    return item_value > value
                elif op == "lt":
                    return item_value < value
                elif op == "gte":
                    return item_value >= value
                elif op == "lte":
                    return item_value <= value
                elif op == "like" or op == "contains":
                    return str(value).lower() in str(item_value).lower()
                elif op == "in":
                    return item_value in (value if isinstance(value, list) else [value])
                return True

            return matches

        filtered = data
        for f in filters:
            matcher = make_matcher(f.field, f.operator, f.value)
            filtered = [item for item in filtered if matcher(item)]

        return filtered

    @staticmethod
    async def _execute_query_internal(
        session: AsyncSession,
        query_id: str,
        filters: list[QueryFilter] | None = None,
        viewer_user_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Internal method to execute query without caching logic."""
        query_repo = QueryRepository(session)
        saved_query = await query_repo.get_with_relations(query_id)

        if not saved_query:
            return {"success": False, "error": "Query not found"}

        if saved_query.query_type == "api":
            return await QueryService._execute_api_query(session, saved_query, filters)

        dataset = saved_query.dataset
        if not dataset:
            return {"success": False, "error": "Dataset not found for query"}

        if dataset.type == "skill_api":
            try:
                api_config = json.loads(saved_query.query)
            except json.JSONDecodeError:
                return {"success": False, "error": "Invalid API config in saved query"}

            credentials = await _resolve_skill_credential(
                session=session,
                skill_name=dataset.skill_name,
                preferred_scope=dataset.skill_scope,
                viewer_user_id=viewer_user_id,
                creator_user_id=saved_query.created_by,
                tenant_id=dataset.tenant_id,
            )

            if not credentials:
                return {
                    "success": False,
                    "error": f"No credentials found for {dataset.skill_name}. Please configure in Settings > Skills.",
                }

            result = await _execute_skill_api_internal(
                skill_name=dataset.skill_name,
                credentials=credentials,
                api_config=api_config,
            )

            if not result.get("success"):
                return result

            return {
                "success": True,
                "message": "Request processed successfully",
                "data": result.get("data"),
                "query_name": saved_query.name,
                "query_id": saved_query.id,
            }

        if dataset.type == "connection" and dataset.connection:
            connection_obj = await dataset.connection.get_decrypted_connection_obj(session)
            if not connection_obj:
                return {"success": False, "error": "Failed to decrypt connection object"}
            db_type = dataset.connection.type
            connection_id = dataset.connection.id
        elif dataset.type == "file":
            connection_obj = {
                "dataset_id": dataset.id,
                "dataset_type": "file",
                "db_type": "duckdb",
                "files": [{"id": f.id, "name": f.name, "type": f.type, "size": f.size} for f in dataset.files],
            }
            db_type = "duckdb"
            connection_id = dataset.id
        else:
            return {"success": False, "error": "Invalid dataset type"}

        query_to_execute = saved_query.query
        sql_params: dict[str, Any] | None = None
        if filters:
            try:
                filters = FilterCompilerService.compile_with_contract(
                    query_id=str(saved_query.id),
                    raw_filters=filters,
                    filter_contract_json=saved_query.filter_contract,
                )
            except (FilterCompilationError, ValueError) as compile_error:
                return {"success": False, "error": f"Invalid filters: {compile_error}"}
            if db_type == "mongo":
                query_to_execute = DatabaseOperationsService.apply_filters_to_mongo(saved_query.query, filters)
            elif db_type == "dynamodb" and connection_obj.get("query_mode") == "native":
                pass
            else:
                query_to_execute, sql_params = DatabaseOperationsService.apply_filters_to_sql(
                    saved_query.query, filters, db_type
                )

        result = await AsyncRawQueryService.execute_raw_query(
            query=query_to_execute,
            db_type=db_type,
            connection_id=connection_id,
            connection_obj=connection_obj,
            params=sql_params,
        )

        if "error" in result and not result.get("success"):
            error_payload = {"success": False, "error": result["error"]}
            for key in ("timeout", "timeout_seconds", "execution_time_seconds", "db_type", "query"):
                if key in result:
                    error_payload[key] = result[key]
            return error_payload

        db_data = result.get("result")

        try:
            from server.services.redaction_service import RedactionService

            redacted_columns = await RedactionService.get_redacted_columns(str(dataset.id), session)
            redacted_tables = await RedactionService.get_redacted_tables(str(dataset.id), session)
            if redacted_columns or redacted_tables:
                RedactionService.redact_result_rows(db_data, redacted_columns, redacted_tables)
        except Exception as redact_err:
            logger.warning("Failed to apply redaction to query results: %s", redact_err)

        return {
            "success": True,
            "message": "Request processed successfully",
            "data": db_data,
            "query_name": saved_query.name,
            "query_id": saved_query.id,
        }

    @staticmethod
    async def execute_saved_query(
        session: AsyncSession,
        query_id: str,
        filters: list[QueryFilter] | None = None,
        viewer_user_id: UUID | None = None,
    ) -> dict[str, Any]:
        try:
            query_repo = QueryRepository(session)
            saved_query = await query_repo.get_with_relations(query_id)

            if saved_query and saved_query.dataset and saved_query.dataset.type == "skill_api":
                result = await QueryService._execute_query_internal(session, query_id, filters, viewer_user_id)
                result["cached"] = False
                result["stale"] = False
                return result

            has_filters = filters is not None and len(filters) > 0
            cache_key = query_result_cache.generate_key(query_id, filters)

            cached_result, is_stale = await query_result_cache.get_with_stale(cache_key, session=session)

            if cached_result is not None:
                if is_stale:
                    asyncio.create_task(_background_refresh_query(query_id, filters, cache_key))

                return {
                    "success": True,
                    "message": "Request processed successfully (cached)",
                    "data": cached_result.get("data"),
                    "query_name": cached_result.get("query_name"),
                    "query_id": query_id,
                    "cached": True,
                    "stale": is_stale,
                }

            result = await QueryService._execute_query_internal(session, query_id, filters, viewer_user_id)

            if result.get("success"):
                try:
                    await query_result_cache.set(
                        cache_key,
                        {"data": result.get("data"), "query_name": result.get("query_name")},
                        query_id=query_id,
                        has_filters=has_filters,
                        session=session,
                    )
                except Exception as cache_error:
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                    logger.warning(f"Failed to update query cache for {query_id}: {cache_error}")

            result["cached"] = False
            result["stale"] = False
            return result

        except Exception as e:
            logger.error(
                f"Failed to execute saved query: {str(e)}",
                posthog_context={
                    "function": "QueryService.execute_saved_query",
                    "query_id": query_id,
                    "has_filters": filters is not None and len(filters) > 0,
                },
            )
            return {
                "success": False,
                "error": f"Failed to execute saved query: {str(e)}",
            }

    @staticmethod
    async def update_query(
        session: AsyncSession,
        query_id: str,
        name: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        try:
            query_repo = QueryRepository(session)

            existing_query = await query_repo.get(query_id)
            if not existing_query:
                return {"success": False, "error": "Query not found"}

            update_data = {}
            if name is not None:
                update_data["name"] = name
            if query is not None:
                update_data["query"] = query

            if not update_data:
                return {"success": False, "error": "No fields to update"}

            updated_query = await query_repo.update(query_id, update_data)

            if not updated_query:
                return {"success": False, "error": "Failed to update query"}

            cache_key = query_result_cache.generate_key(query_id)
            await query_result_cache.invalidate(cache_key, session=session)

            return {
                "success": True,
                "message": "Query updated successfully",
                "query_id": updated_query.id,
            }
        except Exception as e:
            logger.error(
                f"Failed to update query: {str(e)}",
                posthog_context={
                    "function": "QueryService.update_query",
                    "query_id": query_id,
                },
            )
            return {"success": False, "error": f"Failed to update query: {str(e)}"}

    @staticmethod
    async def delete_query(session: AsyncSession, query_id: str) -> dict[str, Any]:
        try:
            query_repo = QueryRepository(session)

            deleted = await query_repo.delete_by_id(query_id)

            if not deleted:
                return {"success": False, "error": "Query not found"}

            return {"success": True, "message": "Query deleted successfully"}
        except Exception as e:
            logger.error(
                f"Failed to delete query: {str(e)}",
                posthog_context={
                    "function": "QueryService.delete_query",
                    "query_id": query_id,
                },
            )
            return {"success": False, "error": f"Failed to delete query: {str(e)}"}

    @staticmethod
    async def delete_all_queries(session: AsyncSession) -> dict[str, Any]:
        try:
            query_repo = QueryRepository(session)

            deleted_count = await query_repo.delete_all()

            return {
                "success": True,
                "message": f"Successfully deleted {deleted_count} queries",
                "deleted_count": deleted_count,
            }
        except Exception as e:
            logger.error(
                f"Failed to delete all queries: {str(e)}",
                posthog_context={"function": "QueryService.delete_all_queries"},
            )
            return {
                "success": False,
                "error": f"Failed to delete all queries: {str(e)}",
            }

    @staticmethod
    async def _execute_single_saved_query_async(
        query_id: str,
        session: AsyncSession,  # Keep for backward compatibility but won't use
        semaphore: asyncio.Semaphore,
        filters: list[QueryFilter] | None = None,
    ) -> SavedQueryResult:
        """Execute a single saved query with semaphore control and isolated session."""
        from server.db.session import AsyncSessionFactory

        async with semaphore:
            start_time = time.time()

            async with AsyncSessionFactory() as isolated_session:
                try:
                    result = await QueryService.execute_saved_query(isolated_session, query_id, filters)
                    execution_time = (time.time() - start_time) * 1000

                    if result["success"]:
                        return SavedQueryResult(
                            query_id=query_id,
                            query_name=result.get("query_name", "Unknown"),
                            success=True,
                            result=result.get("data"),
                            error=None,
                            execution_time_ms=execution_time,
                        )
                    else:
                        return SavedQueryResult(
                            query_id=query_id,
                            query_name=result.get("query_name", "Unknown"),
                            success=False,
                            result=None,
                            error=result.get("error", "Unknown error"),
                            execution_time_ms=execution_time,
                        )
                except Exception as e:
                    execution_time = (time.time() - start_time) * 1000
                    return SavedQueryResult(
                        query_id=query_id,
                        query_name="Unknown",
                        success=False,
                        result=None,
                        error=str(e),
                        execution_time_ms=execution_time,
                    )

    @staticmethod
    async def execute_batch_saved_queries(
        session: AsyncSession,
        query_ids: list[str] | None = None,
        queries_with_filters: list[dict[str, Any]] | None = None,
        max_parallel: int = 5,
    ) -> dict[str, Any]:
        """Execute multiple saved queries in parallel with error isolation and filter support."""
        if not queries_with_filters and not query_ids:
            return {
                "success": True,
                "message": "No queries to execute",
                "data": [],  # Changed from "results" to "data"
                "partial_success": False,
                "total_queries": 0,
                "successful_queries": 0,
                "failed_queries": 0,
                "total_execution_time_ms": 0,
            }

        start_time = time.time()

        prepared_inputs: list[tuple[int, str, list[QueryFilter] | None]] = []
        precompiled_failures: dict[int, SavedQueryResult] = {}

        # Handle both legacy query_ids and new queries_with_filters format
        if queries_with_filters:
            for index, entry in enumerate(queries_with_filters):
                query_id = str(entry["query_id"])
                raw_filters = entry.get("filters") or []
                filter_values = entry.get("filter_values") or None
                try:
                    compiled = await FilterCompilerService.compile_for_query(
                        session=session,
                        query_id=query_id,
                        raw_filters=raw_filters,
                        filter_values=filter_values,
                    )
                    prepared_inputs.append((index, query_id, compiled if compiled else None))
                except (FilterCompilationError, ValueError) as compile_error:
                    precompiled_failures[index] = SavedQueryResult(
                        query_id=query_id,
                        query_name="Unknown",
                        success=False,
                        result=None,
                        error=f"Invalid filters: {compile_error}",
                        execution_time_ms=0,
                    )
        else:
            prepared_inputs = [(idx, str(qid), None) for idx, qid in enumerate(query_ids or [])]

        # Create semaphore for connection pooling
        semaphore = asyncio.Semaphore(max_parallel)

        # Create tasks for all queries with their filters
        task_specs = [
            (
                index,
                asyncio.create_task(
                    QueryService._execute_single_saved_query_async(query_id, session, semaphore, compiled_filters)
                ),
            )
            for index, query_id, compiled_filters in prepared_inputs
        ]

        # Execute all queries in parallel, catching exceptions
        task_results = await asyncio.gather(*[task for _, task in task_specs], return_exceptions=True)

        # Process results
        total_queries = len(queries_with_filters) if queries_with_filters is not None else len(query_ids or [])
        query_results: list[SavedQueryResult | None] = [None] * total_queries
        successful_queries = 0
        failed_queries = 0

        for failed_index, failed_result in precompiled_failures.items():
            query_results[failed_index] = failed_result
            failed_queries += 1

        for i, result in enumerate(task_results):
            original_index = task_specs[i][0]
            if isinstance(result, Exception):
                # Handle unexpected exceptions
                query_id = prepared_inputs[i][1]
                query_results[original_index] = SavedQueryResult(
                    query_id=query_id,
                    query_name="Unknown",
                    success=False,
                    result=None,
                    error=str(result),
                    execution_time_ms=0,
                )
                failed_queries += 1
            else:
                query_results[original_index] = result
                if result.success:
                    successful_queries += 1
                else:
                    failed_queries += 1

        total_execution_time = (time.time() - start_time) * 1000

        # Determine overall success status
        overall_success = failed_queries == 0
        partial_success = successful_queries > 0 and failed_queries > 0

        message = "All queries executed successfully"
        if partial_success:
            message = f"Partial success: {successful_queries}/{total_queries} queries succeeded"
        elif failed_queries == total_queries:
            message = "All queries failed"

        return {
            "success": overall_success,
            "message": message,
            "data": [r for r in query_results if r is not None],  # Preserve input order
            "partial_success": partial_success,
            "total_queries": total_queries,
            "successful_queries": successful_queries,
            "failed_queries": failed_queries,
            "total_execution_time_ms": total_execution_time,
        }
