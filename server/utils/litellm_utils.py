"""Utility functions for litellm model capabilities."""

from __future__ import annotations

from litellm import get_model_info

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

# OpenAI reasoning families that reject non-default temperature. Extend as new families ship.
_RESTRICTED_TEMPERATURE_FAMILIES = ("gpt-5", "o1", "o3", "o4")


def supports_custom_temperature(model: str | None) -> bool:
    """Return False for OpenAI reasoning families (gpt-5.x, o1/o3/o4) that only accept the default temperature.

    LiteLLM's drop_params does not strip temperature for these models, so callers must omit it explicitly.
    """
    if not model:
        return True

    model_lower = model.lower()
    return not any(family in model_lower for family in _RESTRICTED_TEMPERATURE_FAMILIES)


def supports_parallel_tool_calls(model: str | None) -> bool:
    """
    Check if a model supports parallel tool calls.

    Uses litellm's get_model_info to check if 'parallel_tool_calls' is in
    the model's supported_openai_params list.

    Args:
        model: The model name/identifier (e.g., "gpt-4", "bedrock/anthropic.claude-3")

    Returns:
        True if the model supports parallel tool calls, False otherwise.
    """
    if not model:
        return False

    try:
        info = get_model_info(model)
        supported_params = info.get("supported_openai_params") or []
        result = "parallel_tool_calls" in supported_params
        logger.debug(
            "Model %s parallel_tool_calls support: %s",
            model,
            result,
        )
        return result
    except Exception as e:
        logger.warning(
            "Failed to check parallel_tool_calls support for model %s: %s",
            model,
            e,
        )
        return False


if __name__ == "__main__":
    # Example usage
    test_models = ["openai/gpt-5.4"]

    for model in test_models:
        support = supports_parallel_tool_calls(model)
        print(f"Model: {model}, Supports Parallel Tool Calls: {support}")
