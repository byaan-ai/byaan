from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from server.models.custom_skill import CustomSkill  # noqa: F401 - ensure table registered
from server.models.github_repository import GitHubRepository
from server.models.notebooks import Notebook
from server.models.skill_loop_lease import SkillLoopLease
from server.models.tenant import Tenant
from server.models.user import User
from server.repositories.skill_loop_settings import SkillLoopSettingsRepository
from server.services.conversation_evaluation_service import ConversationEvaluationService
from server.services.repo_sync_service import RepoSyncService

pytestmark = pytest.mark.asyncio


async def _seed_tenant(session) -> Tenant:
    user = User(id=uuid4(), email=f"u-{uuid4().hex[:6]}@t.com", hashed_password="x", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(id=uuid4(), name="Acme", slug=f"acme-{uuid4().hex[:6]}", owner_id=user.id)
    session.add(tenant)
    await session.commit()
    return tenant


# --------------------------------------------------------------------------- FIX 3: tick lease


async def test_lease_single_winner_and_expired_reclaim(test_session):
    svc = ConversationEvaluationService()
    holder_a = "hostA:1"
    holder_b = "hostB:2"

    assert await svc._acquire_lease(test_session, holder_a, ttl_seconds=100) is True
    # Second, different holder cannot steal a live lease.
    assert await svc._acquire_lease(test_session, holder_b, ttl_seconds=100) is False
    # Same holder renews re-entrantly.
    assert await svc._acquire_lease(test_session, holder_a, ttl_seconds=100) is True

    # Force the lease to expire; another holder can now reclaim it.
    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    await test_session.execute(update(SkillLoopLease).where(SkillLoopLease.id == 1).values(expires_at=past))
    await test_session.commit()

    assert await svc._acquire_lease(test_session, holder_b, ttl_seconds=100) is True
    test_session.expire_all()
    row = (await test_session.execute(select(SkillLoopLease))).scalar_one()
    assert row.holder == holder_b


async def test_interval_marker_claims_once_per_interval(test_session):
    svc = ConversationEvaluationService()

    assert await svc._claim_interval_marker(test_session, 2, "code_sync", interval_seconds=86400) is True
    # Within the interval, no further claims succeed — even for the same holder.
    assert await svc._claim_interval_marker(test_session, 2, "code_sync", interval_seconds=86400) is False

    past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    await test_session.execute(update(SkillLoopLease).where(SkillLoopLease.id == 2).values(expires_at=past))
    await test_session.commit()

    assert await svc._claim_interval_marker(test_session, 2, "code_sync", interval_seconds=86400) is True


# --------------------------------------------------------------------------- FIX 3: durable digest dedup


async def test_digest_claim_once_per_day_and_resets_next_day(test_session):
    tenant = await _seed_tenant(test_session)
    tenant_id = tenant.id
    svc = ConversationEvaluationService()
    today = date(2026, 7, 9)
    tomorrow = date(2026, 7, 10)

    # First attempt today wins, second attempt today is a no-op (already sent).
    assert await svc._claim_digest(test_session, tenant_id, today) is True
    assert await svc._claim_digest(test_session, tenant_id, today) is False

    test_session.expire_all()
    settings = await SkillLoopSettingsRepository(test_session).get(tenant_id)
    assert settings is not None and settings.last_digest_date == today

    # A new day resets the claim.
    assert await svc._claim_digest(test_session, tenant_id, tomorrow) is True


# --------------------------------------------------------------------------- FIX 2 + FIX 5: agent request


async def test_run_agent_marks_request_as_preview(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    notebook = Notebook(id=uuid4(), tenant_id=tenant.id, notebook_name="NB")

    captured: dict = {}

    async def fake_stream(agent_request, session, tenant_id=None):
        captured["request"] = agent_request
        yield 'data: {"type": "content", "text": "hello"}'

    monkeypatch.setattr("server.services.unified_agent.stream_handoff_agent_response", fake_stream)

    svc = ConversationEvaluationService()
    svc._resolve_llm_connection = AsyncMock(return_value=uuid4())

    result = await svc._run_agent(test_session, notebook, "verify this", tenant.id)

    assert result == "hello"
    assert captured["request"].is_preview is True


async def test_run_agent_returns_empty_on_timeout(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    notebook = Notebook(id=uuid4(), tenant_id=tenant.id, notebook_name="NB")

    async def hanging_stream(agent_request, session, tenant_id=None):
        await asyncio.sleep(30)
        yield "data: never"

    monkeypatch.setattr("server.services.unified_agent.stream_handoff_agent_response", hanging_stream)
    monkeypatch.setattr(
        "server.services.conversation_evaluation_service.get_skill_loop_config",
        lambda: {"agent_timeout_seconds": 0},
    )

    svc = ConversationEvaluationService()
    svc._resolve_llm_connection = AsyncMock(return_value=uuid4())

    result = await svc._run_agent(test_session, notebook, "verify this", tenant.id)
    assert result == ""


# --------------------------------------------------------------------------- FIX 6: repo sweep bounds


async def _seed_repo(session, tenant, *, name: str) -> GitHubRepository:
    repo = GitHubRepository(
        id=uuid4(),
        tenant_id=tenant.id,
        user_id=tenant.owner_id,
        source="github",
        repo_full_name=name,
        default_branch="main",
        skill_sync_enabled=True,
        last_analyzed_sha="base",
        analysis_status="completed",
        is_active=True,
    )
    session.add(repo)
    await session.commit()
    return repo


async def test_repo_cap_limits_processing(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    for i in range(30):
        await _seed_repo(test_session, tenant, name=f"acme/app-{i}")

    monkeypatch.setattr(
        "server.services.repo_sync_service.get_skill_loop_config",
        lambda: {"code_sync_enabled": True, "code_sessions_per_day": 10, "code_max_skills_per_tick": 3},
    )

    svc = RepoSyncService()
    svc._sync_repo = AsyncMock()
    await svc.tick(test_session)

    assert svc._sync_repo.await_count == 25


async def test_per_repo_failure_rolls_back_session(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    await _seed_repo(test_session, tenant, name="acme/one")
    await _seed_repo(test_session, tenant, name="acme/two")

    monkeypatch.setattr(
        "server.services.repo_sync_service.get_skill_loop_config",
        lambda: {"code_sync_enabled": True, "code_sessions_per_day": 10, "code_max_skills_per_tick": 3},
    )

    rollback_spy = AsyncMock()
    monkeypatch.setattr(test_session, "rollback", rollback_spy)

    svc = RepoSyncService()
    svc._sync_repo = AsyncMock(side_effect=RuntimeError("boom"))
    await svc.tick(test_session)

    assert svc._sync_repo.await_count == 2
    assert rollback_spy.await_count == 2
