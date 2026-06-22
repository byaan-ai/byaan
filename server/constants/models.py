OPENAI_MODELS = [
    "openai/gpt-5.5",
    "openai/gpt-5.4",
]

ANTHROPIC_MODELS = [
    "anthropic/claude-opus-4-8",
    "anthropic/claude-opus-4-7",
    "anthropic/claude-sonnet-4-6",
]

CLAUDE_CODE_MODELS = [
    "claude_code/claude-opus-4-8",
    "claude_code/claude-opus-4-7",
    "claude_code/claude-sonnet-4-6",
]

OPENROUTER_SPECIFIC_MODELS = [
    "x-ai/grok-4.3",
    "x-ai/grok-4.20",
    "z-ai/glm-5.1",
]

# Azure and Bedrock models are user-provided (deployment-specific)
# Return empty catalog - models come from user config stored in DB
AZURE_MODELS: list[str] = []
BEDROCK_MODELS: list[str] = []

GROQ_MODELS = [
    "moonshotai/kimi-k2-instruct-0905",
]

XAI_MODELS = [
    "xai/grok-4.3",
    "xai/grok-4.20",
]

CODEX_MODELS = [
    "codex/gpt-5.5",
    "codex/gpt-5.4",
]

# Combined OpenRouter models (includes Anthropic + OpenAI + OpenRouter-specific)
OPENROUTER_MODELS = ANTHROPIC_MODELS + OPENAI_MODELS + OPENROUTER_SPECIFIC_MODELS

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
