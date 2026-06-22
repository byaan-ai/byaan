import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.extensions.models.litellm_model import LitellmModel

from agents import Agent, ModelSettings
from sqlalchemy.ext.asyncio import AsyncSession

from server.constants.models import MODELS_BY_PROVIDER
from server.db.session import get_async_session
from server.models.llm_connections import LLMConnection
from server.repositories.llm_connections import LLMConnectionRepository
from server.services.codex_oauth_service import get_valid_codex_token
from server.services.crypto_service import CryptoService
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


def is_claude_code_authenticated() -> bool:
    """
    Check if Claude Code can authenticate via credentials file OR environment variable.

    Checks in order:
    1. OAuth credentials file (~/.claude/.credentials.json) - from /login flow
    2. ANTHROPIC_API_KEY environment variable - for direct API key usage

    Returns:
        True if either authentication method is available, False otherwise.
    """
    # Check for OAuth credentials file (Linux/Docker)
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    if credentials_path.exists():
        logger.debug("[CLAUDE AUTH] Found credentials file")
        return True

    # Check for API key environment variable (fallback)
    if os.environ.get("ANTHROPIC_API_KEY"):
        logger.debug("[CLAUDE AUTH] Found ANTHROPIC_API_KEY env var")
        return True

    return False


async def build_litellm_params(conn: LLMConnection, session=None, model: str = None) -> dict[str, Any]:
    if not conn.config:
        raise ValueError("Missing encrypted config for LLMConnection")

    cfg = await CryptoService.decrypt_config(conn.config, session)

    # For Azure and Bedrock, check "models" field (can be string or array)
    if conn.type in ["azure", "bedrock"]:
        if not model:
            models_config = cfg.get("models")
            if models_config:
                # If it's a string, use it directly. If array, take first element
                if isinstance(models_config, list):
                    model_name = models_config[0] if models_config else None
                else:
                    model_name = models_config
            else:
                # Fall back to `model` key for compatibility with standalone configs
                model_name = cfg.get("model")
        else:
            model_name = model
    else:
        model_name = model or cfg.get("model")

    logger.debug(f"build_litellm_params: conn.type={conn.type}, model={model}, model_name={model_name}")

    if model_name and conn.type not in [
        "openrouter",
        "azure",
        "bedrock",
        "groq",
        "xai",
    ]:
        if model_name.startswith("openai/"):
            model_name = model_name.replace("openai/", "", 1)
            logger.debug(f"Removed 'openai/' prefix from model name: {model_name}")
        elif model_name.startswith("anthropic/"):
            model_name = model_name.replace("anthropic/", "", 1)
            logger.debug(f"Removed 'anthropic/' prefix from model name: {model_name}")
        elif model_name.startswith("claude_code/"):
            model_name = model_name.replace("claude_code/", "", 1)
            logger.debug(f"Removed 'claude_code/' prefix from model name: {model_name}")
        elif model_name.startswith("codex/"):
            model_name = model_name.replace("codex/", "", 1)
            logger.debug(f"Removed 'codex/' prefix from model name: {model_name}")

    if not model_name:
        provider_models = MODELS_BY_PROVIDER.get(conn.type, [])
        if provider_models:
            default_with_prefix = provider_models[0]
            own_prefix = f"{conn.type}/"
            if default_with_prefix.startswith(own_prefix):
                model_name = default_with_prefix[len(own_prefix) :]
            else:
                model_name = default_with_prefix
        elif conn.type == "azure":
            raise ValueError("Azure deployment name must be provided in the configuration.")
        elif conn.type == "bedrock":
            raise ValueError("Bedrock model ID must be provided in the configuration.")
        else:
            raise ValueError(f"Model must be provided for {conn.type} connection. No default available.")

    if conn.type == "openrouter":
        if not model_name.startswith("openrouter/"):
            model_name = f"openrouter/{model_name}"

        params: dict[str, Any] = {
            "model": model_name,
        }

        if cfg.get("api_key"):
            params["api_key"] = cfg["api_key"]
            os.environ["OPENROUTER_API_KEY"] = cfg["api_key"]

        if cfg.get("api_base"):
            params["base_url"] = cfg["api_base"]
        else:
            params["base_url"] = "https://openrouter.ai/api/v1"

        if cfg.get("site_url"):
            os.environ["OR_SITE_URL"] = cfg["site_url"]
        if cfg.get("app_name"):
            os.environ["OR_APP_NAME"] = cfg["app_name"]

    elif conn.type == "azure":
        # For Azure, model_name should be the deployment name
        # LiteLLM expects format: azure/<deployment_name>
        if not model_name.startswith("azure/"):
            model_name = f"azure/{model_name}"

        params: dict[str, Any] = {
            "model": model_name,
        }

        # Set Azure credentials as environment variables
        if cfg.get("api_key"):
            os.environ["AZURE_API_KEY"] = cfg["api_key"]
        if cfg.get("api_base"):
            os.environ["AZURE_API_BASE"] = cfg["api_base"]
        if cfg.get("api_version"):
            os.environ["AZURE_API_VERSION"] = cfg["api_version"]

        logger.debug(
            f"Azure params: model={model_name}, api_base={cfg.get('api_base')}, api_version={cfg.get('api_version')}"
        )

    elif conn.type == "bedrock":
        # For Bedrock, ensure model starts with bedrock/ prefix
        # Pass the rest of the model identifier through unchanged (including global.anthropic. if present)
        if not model_name.startswith("bedrock/"):
            model_name = f"bedrock/{model_name}"

        params: dict[str, Any] = {
            "model": model_name,
        }

        # Set AWS credentials as environment variables
        if cfg.get("aws_access_key_id"):
            os.environ["AWS_ACCESS_KEY_ID"] = cfg["aws_access_key_id"]
        if cfg.get("aws_secret_access_key"):
            os.environ["AWS_SECRET_ACCESS_KEY"] = cfg["aws_secret_access_key"]
        if cfg.get("aws_region_name"):
            os.environ["AWS_REGION_NAME"] = cfg["aws_region_name"]

        logger.debug(f"Bedrock params: model={model_name}, region={cfg.get('aws_region_name')}")

    elif conn.type == "groq":
        # For Groq, ensure model starts with groq/ prefix
        if not model_name.startswith("groq/"):
            model_name = f"groq/{model_name}"

        params: dict[str, Any] = {
            "model": model_name,
        }

        # Set Groq API key as environment variable
        if cfg.get("api_key"):
            params["api_key"] = cfg["api_key"]
            os.environ["GROQ_API_KEY"] = cfg["api_key"]

        logger.debug(f"Groq params: model={model_name}")

    elif conn.type == "xai":
        # For xAI, ensure model starts with xai/ prefix
        if not model_name.startswith("xai/"):
            model_name = f"xai/{model_name}"

        params: dict[str, Any] = {
            "model": model_name,
        }

        # Set xAI API key as environment variable
        if cfg.get("api_key"):
            params["api_key"] = cfg["api_key"]
            os.environ["XAI_API_KEY"] = cfg["api_key"]

        logger.debug(f"xAI params: model={model_name}")

    elif conn.type == "codex":
        params: dict[str, Any] = {
            "model": model_name,
            "_codex": True,
            "_codex_connection_id": str(conn.id),
        }

        access_token = cfg.get("access_token")
        if access_token:
            params["api_key"] = access_token
            params["base_url"] = "https://chatgpt.com/backend-api/codex"

        logger.debug(f"Codex params: model={model_name}")

    elif conn.type == "openai":
        params: dict[str, Any] = {
            "model": model_name,
        }

        # Set OpenAI credentials as environment variables
        if cfg.get("api_key"):
            params["api_key"] = cfg["api_key"]
            os.environ["OPENAI_API_KEY"] = cfg["api_key"]
        if cfg.get("api_base"):
            params["base_url"] = cfg["api_base"]
            os.environ["OPENAI_API_BASE"] = cfg["api_base"]

        logger.debug(f"OpenAI params: model={model_name}")

    elif conn.type == "anthropic":
        if not model_name.startswith("anthropic/"):
            model_name = f"anthropic/{model_name}"

        params: dict[str, Any] = {
            "model": model_name,
        }

        # Set Anthropic credentials as environment variables
        if cfg.get("api_key"):
            params["api_key"] = cfg["api_key"]
            os.environ["ANTHROPIC_API_KEY"] = cfg["api_key"]
        if cfg.get("api_base"):
            params["base_url"] = cfg["api_base"]

        logger.debug(f"Anthropic params: model={model_name}")

    elif conn.type == "claude_code":
        params: dict[str, Any] = {
            "model": model_name,
        }

        # Verify Claude Code credentials exist
        credentials_path = Path.home() / ".claude" / ".credentials.json"
        if not credentials_path.exists():
            raise ValueError(
                "Claude Code credentials not found at ~/.claude/.credentials.json. "
                "Please run 'claude' in terminal to authenticate with Claude Code."
            )
        logger.info(f"Using Claude Code OAuth authentication for model: {model_name}")
        # Don't set api_key - Claude SDK will use OAuth from ~/.claude/.credentials.json

        logger.debug(f"Claude Code params: model={model_name}")

    else:
        params: dict[str, Any] = {
            "model": model_name,
        }
        if cfg.get("api_key"):
            params["api_key"] = cfg["api_key"]
        # Use base_url for LitellmModel compatibility
        if cfg.get("api_base"):
            params["base_url"] = cfg["api_base"]
        if cfg.get("extra_headers"):
            params["extra_headers"] = cfg["extra_headers"]

    extra_args = cfg.get("extra_args") or {}
    if isinstance(extra_args, dict):
        params.update(extra_args)

    logger.debug(f"build_litellm_params returning: {params}")
    return params


class ModelService:
    @staticmethod
    async def get_agent_with_dynamic_model(
        name: str,
        instructions: str,
        tools: list | None = None,
        mcp_servers: list | None = None,
        handoffs: list | None = None,
        llm_connection_id: str | None = None,
        model: str | None = None,
        force_refresh: bool = False,
        model_settings: ModelSettings | dict | None = None,
        tool_choice: str | None = None,
    ) -> Agent:
        try:
            if not llm_connection_id:
                raise ValueError("LLM connection ID is required. Please configure an AI model.")

            model_instance = await ModelService.get_litellm_model_instance(llm_connection_id, model)

            if not model_instance:
                raise ValueError(
                    f"Failed to initialize model from LLM connection: {llm_connection_id}. Please ensure the connection has a model configured."
                )

            # Handle model_settings dynamically
            agent_model_settings = None
            if model_settings is not None:
                # If model_settings is already a ModelSettings instance, use it directly
                if isinstance(model_settings, ModelSettings):
                    agent_model_settings = model_settings
                # If it's a dictionary, create ModelSettings from it
                elif isinstance(model_settings, dict):
                    agent_model_settings = ModelSettings(**model_settings)
            elif tool_choice is not None:
                # If only tool_choice is provided, create ModelSettings with it
                agent_model_settings = ModelSettings(tool_choice=tool_choice)

            # Create agent with optional model_settings
            agent_kwargs = {
                "name": name,
                "instructions": instructions,
                "tools": tools or [],
                "mcp_servers": mcp_servers or [],
                "handoffs": handoffs or [],
                "model": model_instance,
            }

            # Only add model_settings if it's not None
            if agent_model_settings is not None:
                agent_kwargs["model_settings"] = agent_model_settings

            return Agent(**agent_kwargs)

        except Exception as e:
            logger.error(
                f"Error creating agent with dynamic model: {str(e)}",
                posthog_context={
                    "function": "ModelService.get_agent_with_dynamic_model",
                    "llm_connection_id": llm_connection_id,
                    "model": model,
                },
            )
            raise

    @staticmethod
    async def get_litellm_model_instance(llm_connection_id: str, model: str = None) -> "LitellmModel | None":
        try:
            async for db in get_async_session():
                repo = LLMConnectionRepository(db)
                llm_connection = await repo.get(llm_connection_id)

                if not llm_connection:
                    error = ValueError(f"LLMConnection not found: {llm_connection_id}")
                    logger.error(
                        f"LLMConnection not found: {llm_connection_id}",
                        exc_info=error,
                        posthog_context={
                            "function": "ModelService.get_litellm_model_instance",
                            "llm_connection_id": llm_connection_id,
                        },
                    )
                    return None

                litellm_params = await build_litellm_params(llm_connection, db, model)
                logger.info(f"LiteLLM params for {llm_connection_id}: {litellm_params}")

                if not litellm_params.get("model"):
                    error = ValueError(f"No model specified for LLMConnection: {llm_connection_id}")
                    logger.error(
                        f"No model specified for LLMConnection: {llm_connection_id}. Params: {litellm_params}",
                        exc_info=error,
                        posthog_context={
                            "function": "ModelService.get_litellm_model_instance",
                            "llm_connection_id": llm_connection_id,
                            "litellm_params": str(litellm_params),
                        },
                    )
                    return None

                try:
                    if litellm_params.pop("_codex", False):
                        connection_id = litellm_params.pop("_codex_connection_id", None)
                        from openai import AsyncOpenAI

                        access_token = litellm_params.get("api_key", "")
                        account_id = None
                        if connection_id:
                            access_token, account_id = await get_valid_codex_token(connection_id, db)

                        codex_headers = {
                            "OpenAI-Beta": "responses=experimental",
                            "originator": "codex_cli_rs",
                        }
                        if account_id:
                            codex_headers["ChatGPT-Account-Id"] = account_id

                        codex_client = AsyncOpenAI(
                            api_key=access_token,
                            base_url="https://chatgpt.com/backend-api/codex",
                            default_headers=codex_headers,
                        )

                        from server.services.codex_responses_model import CodexResponsesModel

                        model_instance = CodexResponsesModel(
                            model=litellm_params["model"],
                            openai_client=codex_client,
                        )
                        logger.info(
                            f"Created OpenAIResponsesModel (Codex) for connection {llm_connection_id} with model {litellm_params['model']}"
                        )
                        return model_instance

                    # Lazy import LiteLLM only when actually creating a model instance
                    import time

                    start = time.perf_counter()
                    from agents.extensions.models.litellm_model import LitellmModel

                    logger.info(f"⏱️  LiteLLM import took: {time.perf_counter() - start:.3f}s")

                    model_instance = LitellmModel(**litellm_params)
                    logger.info(
                        f"Created LitellmModel for connection {llm_connection_id} with model {litellm_params['model']}"
                    )
                    return model_instance
                except Exception as model_error:
                    logger.error(
                        f"Failed to create LitellmModel with params {litellm_params}: {str(model_error)}",
                        posthog_context={
                            "function": "ModelService.get_litellm_model_instance.create_model",
                            "llm_connection_id": llm_connection_id,
                            "litellm_params": str(litellm_params),
                        },
                    )
                    raise

        except Exception as e:
            logger.error(
                f"Error creating LitellmModel instance: {str(e)}",
                posthog_context={
                    "function": "ModelService.get_litellm_model_instance",
                    "llm_connection_id": llm_connection_id,
                    "model": model,
                },
            )
            return None

    @staticmethod
    async def create_llm_connection(
        provider_type: str, model_config: dict[str, Any], db: AsyncSession
    ) -> LLMConnection | None:
        try:
            encrypted_config = await CryptoService.encrypt_config(model_config, db)

            llm_connection = LLMConnection(type=provider_type, config=encrypted_config)

            db.add(llm_connection)
            await db.commit()
            await db.refresh(llm_connection)

            logger.info(f"Created LLMConnection {llm_connection.id} for provider {provider_type}")
            return llm_connection

        except Exception as e:
            logger.error(
                f"Error creating LLMConnection: {str(e)}",
                posthog_context={
                    "function": "ModelService.create_llm_connection",
                    "provider_type": provider_type,
                },
            )
            await db.rollback()
            return None

    @staticmethod
    def get_supported_providers() -> dict[str, dict[str, Any]]:
        return {
            "openai": {
                "required_fields": ["api_key"],
                "optional_fields": ["api_base", "organization"],
                "example_config": {
                    "api_key": "sk-...",
                    "api_base": "https://api.openai.com/v1",
                },
            },
            "anthropic": {
                "required_fields": ["api_key"],
                "optional_fields": ["api_base"],
                "example_config": {
                    "api_key": "sk-ant-...",
                },
            },
            "claude_code": {
                "required_fields": [],
                "optional_fields": [],
                "example_config": {},
            },
            "codex": {
                "required_fields": [],
                "optional_fields": [],
                "example_config": {},
            },
            "openrouter": {
                "required_fields": ["api_key"],
                "optional_fields": ["api_base", "site_url", "app_name"],
                "example_config": {
                    "api_key": "sk-or-...",
                    "api_base": "https://openrouter.ai/api/v1",
                },
            },
            "azure": {
                "required_fields": ["api_key", "api_base", "api_version"],
                "optional_fields": ["models", "model"],
                "example_config": {
                    "api_key": "your-azure-api-key",
                    "api_base": "https://your-resource.openai.azure.com/",
                    "api_version": "2024-10-21",
                    "models": "your-deployment-name",
                    "model": "your-deployment-name",
                },
            },
            "bedrock": {
                "required_fields": [
                    "aws_access_key_id",
                    "aws_secret_access_key",
                    "aws_region_name",
                ],
                "optional_fields": ["models", "model"],
                "example_config": {
                    "aws_access_key_id": "AKIA...",
                    "aws_secret_access_key": "...",
                    "aws_region_name": "us-east-1",
                    "model": "bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
                },
            },
            "groq": {
                "required_fields": ["api_key"],
                "optional_fields": [],
                "example_config": {
                    "api_key": "gsk_...",
                },
            },
            "xai": {
                "required_fields": ["api_key"],
                "optional_fields": [],
                "example_config": {
                    "api_key": "xai-...",
                },
            },
        }

    @staticmethod
    def get_available_models(provider: str = None) -> list[str]:
        """Get available models for a provider or all providers.

        Models are defined in server/constants/models.py.
        Azure and Bedrock models are user-provided and stored in the database.
        """
        if not provider:
            return dict(MODELS_BY_PROVIDER)

        return MODELS_BY_PROVIDER.get(provider, [])

    @staticmethod
    def get_models_by_provider() -> dict[str, list[str]]:
        """Get all models organized by provider.

        Models are defined in server/constants/models.py.
        Azure and Bedrock models are user-provided and stored in the database.
        """
        return dict(MODELS_BY_PROVIDER)
