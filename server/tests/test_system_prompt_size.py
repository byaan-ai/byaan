import os

import server.services.claude_mcp_service as claude_mcp_service
from server.prompts.prompts import LEARNING_PREVIEW_CHARS, _format_relevant_learnings


def test_long_learnings_are_previewed_with_id():
    learnings = [{"id": f"id-{i}", "title": f"t{i}", "learning": "x" * 7_500} for i in range(10)]
    block = _format_relevant_learnings(learnings)
    assert len(block) < 10 * (LEARNING_PREVIEW_CHARS + 100)
    assert block.count("call get_learning") == 10
    assert "[id-9]" in block


def test_short_learnings_are_unchanged():
    block = _format_relevant_learnings([{"id": "a", "title": "t", "learning": "short"}, {"id": "b", "title": "u"}])
    assert "- [a] t: short" in block
    assert "- [b] u" in block
    assert "get_learning" not in block


async def test_system_prompt_is_passed_as_file_and_removed(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, options):
            captured["system_prompt"] = options.system_prompt
            path = options.system_prompt["path"]
            with open(path, encoding="utf-8") as f:
                captured["content"] = f.read()

        async def connect(self):
            raise RuntimeError("stop after options are built")

        async def disconnect(self):
            pass

    async def no_token():
        return None

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(claude_mcp_service, "ClaudeSDKClient", FakeClient)
    monkeypatch.setattr(claude_mcp_service, "get_active_token_with_refresh", no_token)
    monkeypatch.setattr(claude_mcp_service.asyncio, "sleep", no_sleep)

    instructions = "rules " * 50_000
    events = [e async for e in claude_mcp_service.stream_claude_with_mcp_tools(prompt="hi", instructions=instructions)]

    assert captured["system_prompt"]["type"] == "file"
    assert captured["content"] == instructions
    assert not os.path.exists(captured["system_prompt"]["path"])
    assert events[-1]["type"] == "error"
