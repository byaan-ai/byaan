from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from server.models.conversation_evaluation import ConversationEvaluation  # noqa: F401 - ensure table registered
from server.models.custom_skill import CustomSkill
from server.models.notebooks import Notebook
from server.models.skill_suggestion import SkillSuggestion
from server.models.skill_version import SkillVersion
from server.models.slack_conversation import SlackConversation
from server.models.slack_workspace import SlackWorkspace
from server.models.tenant import Tenant
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.skill_suggestion import SkillSuggestionRepository
from server.services.skill_suggestion_service import SkillSuggestionService


async def _community(session) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.slug == "community"))
    return result.scalar_one()


async def _make_skill(session, tenant, instructions: str = "v1 original instructions") -> CustomSkill:
    repo = CustomSkillRepository(session)
    return await repo.create(
        tenant_id=tenant.id,
        created_by=tenant.owner_id,
        name="sales-report",
        description="Weekly sales report skill",
        instructions=instructions,
        scope="org",
    )


async def _make_edit_suggestion(session, tenant, skill, section: str = "overview", proposed: str = "v2 improved"):
    service = SkillSuggestionService(session)
    return await service.create_suggestion(
        tenant_id=tenant.id,
        suggestion_type="edit",
        title="Improve overview section",
        rationale="Users kept asking for clearer definitions.",
        confidence="high",
        skill_id=skill.id,
        patch={"section": section, "before": "old", "after": "new"},
        proposed_instructions=proposed,
        source={"origin": "slack", "channel": "C1"},
    )


@pytest_asyncio.fixture
async def seeded(test_client, test_session):
    tenant = await _community(test_session)
    skill = await _make_skill(test_session, tenant)
    return {"client": test_client, "session": test_session, "tenant": tenant, "skill": skill}


@pytest.mark.asyncio
async def test_create_list_and_pending_count(seeded):
    session, tenant, skill, client = seeded["session"], seeded["tenant"], seeded["skill"], seeded["client"]
    await _make_edit_suggestion(session, tenant, skill, section="overview")
    await _make_edit_suggestion(session, tenant, skill, section="steps")

    resp = await client.get("/api/skill-suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) == 2
    assert body["data"][0]["skill_name"] == "sales-report"

    resp = await client.get("/api/skill-suggestions?status=pending")
    assert len(resp.json()["data"]) == 2

    resp = await client.get("/api/skill-suggestions/pending-count")
    assert resp.json()["data"]["count"] == 2


@pytest.mark.asyncio
async def test_approve_applies_patch_and_writes_versions(seeded):
    session, tenant, skill, client = seeded["session"], seeded["tenant"], seeded["skill"], seeded["client"]
    suggestion = await _make_edit_suggestion(session, tenant, skill, proposed="v2 improved instructions")
    suggestion_id = suggestion.id
    skill_id = skill.id

    resp = await client.post(f"/api/skill-suggestions/{suggestion.id}/approve", json={})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "applied"
    assert data["new_version"] == 1
    assert data["reviewed_via"] == "app"
    assert data["reviewed_by"] is not None
    assert data["reviewed_at"] is not None

    session.expire_all()
    updated = (await session.execute(select(CustomSkill).where(CustomSkill.id == skill_id))).scalar_one()
    assert updated.instructions == "v2 improved instructions"

    versions = (await session.execute(select(SkillVersion).where(SkillVersion.skill_id == skill_id))).scalars().all()
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].changed_by == "loop"
    assert versions[0].instructions == "v1 original instructions"
    assert versions[0].suggestion_id == suggestion_id


@pytest.mark.asyncio
async def test_approve_with_final_instructions_override(seeded):
    session, tenant, skill, client = seeded["session"], seeded["tenant"], seeded["skill"], seeded["client"]
    suggestion = await _make_edit_suggestion(session, tenant, skill, proposed="v2 default")
    skill_id = skill.id

    resp = await client.post(
        f"/api/skill-suggestions/{suggestion.id}/approve",
        json={"final_instructions": "v2 edited by reviewer"},
    )
    assert resp.status_code == 200

    session.expire_all()
    updated = (await session.execute(select(CustomSkill).where(CustomSkill.id == skill_id))).scalar_one()
    assert updated.instructions == "v2 edited by reviewer"


@pytest.mark.asyncio
async def test_double_approve_rejected(seeded):
    session, tenant, skill, client = seeded["session"], seeded["tenant"], seeded["skill"], seeded["client"]
    suggestion = await _make_edit_suggestion(session, tenant, skill)

    first = await client.post(f"/api/skill-suggestions/{suggestion.id}/approve", json={})
    assert first.status_code == 200

    second = await client.post(f"/api/skill-suggestions/{suggestion.id}/approve", json={})
    assert second.status_code == 400
    assert "not pending" in second.json()["message"]


@pytest.mark.asyncio
async def test_reject_requires_reason(seeded):
    session, tenant, skill, client = seeded["session"], seeded["tenant"], seeded["skill"], seeded["client"]
    suggestion = await _make_edit_suggestion(session, tenant, skill)

    missing = await client.post(f"/api/skill-suggestions/{suggestion.id}/reject", json={})
    assert missing.status_code == 422

    empty = await client.post(f"/api/skill-suggestions/{suggestion.id}/reject", json={"reason": ""})
    assert empty.status_code == 422

    ok = await client.post(f"/api/skill-suggestions/{suggestion.id}/reject", json={"reason": "Not accurate"})
    assert ok.status_code == 200
    data = ok.json()["data"]
    assert data["status"] == "rejected"
    assert data["review_note"] == "Not accurate"


@pytest.mark.asyncio
async def test_supersede_on_overlap(seeded):
    session, tenant, skill = seeded["session"], seeded["tenant"], seeded["skill"]
    first = await _make_edit_suggestion(session, tenant, skill, section="overview")
    first_id = first.id
    skill_id = skill.id
    await _make_edit_suggestion(session, tenant, skill, section="overview")

    session.expire_all()
    refreshed = (await session.execute(select(SkillSuggestion).where(SkillSuggestion.id == first_id))).scalar_one()
    assert refreshed.status == "superseded"

    pending = (
        await session.execute(
            select(func.count())
            .select_from(SkillSuggestion)
            .where(SkillSuggestion.skill_id == skill_id, SkillSuggestion.status == "pending")
        )
    ).scalar_one()
    assert pending == 1


@pytest.mark.asyncio
async def test_versions_endpoint(seeded):
    session, tenant, skill, client = seeded["session"], seeded["tenant"], seeded["skill"], seeded["client"]
    suggestion = await _make_edit_suggestion(session, tenant, skill)
    await client.post(f"/api/skill-suggestions/{suggestion.id}/approve", json={})

    resp = await client.get(f"/api/custom-skills/{skill.id}/versions")
    assert resp.status_code == 200
    versions = resp.json()["data"]
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["changed_by"] == "loop"
    assert versions[0]["suggestion_id"] == str(suggestion.id)


@pytest.mark.asyncio
async def test_notebook_list_shows_slack_source(test_client, test_session):
    tenant = await _community(test_session)

    slack_nb = Notebook(tenant_id=tenant.id, created_by=tenant.owner_id, notebook_name="From Slack")
    app_nb = Notebook(tenant_id=tenant.id, created_by=tenant.owner_id, notebook_name="From App")
    test_session.add_all([slack_nb, app_nb])
    await test_session.flush()

    workspace = SlackWorkspace(
        tenant_id=tenant.id,
        slack_team_id="T123",
        bot_token_encrypted="enc",
        signing_secret_encrypted="enc",
    )
    test_session.add(workspace)
    await test_session.flush()

    conversation = SlackConversation(
        slack_workspace_id=workspace.id,
        slack_channel_id="C123",
        slack_thread_ts="1700000000.0001",
        notebook_id=slack_nb.id,
        slack_user_id="U123",
    )
    test_session.add(conversation)
    await test_session.commit()

    resp = await test_client.get("/api/notebooks")
    assert resp.status_code == 200
    items = {n["notebook_name"]: n for n in resp.json()["data"]["items"]}

    assert items["From Slack"]["source"] == "slack"
    assert "slack_thread_title" in items["From Slack"]
    assert items["From App"]["source"] == "app"


@pytest.mark.asyncio
async def test_claim_for_review_is_single_winner(seeded):
    session, tenant, skill = seeded["session"], seeded["tenant"], seeded["skill"]
    suggestion = await _make_edit_suggestion(session, tenant, skill)
    repo = SkillSuggestionRepository(session)

    assert await repo.claim_for_review(suggestion.id, tenant.id) is True
    assert await repo.claim_for_review(suggestion.id, tenant.id) is False


@pytest.mark.asyncio
async def test_concurrent_approve_one_applies_one_rejected(seeded):
    session, tenant, skill = seeded["session"], seeded["tenant"], seeded["skill"]
    suggestion = await _make_edit_suggestion(session, tenant, skill, proposed="v2 concurrent")
    service = SkillSuggestionService(session)

    applied, version = await service.approve(suggestion.id, tenant.id, reviewed_via="app")
    assert applied.status == "applied"
    assert version == 1

    with pytest.raises(ValueError, match="not pending"):
        await service.approve(suggestion.id, tenant.id, reviewed_via="slack")


@pytest.mark.asyncio
async def test_approve_failure_restores_to_pending(seeded):
    session, tenant, skill = seeded["session"], seeded["tenant"], seeded["skill"]
    service = SkillSuggestionService(session)
    suggestion = await service.create_suggestion(
        tenant_id=tenant.id,
        suggestion_type="edit",
        title="Broken edit",
        rationale="missing instructions",
        confidence="low",
        skill_id=skill.id,
        patch={"section": "overview", "before": "a", "after": "b"},
        proposed_instructions=None,
    )
    suggestion_id = suggestion.id
    tenant_id = tenant.id

    with pytest.raises(ValueError, match="No instructions"):
        await service.approve(suggestion_id, tenant_id, reviewed_via="app")

    session.expire_all()
    row = (await session.execute(select(SkillSuggestion).where(SkillSuggestion.id == suggestion_id))).scalar_one()
    assert row.status == "pending"
