"""Request-scoped Slack context for agent tool execution.

When the agent is invoked from a Slack event we need to resolve defaults
(channel, thread, user timezone, workspace) inside tools without changing
every tool signature. The context is set by the Slack router/service around
the agent run and read by cron tools when they need Slack defaults.

Non-Slack entry points (web/desktop chat, scheduled runs, tests) do not set
the context; tools fall back to explicit arguments or ask the user.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class SlackContext:
    workspace_id: UUID
    tenant_id: UUID
    team_id: str
    channel_id: str
    thread_ts: str | None
    slack_user_id: str
    user_timezone: str = "UTC"
    byaan_user_id: UUID | None = None


_SLACK_CONTEXT: ContextVar[SlackContext | None] = ContextVar("byaan_slack_context", default=None)


def get_slack_context() -> SlackContext | None:
    return _SLACK_CONTEXT.get()


def set_slack_context(ctx: SlackContext | None):
    return _SLACK_CONTEXT.set(ctx)


def reset_slack_context(token) -> None:
    _SLACK_CONTEXT.reset(token)


@contextmanager
def slack_context_scope(ctx: SlackContext):
    """Scoped setter — guarantees reset on exit."""
    token = _SLACK_CONTEXT.set(ctx)
    try:
        yield ctx
    finally:
        _SLACK_CONTEXT.reset(token)
