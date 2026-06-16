"""Model-specific prompt variant selection logic.

This module provides utilities for selecting the appropriate prompt variant
based on the LLM model being used. Different models have different strengths:

- GPT-5 models: Prefer markdown headers, explicit sequencing, positive framing, specific thresholds
- Default (Claude, Gemini, etc.): Excel at XML-structured prompts, nested instructions, reference-heavy context

"""

from __future__ import annotations

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


class ModelFamily:
    """Model family identifiers for prompt variant selection."""

    GPT = "gpt"
    DEFAULT = "default"


def detect_model_family(model: str | None) -> str:
    """
    Detect the model family from a model string.

    Args:
        model: Model string from LiteLLM. Examples:
            - GPT models: "gpt-5.4", "gpt-5.4-mini"
            - Claude models: "claude-3-5-sonnet", "anthropic/claude-3-5-sonnet"
            - Gemini models: "gemini-pro", "gemini-3-pro-preview"
            - Other models: Any other model

    Returns:
        Model family string: "gpt" or "default"

    Examples:
        >>> detect_model_family("gpt-5.4")
        'gpt'
        >>> detect_model_family("gpt-5")
        'gpt'
        >>> detect_model_family("claude-3-5-sonnet")
        'default'
        >>> detect_model_family("gemini-3-pro-preview")
        'default'
    """
    if not model:
        logger.info("No model specified, using default prompts")
        return ModelFamily.DEFAULT

    model_lower = model.lower()

    # GPT model detection - all GPT models use the same optimized prompts
    if "gpt" in model_lower or "codex" in model_lower:
        logger.info(f"Detected GPT family model: {model}")
        return ModelFamily.GPT

    # All other models (Claude, Gemini, GLM etc.) use default prompts
    logger.info(f"Model '{model}' will use default prompt components")
    return ModelFamily.DEFAULT


def get_prompt_components(model: str | None = None) -> dict:
    """
    Get the appropriate prompt components for the given model.

    Args:
        model: Model string (optional). If None, uses default components.

    Returns:
        Dictionary with component modules/strings:
        {
            "html_generation_rules": str,
            "filter_workflow_rules": str,
            "skill_workflow_rules": str,
            "query_output_format_rules": str,
            "sql_rules": str,
            "mongo_specific_rules": str,
            "duckdb_specific_rules": str,
            "dynamodb_specific_rules": str,
            "databricks_specific_rules": str,
            "summary_after_code": str,
        }

    Example:
        >>> components = get_prompt_components("gpt-5.4")
        >>> html_rules = components["html_generation_rules"]
    """
    family = detect_model_family(model)

    if family == ModelFamily.GPT:
        # Import GPT-optimized variants
        try:
            from server.prompts.components import (
                DATABRICKS_SPECIFIC_RULES,
                DYNAMODB_SPECIFIC_RULES,
                FILTER_WORKFLOW_RULES,
                SKILL_WORKFLOW_RULES,
            )
            from server.prompts.components.variants.gpt import (
                DUCKDB_SPECIFIC_RULES as GPT_DUCKDB_RULES,
            )
            from server.prompts.components.variants.gpt import (
                HTML_DASHBOARD_RULES_TEMPLATE as GPT_HTML_RULES,
            )
            from server.prompts.components.variants.gpt import (
                MONGO_SPECIFIC_RULES as GPT_MONGO_RULES,
            )
            from server.prompts.components.variants.gpt import (
                QUERY_OUTPUT_RULES as GPT_QUERY_OUTPUT,
            )
            from server.prompts.components.variants.gpt import (
                SQL_SPECIFIC_RULES as GPT_SQL_RULES,
            )
            from server.prompts.components.variants.gpt import (
                SUMMARY_AFTER_CODE as GPT_SUMMARY,
            )

            logger.info(f"Loaded GPT-optimized prompt components for model: {model}")

            return {
                "html_generation_rules": GPT_HTML_RULES,
                "filter_workflow_rules": FILTER_WORKFLOW_RULES,
                "skill_workflow_rules": SKILL_WORKFLOW_RULES,
                "query_output_format_rules": GPT_QUERY_OUTPUT,
                "sql_rules": GPT_SQL_RULES,
                "mongo_specific_rules": GPT_MONGO_RULES,
                "duckdb_specific_rules": GPT_DUCKDB_RULES,
                "dynamodb_specific_rules": DYNAMODB_SPECIFIC_RULES,
                "databricks_specific_rules": DATABRICKS_SPECIFIC_RULES,
                "summary_after_code": GPT_SUMMARY,
            }
        except ImportError as e:
            logger.warning(f"Failed to import GPT variants ({e}), falling back to default components")

    # Default components (used for Claude, Gemini, GLM and other models)
    from server.prompts.components import (
        DATABRICKS_SPECIFIC_RULES,
        DUCKDB_SPECIFIC_RULES,
        DYNAMODB_SPECIFIC_RULES,
        FILTER_WORKFLOW_RULES,
        HTML_DASHBOARD_RULES_TEMPLATE,
        MONGO_SPECIFIC_RULES,
        QUERY_OUTPUT_RULES,
        SKILL_WORKFLOW_RULES,
        SQL_SPECIFIC_RULES,
        SUMMARY_AFTER_CODE,
    )

    logger.info(f"Loaded default prompt components for model: {model or 'None'}")

    return {
        "html_generation_rules": HTML_DASHBOARD_RULES_TEMPLATE,
        "filter_workflow_rules": FILTER_WORKFLOW_RULES,
        "skill_workflow_rules": SKILL_WORKFLOW_RULES,
        "query_output_format_rules": QUERY_OUTPUT_RULES,
        "sql_rules": SQL_SPECIFIC_RULES,
        "mongo_specific_rules": MONGO_SPECIFIC_RULES,
        "duckdb_specific_rules": DUCKDB_SPECIFIC_RULES,
        "dynamodb_specific_rules": DYNAMODB_SPECIFIC_RULES,
        "databricks_specific_rules": DATABRICKS_SPECIFIC_RULES,
        "summary_after_code": SUMMARY_AFTER_CODE,
    }
