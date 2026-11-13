from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, UserMessage
from claude_agent_sdk.types import TextBlock

from server.schemas.handoff import HandoffPaths
from server.services import handoff_agent
from server.services.handoff_agent import AgentRunSpec, ClaudeCodeAdapter

pytestmark = pytest.mark.handoff


def _result(text: str, cost: float = 0.1) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        result=text,
        total_cost_usd=cost,
    )


class FakeClient:
    script: list = []

    def __init__(self, options):
        self.queries: list[str] = []
        self.interrupted = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt):
        self.queries.append(prompt)

    async def interrupt(self):
        self.interrupted = True

    async def receive_messages(self):
        for message in FakeClient.script:
            await asyncio.sleep(0)
            yield message
        await asyncio.sleep(3600)


class Hooks:
    def __init__(self):
        self.delivered = []

    async def on_tool(self, name, target):
        pass

    async def report_progress(self, step, note, estimate):
        return "ok"

    async def ask_human(self, question, options):
        return "no"

    async def submit_result(self, title, summary, tests, risks, steps):
        return "ok"

    async def pending_followups(self):
        return []

    async def mark_delivered(self, ids):
        self.delivered.extend(ids)

    async def stop_requested(self):
        return False

    def waiting_seconds(self):
        return 0.0

    def is_waiting(self):
        return False


async def test_stale_turn_result_does_not_end_the_run(monkeypatch, tmp_path: Path):
    prompt = "CI failed on your pull request. Fix it."
    FakeClient.script = [
        UserMessage(content="<task-notification>background shell stopped</task-notification>"),
        _result("acknowledged the old background task", cost=0.01),
        UserMessage(content=prompt),
        AssistantMessage(content=[TextBlock(text="Fixed the lint errors.")], model="m"),
        _result("done", cost=0.2),
    ]
    monkeypatch.setattr(handoff_agent, "ClaudeSDKClient", FakeClient)
    spec = AgentRunSpec(
        cwd=tmp_path, home=tmp_path, prompt=prompt, system_prompt="", oauth_token="t", paths=HandoffPaths(), minutes=5
    )
    hooks = Hooks()
    result = await asyncio.wait_for(ClaudeCodeAdapter()._drive(None, spec, hooks), timeout=5)

    assert result.outcome == "success"
    assert result.final_text == "Fixed the lint errors."
    assert result.cost_usd == pytest.approx(0.21)
    assert hooks.delivered == []
