import pytest

from server.constants.models import MODELS_BY_PROVIDER, RETIRED_MODEL_SUCCESSORS, resolve_model
from server.schemas.notebooks import NotebookRead


@pytest.mark.parametrize(
    ("model", "provider", "expected"),
    [
        ("anthropic/claude-opus-4-8", "anthropic", "anthropic/claude-opus-5-5"),
        ("claude-opus-4-7", "anthropic", "claude-opus-5-5"),
        ("claude_code/claude-sonnet-4-6", "claude_code", "claude_code/claude-sonnet-5-5"),
        ("anthropic/claude-opus-4-8", "openrouter", "anthropic/claude-opus-5.5"),
        ("openrouter/anthropic/claude-sonnet-4-6", "openrouter", "openrouter/anthropic/claude-sonnet-5.5"),
        ("openai/gpt-5.6", "openai", "openai/gpt-6.1-sol"),
        ("codex/gpt-5.6-sol", "codex", "codex/gpt-6.1-sol"),
        ("gpt-5.4", "codex", "gpt-6.1-sol"),
        ("xai/grok-4.20", "xai", "xai/grok-4.7"),
        ("x-ai/grok-4.3", "openrouter", "x-ai/grok-4.7"),
        ("z-ai/glm-5.1", "openrouter", "z-ai/glm-5.3"),
        ("moonshotai/kimi-k2-instruct-0905", "groq", "openai/gpt-oss-120b"),
        ("claude-opus-4-8", "openrouter", "claude-opus-5.5"),
        ("claude-sonnet-4-6", "openrouter", "claude-sonnet-5.5"),
        ("claude-opus-4.7", "openrouter", "claude-opus-5.5"),
        ("kimi-k2-instruct-0905", "groq", "openai/gpt-oss-120b"),
    ],
)
def test_retired_models_resolve_to_successor(model, provider, expected):
    assert resolve_model(model, provider) == expected


@pytest.mark.parametrize("model", ["anthropic/claude-opus-5-5", "my-azure-deployment", "gpt-6-astra", None, ""])
def test_current_and_custom_models_pass_through(model):
    assert resolve_model(model, "anthropic") == model


def test_catalog_has_no_retired_models():
    retired = {key.rpartition("/")[2] for key in RETIRED_MODEL_SUCCESSORS}
    for provider, models in MODELS_BY_PROVIDER.items():
        for model in models:
            assert model.rpartition("/")[2] not in retired, f"{provider} still lists retired {model}"


def test_successors_are_in_some_catalog():
    catalog = {model for models in MODELS_BY_PROVIDER.values() for model in models}
    bare_catalog = {model.rpartition("/")[2] for model in catalog}
    for successor in RETIRED_MODEL_SUCCESSORS.values():
        assert successor in catalog or successor in bare_catalog, successor


def test_notebook_read_upgrades_saved_model():
    notebook = NotebookRead(
        id="00000000-0000-0000-0000-000000000001",
        notebook_name="n",
        last_used_provider="openrouter",
        last_used_model="anthropic/claude-opus-4-8",
        created_at="2026-10-04T00:00:00",
        updated_at="2026-10-04T00:00:00",
    )
    assert notebook.last_used_model == "anthropic/claude-opus-5.5"
