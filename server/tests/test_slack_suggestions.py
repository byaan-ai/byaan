from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import server.db.session as db_session
import server.services.slack_suggestion_service as sss
from server.models.custom_skill import CustomSkill  # noqa: F401 - ensure table registered
from server.models.skill_suggestion import SkillSuggestion
from server.models.skill_version import SkillVersion
from server.models.slack_conversation import SlackConversation
from server.models.slack_workspace import SlackWorkspace
from server.models.tenant import Tenant
from server.models.user import User
from server.repositories.custom_skill import CustomSkillRepository
from server.services.skill_suggestion_service import SkillSuggestionService
from server.services.slack_agent_service import SlackAgentService
from server.services.slack_service import SlackService
from server.services.slack_suggestion_service import handle_suggestion_action, notify_suggestion_created


async def _community(session) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.slug == "community"))
    return result.scalar_one()


async def _make_workspace(session, tenant, *, team_id: str = "T123", reviewers_channel_id: str | None = None):
    workspace = SlackWorkspace(
        tenant_id=tenant.id,
        slack_team_id=team_id,
        slack_team_name="Test WS",
        bot_token_encrypted="enc-bot",
        signing_secret_encrypted="enc-sign",
        reviewers_channel_id=reviewers_channel_id,
    )
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace


async def _make_skill(session, tenant) -> CustomSkill:
    return await CustomSkillRepository(session).create(
        tenant_id=tenant.id,
        created_by=tenant.owner_id,
        name="sales-report",
        description="Weekly sales report skill",
        instructions="v1 original instructions",
        scope="org",
    )


async def _make_edit_suggestion(session, tenant, skill, *, source: dict | None = None) -> SkillSuggestion:
    return await SkillSuggestionService(session).create_suggestion(
        tenant_id=tenant.id,
        suggestion_type="edit",
        title="Improve overview section",
        rationale="Users kept asking for clearer definitions.",
        confidence="high",
        skill_id=skill.id,
        patch={"section": "overview", "before": "old text", "after": "new text"},
        proposed_instructions="v2 improved instructions",
        evidence={"summary": "3 threads asked the same question"},
        source=source if source is not None else {"origin": "slack", "channel": "C_SRC", "thread_ts": "999.111"},
    )


def _patch_session_factory(monkeypatch, session: AsyncSession) -> None:
    factory = async_sessionmaker(bind=session.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_session, "AsyncSessionFactory", factory)


# --------------------------------------------------------------------------- thread_title


@pytest.mark.asyncio
async def test_thread_title_set_on_new_conversation(test_client, test_session):
    tenant = await _community(test_session)
    workspace = await _make_workspace(test_session, tenant)

    conversation = await SlackAgentService._get_or_create_conversation(
        workspace=workspace,
        channel_id="C1",
        thread_ts="100.1",
        user_id="U1",
        session=test_session,
        text="   How many   orders shipped last week?  ",
    )

    assert conversation.thread_title == "How many orders shipped last week?"


@pytest.mark.asyncio
async def test_thread_title_backfilled_on_touch(test_client, test_session):
    tenant = await _community(test_session)
    workspace = await _make_workspace(test_session, tenant)

    convo = SlackConversation(
        slack_workspace_id=workspace.id,
        slack_channel_id="C2",
        slack_thread_ts="200.2",
        slack_user_id="U1",
        thread_title=None,
    )
    test_session.add(convo)
    await test_session.commit()

    touched = await SlackAgentService._get_or_create_conversation(
        workspace=workspace,
        channel_id="C2",
        thread_ts="200.2",
        user_id="U1",
        session=test_session,
        text="Backfilled title here",
    )

    assert touched.id == convo.id
    assert touched.thread_title == "Backfilled title here"


# --------------------------------------------------------------------------- notebooks endpoint


@pytest.mark.asyncio
async def test_notebooks_endpoint_returns_thread_title(test_client, test_session):
    tenant = await _community(test_session)
    workspace = await _make_workspace(test_session, tenant)

    create = await test_client.post("/api/notebooks", json={"notebook_name": "Slack NB"})
    notebook_id = create.json()["data"]["id"]

    convo = SlackConversation(
        slack_workspace_id=workspace.id,
        slack_channel_id="C3",
        slack_thread_ts="300.3",
        slack_user_id="U1",
        notebook_id=notebook_id,
        thread_title="Revenue by region",
    )
    test_session.add(convo)
    await test_session.commit()

    listing = await test_client.get("/api/notebooks")
    items = listing.json()["data"]["items"]
    match = next(i for i in items if i["id"] == notebook_id)
    assert match["source"] == "slack"
    assert match["slack_thread_title"] == "Revenue by region"


# --------------------------------------------------------------------------- notify_suggestion_created


@pytest.mark.asyncio
async def test_notify_posts_card_and_stamps_ts(test_client, test_session, monkeypatch):
    tenant = await _community(test_session)
    await _make_workspace(test_session, tenant, reviewers_channel_id="C_REVIEW")
    skill = await _make_skill(test_session, tenant)
    suggestion = await _make_edit_suggestion(test_session, tenant, skill)

    captured = {}

    async def fake_post_message(self, channel, text, thread_ts=None, blocks=None):
        captured["channel"] = channel
        captured["thread_ts"] = thread_ts
        captured["blocks"] = blocks
        return {"ts": "1700.0001", "channel": channel}

    monkeypatch.setattr(sss, "_bot_token", lambda ws, session: _async_return("xoxb-test"))
    monkeypatch.setattr(SlackService, "post_message", fake_post_message)

    await notify_suggestion_created(test_session, suggestion)

    assert captured["channel"] == "C_REVIEW"
    assert captured["thread_ts"] is None

    actions = [b for b in captured["blocks"] if b.get("type") == "actions"][0]
    action_ids = {el["action_id"] for el in actions["elements"]}
    assert action_ids == {
        "skill_suggestion_approve",
        "skill_suggestion_reject",
        "skill_suggestion_discuss",
    }
    for el in actions["elements"]:
        assert json.loads(el["value"]) == {"suggestion_id": str(suggestion.id)}

    await test_session.refresh(suggestion)
    assert suggestion.slack_message_ts == "1700.0001"
    assert suggestion.slack_channel_id == "C_REVIEW"


@pytest.mark.asyncio
async def test_notify_falls_back_to_source_thread(test_client, test_session, monkeypatch):
    tenant = await _community(test_session)
    await _make_workspace(test_session, tenant, reviewers_channel_id=None)
    skill = await _make_skill(test_session, tenant)
    suggestion = await _make_edit_suggestion(test_session, tenant, skill)

    captured = {}

    async def fake_post_message(self, channel, text, thread_ts=None, blocks=None):
        captured["channel"] = channel
        captured["thread_ts"] = thread_ts
        return {"ts": "1700.0002", "channel": channel}

    monkeypatch.setattr(sss, "_bot_token", lambda ws, session: _async_return("xoxb-test"))
    monkeypatch.setattr(SlackService, "post_message", fake_post_message)

    await notify_suggestion_created(test_session, suggestion)

    assert captured["channel"] == "C_SRC"
    assert captured["thread_ts"] == "999.111"


# --------------------------------------------------------------------------- handle_suggestion_action


def _async_return(value):
    async def _inner():
        return value

    return _inner()


@pytest_asyncio.fixture
async def review_ctx(test_client, test_session, monkeypatch):
    tenant = await _community(test_session)
    workspace = await _make_workspace(test_session, tenant, team_id="TREVIEW", reviewers_channel_id="C_REVIEW")
    skill = await _make_skill(test_session, tenant)
    suggestion = await _make_edit_suggestion(test_session, tenant, skill)
    suggestion.slack_channel_id = "C_REVIEW"
    suggestion.slack_message_ts = "1700.5"
    await test_session.commit()

    owner = await test_session.get(User, tenant.owner_id)

    _patch_session_factory(monkeypatch, test_session)
    monkeypatch.setattr(sss, "_bot_token", lambda ws, session: _async_return("xoxb-test"))

    ephemerals: list[str] = []
    replaced: list[dict] = []

    async def fake_ephemeral(response_url, text):
        ephemerals.append(text)

    async def fake_replace(response_url, blocks, text):
        replaced.append({"blocks": blocks, "text": text})

    monkeypatch.setattr(sss, "_post_ephemeral", fake_ephemeral)
    monkeypatch.setattr(sss, "_replace_original", fake_replace)

    async def fake_post_message(self, channel, text, thread_ts=None, blocks=None):
        return {"ts": "reply.1", "channel": channel}

    monkeypatch.setattr(SlackService, "post_message", fake_post_message)

    return {
        "session": test_session,
        "tenant": tenant,
        "workspace": workspace,
        "skill": skill,
        "suggestion": suggestion,
        "owner_email": owner.email,
        "ephemerals": ephemerals,
        "replaced": replaced,
        "monkeypatch": monkeypatch,
    }


@pytest.mark.asyncio
async def test_approve_flow_end_to_end(review_ctx):
    ctx = review_ctx
    ctx["monkeypatch"].setattr(
        SlackService,
        "get_user_info",
        lambda self, uid: _async_return({"id": uid, "name": "Alice", "email": ctx["owner_email"]}),
    )

    await handle_suggestion_action(
        action_id="skill_suggestion_approve",
        suggestion_id=str(ctx["suggestion"].id),
        slack_user_id="U_ALICE",
        response_url="http://response",
        team_id="TREVIEW",
    )

    session = ctx["session"]
    fresh = await session.get(SkillSuggestion, ctx["suggestion"].id, populate_existing=True)
    await session.refresh(fresh)
    assert fresh.status == "applied"
    assert fresh.reviewed_via == "slack"
    assert fresh.reviewer_display_name == "Alice"

    versions = (
        (await session.execute(select(SkillVersion).where(SkillVersion.skill_id == ctx["skill"].id))).scalars().all()
    )
    assert len(versions) == 1

    assert ctx["replaced"], "expected the original message to be rewritten"
    assert "Approved by Alice" in ctx["replaced"][0]["blocks"][0]["text"]["text"]


@pytest.mark.asyncio
async def test_non_member_email_can_approve(review_ctx):
    ctx = review_ctx
    ctx["monkeypatch"].setattr(
        SlackService,
        "get_user_info",
        lambda self, uid: _async_return({"id": uid, "name": "Mallory", "email": "stranger@example.com"}),
    )

    await handle_suggestion_action(
        action_id="skill_suggestion_approve",
        suggestion_id=str(ctx["suggestion"].id),
        slack_user_id="U_MALLORY",
        response_url="http://response",
        team_id="TREVIEW",
    )

    session = ctx["session"]
    fresh = await session.get(SkillSuggestion, ctx["suggestion"].id, populate_existing=True)
    await session.refresh(fresh)
    assert fresh.status == "applied"
    assert fresh.reviewed_by is None
    assert fresh.reviewer_display_name == "Mallory"
    assert ctx["replaced"], "expected the original message to be rewritten"


@pytest.mark.asyncio
async def test_unknown_user_can_approve(review_ctx):
    ctx = review_ctx
    ctx["monkeypatch"].setattr(SlackService, "get_user_info", lambda self, uid: _async_return(None))

    await handle_suggestion_action(
        action_id="skill_suggestion_approve",
        suggestion_id=str(ctx["suggestion"].id),
        slack_user_id="U_GHOST",
        response_url="http://response",
        team_id="TREVIEW",
    )

    session = ctx["session"]
    fresh = await session.get(SkillSuggestion, ctx["suggestion"].id, populate_existing=True)
    await session.refresh(fresh)
    assert fresh.status == "applied"
    assert fresh.reviewed_by is None
    assert fresh.reviewer_display_name == "U_GHOST"
    assert ctx["replaced"], "expected the original message to be rewritten"


@pytest.mark.asyncio
async def test_double_click_reports_already_handled(review_ctx):
    ctx = review_ctx
    ctx["monkeypatch"].setattr(
        SlackService,
        "get_user_info",
        lambda self, uid: _async_return({"id": uid, "name": "Alice", "email": ctx["owner_email"]}),
    )

    for _ in range(2):
        await handle_suggestion_action(
            action_id="skill_suggestion_approve",
            suggestion_id=str(ctx["suggestion"].id),
            slack_user_id="U_ALICE",
            response_url="http://response",
            team_id="TREVIEW",
        )

    assert ctx["ephemerals"], "second click should surface an ephemeral"
    assert "Already handled" in ctx["ephemerals"][-1]
