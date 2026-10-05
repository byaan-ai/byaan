OPENAI_MODELS = [
    "openai/gpt-6.1-sol",
    "openai/gpt-6-astra",
    "openai/gpt-6-luna",
]

ANTHROPIC_MODELS = [
    "anthropic/claude-opus-5-5",
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5-5",
]

CLAUDE_CODE_MODELS = [
    "claude_code/claude-opus-5-5",
    "claude_code/claude-opus-5",
    "claude_code/claude-sonnet-5-5",
]

# OpenRouter spells Anthropic versions with dots, unlike Anthropic's own API.
OPENROUTER_ANTHROPIC_MODELS = [
    "anthropic/claude-opus-5.5",
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5.5",
]

OPENROUTER_SPECIFIC_MODELS = [
    "x-ai/grok-4.7",
    "z-ai/glm-5.3",
    "moonshotai/kimi-k3",
]

# Azure and Bedrock models are user-provided (deployment-specific)
# Return empty catalog - models come from user config stored in DB
AZURE_MODELS: list[str] = []
BEDROCK_MODELS: list[str] = []

# Groq retired all Kimi models; gpt-oss-120b is Groq's designated replacement.
GROQ_MODELS = [
    "openai/gpt-oss-120b",
]

XAI_MODELS = [
    "xai/grok-4.7",
]

# The Codex backend serves no bare "gpt-6.1" - only the named variants.
CODEX_MODELS = [
    "codex/gpt-6.1-sol",
    "codex/gpt-6-astra",
    "codex/gpt-6-luna",
]

# Combined OpenRouter models (includes Anthropic + OpenAI + OpenRouter-specific)
OPENROUTER_MODELS = OPENROUTER_ANTHROPIC_MODELS + OPENAI_MODELS + OPENROUTER_SPECIFIC_MODELS

# Main dictionary for easy access by provider
MODELS_BY_PROVIDER: dict[str, list[str]] = {
    "openai": OPENAI_MODELS,
    "anthropic": ANTHROPIC_MODELS,
    "claude_code": CLAUDE_CODE_MODELS,
    "openrouter": OPENROUTER_MODELS,
    "azure": AZURE_MODELS,
    "bedrock": BEDROCK_MODELS,
    "groq": GROQ_MODELS,
    "xai": XAI_MODELS,
    "codex": CODEX_MODELS,
}

# Cheap-model override for the Slack thread-followup intent classifier; providers not listed fall back to workspace default.
SLACK_CLASSIFIER_MODEL_BY_PROVIDER: dict[str, str] = {
    "anthropic": "anthropic/claude-sonnet-5-5",
    "claude_code": "claude_code/claude-sonnet-5-5",
    "openrouter": "anthropic/claude-sonnet-5.5",
    "openai": "openai/gpt-6-luna",
    "codex": "codex/gpt-6-luna",
}

# Retired models map to their successor so saved picks (user settings, notebooks, Slack) keep working.
# Keys are bare model names, or the full stored id when the successor lives under another vendor prefix.
# A successor containing "/" is a cross-vendor move: it is returned as-is, dropping the original prefix.
RETIRED_MODEL_SUCCESSORS: dict[str, str] = {
    "claude-opus-4-8": "claude-opus-5-5",
    "claude-opus-4.8": "claude-opus-5-5",
    "claude-opus-4-7": "claude-opus-5-5",
    "claude-opus-4.7": "claude-opus-5-5",
    "claude-sonnet-4-6": "claude-sonnet-5-5",
    "claude-sonnet-4.6": "claude-sonnet-5-5",
    "gpt-5.6": "gpt-6.1-sol",
    "gpt-5.6-sol": "gpt-6.1-sol",
    "gpt-5.5": "gpt-6.1-sol",
    "gpt-5.4": "gpt-6.1-sol",
    "grok-4.3": "grok-4.7",
    "grok-4.20": "grok-4.7",
    "glm-5.1": "glm-5.3",
    "kimi-k2-instruct-0905": "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct-0905": "openai/gpt-oss-120b",
}

_OPENROUTER_SPELLINGS = {
    "claude-opus-5-5": "claude-opus-5.5",
    "claude-sonnet-5-5": "claude-sonnet-5.5",
}


def resolve_model(model: str | None, provider: str | None = None) -> str | None:
    """Swap a retired model for its successor, keeping whatever provider prefix it was stored with."""
    if not model:
        return model
    prefix, _, name = model.rpartition("/")
    successor = RETIRED_MODEL_SUCCESSORS.get(model) or RETIRED_MODEL_SUCCESSORS.get(name)
    if not successor:
        return model
    if "/" in successor:
        return successor
    if provider == "openrouter" or prefix.startswith("openrouter"):
        successor = _OPENROUTER_SPELLINGS.get(successor, successor)
    return f"{prefix}/{successor}" if prefix else successor
