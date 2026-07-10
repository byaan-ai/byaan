from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.auth.dependencies import AuthContext, _auth_context_dependency
from server.auth.scopes import get_scopes_for_role
from server.main import app
from server.models.messages import Message
from server.models.notebooks import Notebook
from server.models.skill_loop_settings import SkillLoopSettings
from server.models.slack_workspace import SlackWorkspace
from server.models.tenant import Tenant
from server.models.tenant_member import TenantRole
from server.models.threads import Thread
from server.models.user import User
from server.services.conversation_evaluation_service import ConversationEvaluationService
from server.utils.config_loader import get_skill_loop_config

pytestmark = pytest.mark.asyncio


async def _community(session) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.slug == "community"))
    return result.scalar_one()


def _override_auth(role: TenantRole, tenant_id, user_id) -> None:
    async def _fake_auth() -> AuthContext:
        user = User(id=user_id, email="role@test.com", hashed_password="x", is_active=True, is_verified=True)
        return AuthContext(user, tenant_id, role, get_scopes_for_role(role))

    app.dependency_overrides[_auth_context_dependency] = _fake_auth


# --------------------------------------------------------------------------- GET


async def test_get_returns_defaults_without_creating_row(test_client, test_session):
    resp = await test_client.get("/api/skill-loop/settings")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enabled"] is True
    assert data["digest_enabled"] is True
    assert data["digest_hour"] == 17
    assert data["slack_reviewers_channel_id"] is None
    assert data["slack_workspace_connected"] is False
    assert data["loop_globally_enabled"] is True

    rows = (await test_session.execute(select(SkillLoopSettings))).scalars().all()
    assert rows == []


# --------------------------------------------------------------------------- config default


async def test_skill_loop_config_enabled_by_default(monkeypatch):
    monkeypatch.delenv("SKILL_LOOP_ENABLED", raising=False)
    assert get_skill_loop_config()["enabled"] is True


async def test_skill_loop_config_env_false_disables(monkeypatch):
    monkeypatch.setenv("SKILL_LOOP_ENABLED", "false")
    assert get_skill_loop_config()["enabled"] is False


# --------------------------------------------------------------------------- slack channels


async def test_slack_channels_returns_channels_when_connected(test_client, test_session, monkeypatch):
    import server.routers.skill_loop as skill_loop_router

    tenant = await _community(test_session)
    workspace = SlackWorkspace(
        tenant_id=tenant.id,
        slack_team_id="T-ch",
        slack_team_name="WS",
        bot_token_encrypted="enc",
        signing_secret_encrypted="enc",
    )
    test_session.add(workspace)
    await test_session.commit()

    async def _fake_token(ws, session):
        return "xoxb-test"

    class _FakeSlack:
        def __init__(self, token):
            self.token = token

        async def list_channels(self, limit=200):
            return [{"id": "C1", "name": "general"}, {"id": "C2", "name": "random"}]

    monkeypatch.setattr(skill_loop_router.SlackAgentService, "_get_bot_token", staticmethod(_fake_token))
    monkeypatch.setattr(skill_loop_router, "SlackService", _FakeSlack)

    resp = await test_client.get("/api/skill-loop/slack-channels")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["connected"] is True
    assert data["channels"] == [{"id": "C1", "name": "general"}, {"id": "C2", "name": "random"}]


async def test_slack_channels_not_connected_without_workspace(test_client, test_session):
    resp = await test_client.get("/api/skill-loop/slack-channels")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["connected"] is False
    assert data["channels"] == []


# --------------------------------------------------------------------------- PUT


async def test_put_upserts_and_returns_updated_values(test_client, test_session):
    resp = await test_client.put(
        "/api/skill-loop/settings",
        json={"enabled": False, "digest_enabled": False, "digest_hour": 9},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enabled"] is False
    assert data["digest_enabled"] is False
    assert data["digest_hour"] == 9

    row = (await test_session.execute(select(SkillLoopSettings))).scalars().one()
    assert row.enabled is False
    assert row.digest_hour == 9


async def test_put_invalid_digest_hour_returns_422(test_client):
    resp = await test_client.put("/api/skill-loop/settings", json={"digest_hour": 25})
    assert resp.status_code == 422


async def test_put_updates_workspace_reviewers_channel(test_client, test_session):
    tenant = await _community(test_session)
    workspace = SlackWorkspace(
        tenant_id=tenant.id,
        slack_team_id="T-review",
        slack_team_name="WS",
        bot_token_encrypted="enc",
        signing_secret_encrypted="enc",
    )
    test_session.add(workspace)
    await test_session.commit()

    resp = await test_client.put(
        "/api/skill-loop/settings",
        json={"slack_reviewers_channel_id": "  C12345  "},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["slack_reviewers_channel_id"] == "C12345"
    assert resp.json()["data"]["slack_workspace_connected"] is True

    await test_session.refresh(workspace)
    assert workspace.reviewers_channel_id == "C12345"

    resp = await test_client.put("/api/skill-loop/settings", json={"slack_reviewers_channel_id": ""})
    assert resp.status_code == 200
    assert resp.json()["data"]["slack_reviewers_channel_id"] is None
    await test_session.refresh(workspace)
    assert workspace.reviewers_channel_id is None


async def test_put_reviewers_channel_without_workspace_warns_but_saves(test_client, test_session):
    resp = await test_client.put(
        "/api/skill-loop/settings",
        json={"enabled": False, "slack_reviewers_channel_id": "C999"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "no slack workspace" in body["message"].lower()
    row = (await test_session.execute(select(SkillLoopSettings))).scalars().one()
    assert row.enabled is False


# --------------------------------------------------------------------------- RBAC


async def test_member_can_get_but_cannot_put(test_client, test_session):
    tenant = await _community(test_session)
    _override_auth(TenantRole.MEMBER, tenant.id, tenant.owner_id)
    try:
        get_resp = await test_client.get("/api/skill-loop/settings")
        assert get_resp.status_code == 200

        put_resp = await test_client.put("/api/skill-loop/settings", json={"enabled": False})
        assert put_resp.status_code == 403
    finally:
        app.dependency_overrides.pop(_auth_context_dependency, None)


# --------------------------------------------------------------------------- evaluator gating


async def _seed_tenant(session) -> Tenant:
    user = User(
        id=uuid4(), email=f"o-{uuid4().hex[:6]}@test.com", hashed_password="x", is_active=True, is_verified=True
    )
    session.add(user)
    await session.flush()
    tenant = Tenant(id=uuid4(), name="Acme", slug=f"acme-{uuid4().hex[:6]}", owner_id=user.id)
    session.add(tenant)
    await session.commit()
    return tenant


async def _seed_notebook(session, tenant) -> Notebook:
    notebook = Notebook(id=uuid4(), tenant_id=tenant.id, notebook_name="NB")
    session.add(notebook)
    await session.flush()
    thread = Thread(id=notebook.id, notebook_id=notebook.id)
    session.add(thread)
    await session.flush()
    base = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=45)
    for idx, (role, content) in enumerate([("user", "q"), ("assistant", "a")]):
        session.add(
            Message(
                id=uuid4(), thread_id=thread.id, role=role, content=content, created_at=base + timedelta(seconds=idx)
            )
        )
    await session.commit()
    return notebook


async def test_candidate_discovery_skips_disabled_tenant(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant)
    svc = ConversationEvaluationService()
    svc._notify = AsyncMock()

    before = await svc.find_candidate_notebooks(test_session, limit=10)
    assert any(c["notebook_id"] == notebook.id for c in before)

    test_session.add(SkillLoopSettings(tenant_id=tenant.id, enabled=False))
    await test_session.commit()

    after = await svc.find_candidate_notebooks(test_session, limit=10)
    assert all(c["notebook_id"] != notebook.id for c in after)


async def test_digest_gating_respects_disabled_tenant(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    svc = ConversationEvaluationService()

    async def _fake_needing(session):
        return [tenant.id]

    monkeypatch.setattr(svc, "_tenants_needing_digest", _fake_needing)
    now = datetime.now().replace(hour=23)

    due = await svc._tenants_due_for_digest(test_session, now)
    assert tenant.id in due

    test_session.add(SkillLoopSettings(tenant_id=tenant.id, enabled=False, digest_enabled=True))
    await test_session.commit()
    assert await svc._tenants_due_for_digest(test_session, now) == []


async def test_digest_gating_respects_digest_hour(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    test_session.add(SkillLoopSettings(tenant_id=tenant.id, enabled=True, digest_enabled=True, digest_hour=20))
    await test_session.commit()
    svc = ConversationEvaluationService()

    async def _fake_needing(session):
        return [tenant.id]

    monkeypatch.setattr(svc, "_tenants_needing_digest", _fake_needing)

    assert await svc._tenants_due_for_digest(test_session, datetime.now().replace(hour=9)) == []
    assert tenant.id in await svc._tenants_due_for_digest(test_session, datetime.now().replace(hour=21))
