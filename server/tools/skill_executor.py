"""Generic skill executor - single tool for all external API integrations."""

import asyncio
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from agents import function_tool
from agents.run_context import RunContextWrapper

from server.services.skill_discovery import SkillApiConfig, SkillConfig, SkillCredentialConfig, SkillDiscovery
from server.services.skill_registry import SkillRegistry
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

SKILL_SOURCE_BYAAN = "byaan"
SKILL_SOURCE_CUSTOM = "custom"


def _build_custom_skill_config(skill_data: dict) -> SkillConfig:
    return SkillConfig(
        name=skill_data.get("name", ""),
        display_name=skill_data.get("name", ""),
        description=skill_data.get("description", ""),
        emoji="📝",
        homepage="",
        api=SkillApiConfig(
            base_url=skill_data.get("api_base_url", ""),
            type=skill_data.get("api_type", "rest"),
            auth_type=skill_data.get("api_auth_type", "bearer"),
            domain=skill_data.get("api_domain", ""),
        ),
        credentials=[SkillCredentialConfig(key="api_key", label="API Key")],
    )


def _build_request_headers(config: SkillConfig, credentials: dict) -> dict[str, str]:
    headers = {"Content-Type": "application/json", **config.api.headers}
    api_key = credentials.get("api_key", "")
    if api_key:
        if config.api.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers["Authorization"] = api_key
    return headers


def _is_allowed_domain(url: str, config: SkillConfig) -> bool:
    parsed = urlparse(url)
    return config.api.is_allowed_host(parsed.netloc)


def _resolve_url(config: SkillConfig, credentials: dict, endpoint_path: str) -> str:
    return config.api.base_url.rstrip("/") + "/" + endpoint_path.lstrip("/")


def _get_credentials_for_skill(ctx: RunContextWrapper[Any], skill_name: str, scope: str = "") -> dict:
    if scope:
        return ctx.context.get(f"{skill_name}:{scope}_credentials", {})
    return ctx.context.get(f"{skill_name}:user_credentials") or ctx.context.get(f"{skill_name}:org_credentials", {})


def _validate_skill_request(
    ctx: RunContextWrapper[Any], skill_name: str, scope: str
) -> tuple[dict | None, SkillConfig | None, str | None]:
    """
    Validate a skill API request. Returns (credentials, config, error_json).
    If error_json is not None, return it directly as the tool result.
    """
    custom_skills = ctx.context.get("custom_skills", {})
    if skill_name in custom_skills:
        skill_data = custom_skills[skill_name]
        if not skill_data.get("can_execute_api", False):
            return (
                None,
                None,
                json.dumps(
                    {
                        "success": False,
                        "error": f"Custom skill '{skill_name}' is informational only and has no API configuration.",
                    }
                ),
            )
        creds = skill_data.get("credentials", {})
        creds["domain_active"] = skill_data.get("domain_active", True)
        config = _build_custom_skill_config(skill_data)
        return creds, config, None

    credentials = _get_credentials_for_skill(ctx, skill_name, scope)
    if not credentials:
        return (
            None,
            None,
            json.dumps(
                {
                    "success": False,
                    "error": f"{skill_name.title()} not configured. Add credentials in Settings > Skills.",
                }
            ),
        )

    config = SkillDiscovery.get_skill_config(skill_name)
    if not config:
        return (
            None,
            None,
            json.dumps(
                {
                    "success": False,
                    "error": f"Unknown skill: {skill_name}. Available skills: {SkillDiscovery.get_skill_names()}",
                }
            ),
        )

    return credentials, config, None


def _camel_to_snake(name: str) -> str:
    """Convert PascalCase/camelCase to snake_case (e.g., FilterLogEvents -> filter_log_events)."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)).lower()


async def _execute_aws_request(
    config: SkillConfig, credentials: dict, action: str, params: dict
) -> tuple[dict | None, str | None]:
    """Execute an AWS API request via boto3. Returns (response_data, error_json)."""
    use_default_chain = credentials.get("auth_mode") == "iam_role"
    aws_access_key_id = credentials.get("aws_access_key_id", "")
    aws_secret_access_key = credentials.get("aws_secret_access_key", "")
    aws_region = credentials.get("aws_region", "us-east-1")

    if not use_default_chain and (not aws_access_key_id or not aws_secret_access_key):
        return None, json.dumps(
            {"success": False, "error": "AWS credentials (access key and secret key) are required."}
        )

    service_name = config.api.aws_service
    if not service_name:
        return None, json.dumps({"success": False, "error": "AWS service name not configured for this skill."})

    def _call_boto3():
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

        try:
            if use_default_chain:
                # AWS default credential chain: env vars -> shared config -> IMDS
                # instance profile / ECS task role. No long-lived keys stored.
                client = boto3.client(service_name, region_name=aws_region)
            else:
                client = boto3.client(
                    service_name,
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                    region_name=aws_region,
                )
            method_name = _camel_to_snake(action)
            method = getattr(client, method_name, None)
            if not method:
                return None, json.dumps(
                    {
                        "success": False,
                        "error": f"Unknown action '{action}' (method '{method_name}') for AWS service '{service_name}'.",
                    }
                )
            response = method(**params)
            response.pop("ResponseMetadata", None)
            return response, None
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_msg = e.response["Error"]["Message"]
            return None, json.dumps({"success": False, "error": f"AWS {error_code}: {error_msg}"})
        except NoCredentialsError:
            return None, json.dumps(
                {
                    "success": False,
                    "error": (
                        "No AWS credentials found via the default chain. Attach an IAM role with "
                        "CloudWatch Logs read access to this instance/task (on EC2, containers also need "
                        "IMDSv2 hop limit 2), or switch the skill to access keys."
                    ),
                }
            )
        except BotoCoreError as e:
            return None, json.dumps({"success": False, "error": f"AWS error: {str(e)}"})
        except Exception as e:
            return None, json.dumps({"success": False, "error": f"AWS request failed: {str(e)}"})

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_boto3)


def _build_request_body(
    body: str, is_graphql: bool, graphql_query: str, graphql_variables: str
) -> tuple[dict | None, str | None]:
    """Build request body. Returns (body_dict, error_json)."""
    if is_graphql:
        request_body = {"query": graphql_query}
        if graphql_variables:
            try:
                request_body["variables"] = json.loads(graphql_variables)
            except json.JSONDecodeError:
                return None, json.dumps({"success": False, "error": "Invalid graphql_variables JSON"})
        return request_body, None

    if body:
        try:
            return json.loads(body), None
        except json.JSONDecodeError:
            return None, json.dumps({"success": False, "error": "Invalid body JSON"})

    return None, None


async def _execute_http_request(
    url: str, method: str, headers: dict, body: dict | None, is_graphql: bool
) -> tuple[dict | None, str | None]:
    """Execute HTTP request. Returns (response_data, error_json)."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.request(
                method="POST" if is_graphql else method.upper(),
                url=url,
                headers=headers,
                json=body,
                timeout=30.0,
            )

            if response.status_code >= 400:
                error_text = response.text
                try:
                    error_data = response.json()
                    error_text = error_data.get("message", error_data.get("error", response.text))
                except Exception:
                    pass
                return None, json.dumps(
                    {
                        "success": False,
                        "error": error_text,
                        "status_code": response.status_code,
                    }
                )

            data = response.json()

            if is_graphql and "errors" in data:
                return None, json.dumps({"success": False, "errors": data["errors"]})

            result_data = data.get("data") if is_graphql else data
            return result_data, None

        except httpx.TimeoutException:
            return None, json.dumps({"success": False, "error": "Request timed out"})
        except Exception as e:
            from server.utils.error_sanitizer import sanitize_error_message

            return None, json.dumps({"success": False, "error": sanitize_error_message(e)})


@function_tool
async def execute_skill_api(
    ctx: RunContextWrapper[Any],
    skill_name: str,
    endpoint_path: str,
    method: str = "GET",
    body: str = "",
    headers: str = "",
    is_graphql: bool = False,
    graphql_query: str = "",
    graphql_variables: str = "",
    scope: str = "",
) -> str:
    """
    Execute an API request for an enabled skill (Byaan or API-enabled custom skill).

    Recommended sequence: search_enabled_skills -> get_skill_definition -> execute_skill_api.
    Use endpoint_path with just the path (e.g., "/graphql", "/v1/search"), not the full URL.
    The base URL is constructed automatically from the skill configuration.

    Args:
        ctx: Run context with skill credentials
        skill_name: Name of the skill (e.g., "notion", "linear")
        endpoint_path: API endpoint path (e.g., "/graphql", "/v1/search")
        method: HTTP method (GET, POST, PATCH, DELETE)
        body: JSON string for request body (for REST requests)
        headers: Optional JSON string of additional headers
        is_graphql: Set to True for GraphQL requests
        graphql_query: GraphQL query string (required if is_graphql=True)
        graphql_variables: JSON string of GraphQL variables
        scope: Credential scope to use ("user" or "org"). If empty, tries user first then org.

    Returns:
        JSON response with success status and data/error
    """
    credentials, config, error = _validate_skill_request(ctx, skill_name, scope)
    if error:
        return error

    if not credentials.get("domain_active", credentials.get("subdomain_active", True)):
        return json.dumps(
            {
                "success": False,
                "error": f"{config.display_name} domain is not whitelisted. Enable it in Settings > Skills > Whitelisted Domains.",
            }
        )

    if config.api.type == "aws":
        params = {}
        if body:
            try:
                params = json.loads(body)
            except json.JSONDecodeError:
                return json.dumps({"success": False, "error": "Invalid body JSON for AWS request"})
        result_data, error = await _execute_aws_request(config, credentials, endpoint_path, params)
        if error:
            return error
        return json.dumps({"success": True, "data": result_data}, indent=2, default=str)

    url = _resolve_url(config, credentials, endpoint_path)

    if not _is_allowed_domain(url, config):
        return json.dumps(
            {
                "success": False,
                "error": f"Resolved domain not allowed for {skill_name}. Allowed: {config.api.allowed_domains}",
            }
        )

    request_headers = _build_request_headers(config, credentials)
    if headers:
        try:
            request_headers.update(json.loads(headers))
        except json.JSONDecodeError:
            pass

    request_body, error = _build_request_body(body, is_graphql, graphql_query, graphql_variables)
    if error:
        return error

    result_data, error = await _execute_http_request(url, method, request_headers, request_body, is_graphql)
    if error:
        return error

    return json.dumps({"success": True, "data": result_data}, indent=2, default=str)


@function_tool
async def search_enabled_skills(
    ctx: RunContextWrapper[Any],
    query: str,
) -> str:
    """
    Search for enabled skills by keyword.

    Use this when the user mentions external services (Linear, Notion, GitHub, etc.)
    or asks about tickets, issues, pages, tasks, projects, etc.
    This should be the first step in the skill workflow.

    Returns both Byaan skills (can execute APIs) and custom skills (informational only).

    Args:
        ctx: Run context with enabled skill names
        query: Search keyword (e.g., "tickets", "issues", "pages", "notion", "linear")

    Returns:
        JSON array of matching skills with name, description, source, and can_execute_api flag.
    """
    enabled_skills = ctx.context.get("enabled_skills", {})
    enabled_names = ctx.context.get("enabled_skill_names", [])
    custom_skills = ctx.context.get("custom_skills", {})

    if not enabled_names and not custom_skills:
        return json.dumps(
            {
                "success": False,
                "error": "No skills are enabled. Configure skills in Settings > Skills.",
            }
        )

    skill_scopes: dict[str, list[str]] = {}
    for key, data in enabled_skills.items():
        name = data.get("skill_name", key.split(":")[0])
        skill_scopes.setdefault(name, []).append(data.get("scope", "user"))

    results = []
    query_lower = query.lower()

    byaan_results = SkillDiscovery.search_skills(query, enabled_names)
    for skill in byaan_results:
        scopes = skill_scopes.get(skill["name"], ["user"])
        skill["scopes"] = scopes
        skill["source"] = SKILL_SOURCE_BYAAN
        skill["can_execute_api"] = True
        scope_info = []
        if "user" in scopes:
            scope_info.append("personal key available")
        if "org" in scopes:
            scope_info.append("team shared key available")
        skill["credentials_info"] = ", ".join(scope_info)
        results.append(skill)

    for name, skill_data in custom_skills.items():
        searchable = f"{name} {skill_data.get('description', '')}".lower()
        if query_lower in searchable:
            can_api = skill_data.get("can_execute_api", False)
            entry = {
                "name": name,
                "display_name": name,
                "description": skill_data.get("description", ""),
                "source": SKILL_SOURCE_CUSTOM,
                "can_execute_api": can_api,
                "created_by_name": skill_data.get("created_by_name", ""),
            }
            if can_api:
                entry["api_type"] = skill_data.get("api_type", "rest")
                entry["api_domain"] = skill_data.get("api_domain", "")
            results.append(entry)

    if not results:
        all_available = list(enabled_names) + list(custom_skills.keys())
        return json.dumps(
            {
                "success": True,
                "message": f"No skills found matching '{query}'.",
                "available_skills": all_available,
            }
        )

    return json.dumps({"success": True, "skills": results}, indent=2)


@function_tool
async def get_skill_definition(
    ctx: RunContextWrapper[Any],
    skill_name: str,
) -> str:
    """
    Get the full instructions/documentation for a specific skill.

    Call this AFTER search_enabled_skills() to load the complete documentation.
    For Byaan skills, this returns API documentation for use with execute_skill_api().
    For custom skills, this returns instructional content (no API execution available).

    Args:
        ctx: Run context with enabled skill names
        skill_name: Exact skill name (e.g., "linear", "notion", "weekly-status-report")

    Returns:
        Full skill documentation/instructions.
    """
    enabled_names = ctx.context.get("enabled_skill_names", [])
    custom_skills = ctx.context.get("custom_skills", {})

    if skill_name in custom_skills:
        skill_data = custom_skills[skill_name]
        can_api = skill_data.get("can_execute_api", False)
        result = {
            "success": True,
            "skill_name": skill_name,
            "source": SKILL_SOURCE_CUSTOM,
            "can_execute_api": can_api,
            "description": skill_data.get("description", ""),
            "instructions": skill_data.get("instructions", ""),
            "created_by_name": skill_data.get("created_by_name", ""),
        }
        if can_api:
            result["api_type"] = skill_data.get("api_type", "rest")
            result["base_url"] = skill_data.get("api_base_url", "")
            result["note"] = "Custom skill with API. Use execute_skill_api() with endpoint_path (not full URL)."
        else:
            result["note"] = "Custom skill - informational only. Cannot use execute_skill_api()."
        return json.dumps(result, indent=2)

    if skill_name not in enabled_names:
        all_available = list(enabled_names) + list(custom_skills.keys())
        return json.dumps(
            {
                "success": False,
                "error": f"Skill '{skill_name}' is not enabled. Configure it in Settings > Skills.",
                "enabled_skills": all_available,
            }
        )

    docs = SkillRegistry.get_skill_docs(skill_name)
    if not docs:
        return json.dumps(
            {
                "success": False,
                "error": f"No documentation found for skill '{skill_name}'.",
            }
        )

    config = SkillDiscovery.get_skill_config(skill_name)
    api_type = config.api.type if config else "rest"
    base_url = config.api.base_url if config else ""

    if api_type == "aws":
        note = (
            "AWS skill: set endpoint_path to the action name (e.g., 'FilterLogEvents') "
            "and body to a JSON string of parameters. method should be 'POST'."
        )
    else:
        note = "Use endpoint_path parameter with just the path (e.g., '/graphql'), not the full URL."

    return json.dumps(
        {
            "success": True,
            "skill_name": skill_name,
            "source": SKILL_SOURCE_BYAAN,
            "can_execute_api": True,
            "api_type": api_type,
            "base_url": base_url,
            "note": note,
            "documentation": docs,
        },
        indent=2,
    )


@function_tool
async def save_skill_query(
    ctx: RunContextWrapper[Any],
    skill_name: str,
    name: str,
    endpoint_path: str,
    method: str = "GET",
    body: str = "",
    is_graphql: bool = False,
    graphql_query: str = "",
    graphql_variables: str = "",
    scope: str = "",
) -> str:
    """
    Save a skill API query for later use in dashboards.

    Executes the API call and saves it as a reusable query for dashboard live data.
    IMPORTANT: Only works for Byaan-administered skills (Linear, Notion, etc.).
    Use endpoint_path with just the path (e.g., "/graphql"), not the full URL.

    Args:
        ctx: Run context with skill credentials
        skill_name: Name of the skill (e.g., "notion", "linear")
        name: Human-readable name for the saved query
        endpoint_path: API endpoint path (e.g., "/graphql", "/v1/search")
        method: HTTP method (GET, POST, PATCH, DELETE)
        body: JSON string for request body (for REST requests)
        is_graphql: Set to True for GraphQL requests
        graphql_query: GraphQL query string (required if is_graphql=True)
        graphql_variables: JSON string of GraphQL variables
        scope: Credential scope to use ("user" or "org"). If empty, tries user first then org.

    Returns:
        JSON response with query_id for dashboard use
    """
    credentials, config, error = _validate_skill_request(ctx, skill_name, scope)
    if error:
        return error

    if not credentials.get("domain_active", credentials.get("subdomain_active", True)):
        return json.dumps(
            {
                "success": False,
                "error": f"{config.display_name} domain is not whitelisted. Enable it in Settings > Skills > Whitelisted Domains.",
            }
        )

    is_aws = config.api.type == "aws"

    if is_aws:
        params = {}
        if body:
            try:
                params = json.loads(body)
            except json.JSONDecodeError:
                return json.dumps({"success": False, "error": "Invalid body JSON for AWS request"})
        result_data, error = await _execute_aws_request(config, credentials, endpoint_path, params)
        if error:
            return error
    else:
        url = _resolve_url(config, credentials, endpoint_path)
        if not _is_allowed_domain(url, config):
            return json.dumps(
                {
                    "success": False,
                    "error": f"Resolved domain not allowed for {skill_name}. Allowed: {config.api.allowed_domains}",
                }
            )
        request_headers = _build_request_headers(config, credentials)
        request_body, error = _build_request_body(body, is_graphql, graphql_query, graphql_variables)
        if error:
            return error
        result_data, error = await _execute_http_request(url, method, request_headers, request_body, is_graphql)
        if error:
            return error

    notebook_id = ctx.context.get("notebook_id")
    if not notebook_id:
        return json.dumps({"success": False, "error": "No notebook context found"})

    tenant_id = ctx.context.get("tenant_id")
    user_id = ctx.context.get("user_id")

    actual_scope = scope
    if not actual_scope:
        actual_scope = "user" if ctx.context.get(f"{skill_name}:user_credentials") else "org"

    try:
        from server.db.session import get_async_session_context
        from server.repositories.queries import QueryRepository
        from server.services.dataset import DatasetService
        from server.utils.schema_generator import generate_schema_from_response

        async with get_async_session_context() as session:
            dataset = await DatasetService.create_dataset(
                session=session,
                type="skill_api",
                notebook_id=notebook_id,
                skill_name=skill_name,
                skill_scope=actual_scope,
                tenant_id=tenant_id,
                created_by=user_id,
            )

            api_config = {
                "endpoint_path": endpoint_path,
                "method": method.upper() if not is_graphql else "POST",
                "body": body,
                "is_graphql": is_graphql,
                "graphql_query": graphql_query,
                "graphql_variables": graphql_variables,
            }
            if is_aws:
                api_config["is_aws"] = True
                api_config["action"] = endpoint_path
                api_config["params"] = body

            generated_schema = None
            if result_data:
                try:
                    if isinstance(result_data, list):
                        generated_schema = generate_schema_from_response(result_data)
                    elif isinstance(result_data, dict):
                        for key, value in result_data.items():
                            if isinstance(value, dict) and "nodes" in value:
                                generated_schema = generate_schema_from_response(value["nodes"])
                                break
                            elif isinstance(value, list):
                                generated_schema = generate_schema_from_response(value)
                                break
                        if not generated_schema:
                            generated_schema = generate_schema_from_response([result_data])
                except Exception as e:
                    logger.warning(f"Failed to generate schema for skill query: {e}")
                    generated_schema = {}

            query_repo = QueryRepository(session)
            saved_query = await query_repo.create(
                {
                    "name": name,
                    "query": json.dumps(api_config),
                    "output_schema": json.dumps(generated_schema) if generated_schema else "{}",
                    "dataset_id": dataset.id,
                    "notebook_id": notebook_id,
                    "created_by": str(user_id) if user_id else None,
                }
            )

            return json.dumps(
                {
                    "success": True,
                    "query_id": str(saved_query.id),
                    "query_name": name,
                    "skill_name": skill_name,
                    "scope": actual_scope,
                    "data": result_data,
                    "schema": generated_schema,
                    "data_path": "result.data[N].result",
                    "mapping_instructions": "CRITICAL: The 'data' field above shows EXACTLY what the batch endpoint will return. In dashboard HTML, access as: const items = result.data[0]?.result || []; Then map fields directly: items.map(item => item.id, item.name, etc). NEVER use nested paths like .teams.nodes or .viewer",
                },
                indent=2,
                default=str,
            )

    except Exception as e:
        logger.error(f"Failed to save skill query: {e}")
        return json.dumps({"success": False, "error": f"Failed to save query: {str(e)}"})


@function_tool
async def update_custom_skill(
    ctx: RunContextWrapper[Any],
    skill_name: str,
    instructions: str | None = None,
    description: str | None = None,
) -> str:
    """
    Update a custom skill's instructions or description through chat.

    Use this when the user wants to modify or improve a custom skill's behavior.
    Only the skill creator can update their own skills.

    Args:
        ctx: Run context with tenant/user info
        skill_name: Name of the custom skill to update
        instructions: New instructions for the skill (optional)
        description: New description for the skill (optional)

    Returns:
        JSON response with success status
    """
    tenant_id = ctx.context.get("tenant_id")
    user_id = ctx.context.get("user_id")

    if not tenant_id or not user_id:
        return json.dumps({"success": False, "error": "Missing tenant or user context"})

    custom_skills = ctx.context.get("custom_skills", {})
    if skill_name not in custom_skills:
        return json.dumps(
            {
                "success": False,
                "error": f"Custom skill '{skill_name}' not found. Available custom skills: {list(custom_skills.keys())}",
            }
        )

    skill_data = custom_skills[skill_name]
    skill_id = skill_data.get("id")

    if not skill_id:
        return json.dumps({"success": False, "error": f"Skill '{skill_name}' has no ID"})

    if str(skill_data.get("created_by")) != str(user_id):
        return json.dumps(
            {
                "success": False,
                "error": f"Only the creator can update '{skill_name}'. This skill was created by {skill_data.get('created_by_name', 'another user')}.",
            }
        )

    if not instructions and not description:
        return json.dumps(
            {"success": False, "error": "Provide at least one field to update: instructions or description"}
        )

    try:
        from server.db.session import get_async_session_context
        from server.repositories.custom_skill import CustomSkillRepository

        updates = {}
        if instructions:
            updates["instructions"] = instructions
        if description:
            updates["description"] = description

        async with get_async_session_context() as session:
            repo = CustomSkillRepository(session)
            updated_skill = await repo.update(skill_id, tenant_id, user_id, **updates)

            if not updated_skill:
                return json.dumps({"success": False, "error": "Failed to update skill"})

            return json.dumps(
                {
                    "success": True,
                    "skill_name": skill_name,
                    "updated_fields": list(updates.keys()),
                    "message": f"Successfully updated {skill_name}",
                },
                indent=2,
            )

    except Exception as e:
        logger.error(f"Failed to update custom skill: {e}")
        return json.dumps({"success": False, "error": f"Failed to update skill: {str(e)}"})


@function_tool
async def create_custom_skill(
    ctx: RunContextWrapper[Any],
    name: str,
    description: str,
    instructions: str,
) -> str:
    """
    Create a new custom skill that the user or team can reuse across conversations.

    Use this when the user wants to save reusable instructions, workflows, or knowledge
    as a named skill. The skill will be available for future conversations.

    Args:
        ctx: Run context with tenant and user info
        name: Short, descriptive name for the skill (e.g., "weekly-status-report", "code-review-checklist")
        description: Brief summary of what the skill does (max 500 chars)
        instructions: Full instructions/content for the skill. This is what the agent will follow when the skill is invoked.

    Returns:
        JSON with success status, skill_id, and skill_name
    """
    tenant_id = ctx.context.get("tenant_id")
    user_id = ctx.context.get("user_id")

    if not tenant_id or not user_id:
        return json.dumps({"success": False, "error": "Missing tenant or user context"})

    try:
        from server.db.session import get_async_session_context
        from server.repositories.custom_skill import CustomSkillRepository

        async with get_async_session_context() as session:
            repo = CustomSkillRepository(session)
            skill = await repo.create(
                tenant_id=tenant_id,
                created_by=user_id,
                name=name,
                description=description,
                instructions=instructions,
                scope="user",
                skill_type="general",
            )

            custom_skills = ctx.context.get("custom_skills", {})
            custom_skills[skill.name] = {
                "description": skill.description,
                "instructions": skill.instructions,
                "can_execute_api": False,
                "created_by_name": skill.creator.full_name if skill.creator else "",
            }
            ctx.context["custom_skills"] = custom_skills

            return json.dumps(
                {
                    "success": True,
                    "skill_id": str(skill.id),
                    "skill_name": skill.name,
                    "message": f"Skill '{skill.name}' created successfully.",
                }
            )
    except Exception as e:
        logger.error(f"Failed to create custom skill: {e}")
        return json.dumps({"success": False, "error": f"Failed to create skill: {str(e)}"})


def get_skill_management_tools() -> list:
    return [create_custom_skill]


def get_skill_executor_tools() -> list:
    return [search_enabled_skills, get_skill_definition, execute_skill_api, update_custom_skill]
