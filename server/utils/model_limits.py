"""Model context window limits registry."""

# Model context limits in tokens
# Source: Official provider documentation (as of 2024-11)
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # OpenAI
    "gpt-5.5": 1_000_000,
    "gpt-5": 128_000,
    "gpt-4o": 128_000,
    # Anthropic
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4.8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4.7": 1_000_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4.6": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-sonnet": 200_000,
    "claude-3-haiku": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-sonnet-4.5": 200_000,
    "claude-haiku-4.5": 200_000,
    # Gemini
    "gemini-3-pro": 2_000_000,
    "gemini-pro": 1_000_000,
    # xAI Grok
    "grok-4.3": 1_000_000,
    "grok-4.20": 2_000_000,
    # Zhipu GLM
    "glm-5.1": 128_000,
    # Default fallback
    "default": 128_000,
}

# Conservative threshold: trigger handoff at 90% of limit
HANDOFF_THRESHOLD_PERCENTAGE = 0.90

# Tier 1 pruning configuration (more aggressive)
TIER1_CONFIG = {
    "PROTECT_RECENT_TOKENS": 15_000,  # Keep last 15k tokens pristine (was 40k)
    "MIN_SAVINGS_THRESHOLD": 2_000,  # Only prune if we save >2k (was 5k)
    "MAX_TOOL_OUTPUT_LENGTH": 1_000,  # Truncate tool outputs beyond this
}

# Tier 1.5 compaction trigger (runs at 60% of context limit)
COMPACTION_TRIGGER_PERCENTAGE = 0.60


def get_model_context_limit(model: str) -> int:
    """
    Get context window limit for a model.

    Args:
        model: Model name (can include provider prefix like "openai/gpt-4")

    Returns:
        Context limit in tokens
    """
    # Strip provider prefix
    model_name = model.lower()
    for prefix in [
        "openai/",
        "anthropic/",
        "openrouter/",
        "azure/",
        "bedrock/",
        "groq/",
        "xai/",
    ]:
        model_name = model_name.replace(prefix, "")

    # Check exact match first
    if model_name in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model_name]

    # Check partial match (for versioned models like "gpt-4-0125")
    for model_key, limit in MODEL_CONTEXT_LIMITS.items():
        if model_key in model_name:
            return limit

    # Default fallback
    return MODEL_CONTEXT_LIMITS["default"]


def get_handoff_trigger_tokens(model: str) -> int:
    """
    Get the token count that should trigger a session handoff.

    Args:
        model: Model name

    Returns:
        Token threshold for handoff (90% of context limit)
    """
    limit = get_model_context_limit(model)
    return int(limit * HANDOFF_THRESHOLD_PERCENTAGE)


def should_trigger_handoff(current_tokens: int, model: str) -> bool:
    """
    Check if current token count exceeds handoff threshold.

    Args:
        current_tokens: Current conversation token count
        model: Model name

    Returns:
        True if handoff should be triggered
    """
    threshold = get_handoff_trigger_tokens(model)
    return current_tokens >= threshold


def get_compaction_trigger_tokens(model: str) -> int:
    """
    Get the token count that should trigger compaction (Tier 1.5).

    Args:
        model: Model name

    Returns:
        Token threshold for compaction (60% of context limit)
    """
    limit = get_model_context_limit(model)
    return int(limit * COMPACTION_TRIGGER_PERCENTAGE)


def should_trigger_compaction(current_tokens: int, model: str) -> bool:
    """
    Check if current token count exceeds compaction threshold (60%).

    Args:
        current_tokens: Current conversation token count
        model: Model name

    Returns:
        True if compaction should be triggered
    """
    threshold = get_compaction_trigger_tokens(model)
    return current_tokens >= threshold
