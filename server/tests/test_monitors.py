"""Tests for Monitor (cron) tools, repo helpers and service-side notify policy."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from server.models.notebooks import Notebook
from server.models.tenant import Tenant
from server.models.user import User
from server.repositories.schedules import ScheduleRepository
from server.services.schedule_service import ScheduleService
from server.services.slack_context import SlackContext, get_slack_context, slack_context_scope
from server.tools import monitors


@pytest_asyncio.fixture
async def tenant_and_notebook(test_session):
    uid = uuid4()
    tid = uuid4()
    test_session.add(User(id=uid, email=f"{uid}@test.com", hashed_password="x", is_active=True, is_verified=True))
    await test_session.flush()
    test_session.add(Tenant(id=tid, name="MonitorTenant", slug=f"t-{tid}", owner_id=uid, is_personal=True))
    await test_session.commit()
    nb = Notebook(tenant_id=tid, notebook_name="Nb")
    test_session.add(nb)
    await test_session.commit()
    await test_session.refresh(nb)
    return tid, nb.id, uid


def test_validate_cron_rejects_sub_hour():
    ok, msg = monitors._validate_cron("* * * * *")
    assert not ok
    assert "hour" in (msg or "").lower()


def test_validate_cron_accepts_hourly():
    ok, msg = monitors._validate_cron("0 * * * *")
    assert ok, msg


def test_validate_cron_rejects_garbage():
    ok, _ = monitors._validate_cron("not a cron")
    assert not ok


def test_slack_context_scope_isolates():
    ctx = SlackContext(
        workspace_id=uuid4(),
        tenant_id=uuid4(),
        team_id="T1",
        channel_id="C1",
        thread_ts="1.1",
        slack_user_id="U1",
        user_timezone="UTC",
        byaan_user_id=uuid4(),
    )
    assert get_slack_context() is None
    with slack_context_scope(ctx):
        assert get_slack_context() is ctx
    assert get_slack_context() is None


@pytest.mark.asyncio
async def test_repo_create_count_and_statuses(test_session, tenant_and_notebook):
    tenant_id, notebook_id, _ = tenant_and_notebook
    repo = ScheduleRepository(test_session)

    sched = await repo.create(
        {
            "tenant_id": tenant_id,
            "notebook_id": notebook_id,
            "name": "nightly",
            "cron_expression": "0 0 * * *",
            "timezone": "UTC",
            "is_enabled": False,
            "status": "draft",
            "origin": "notebook_chat",
            "notify_policy": "always",
        }
    )
    assert sched.status == "draft"
    assert await repo.count_for_tenant(tenant_id) == 1

    updated = await repo.set_status(sched.id, "active")
    assert updated is not None
    assert updated.status == "active"
    assert updated.is_enabled is True

    paused = await repo.set_status(sched.id, "paused")
    assert paused.status == "paused"
    assert paused.is_enabled is False


@pytest.mark.asyncio
async def test_repo_lease_claim_and_reclaim(test_session, tenant_and_notebook):
    tenant_id, notebook_id, _ = tenant_and_notebook
    repo = ScheduleRepository(test_session)

    sched = await repo.create(
        {
            "tenant_id": tenant_id,
            "notebook_id": notebook_id,
            "name": "nightly",
            "cron_expression": "0 0 * * *",
            "timezone": "UTC",
            "status": "active",
        }
    )
    now = datetime.utcnow()
    assert await repo.claim_with_lease(sched.id, now) is True
    assert await repo.claim_with_lease(sched.id, now) is False  # already running

    # Simulate a crashed run: push running_since into the past.
    old = now - timedelta(hours=1)
    await test_session.execute(
        __import__("sqlalchemy").update(repo._model).where(repo._model.id == sched.id).values(running_since=old)
    )
    await test_session.commit()
    reclaimed = await repo.reclaim_stale_leases(now - timedelta(minutes=10))
    assert reclaimed >= 1


@pytest.mark.asyncio
async def test_repo_increment_failure_and_reset(test_session, tenant_and_notebook):
    tenant_id, notebook_id, _ = tenant_and_notebook
    repo = ScheduleRepository(test_session)
    sched = await repo.create(
        {
            "tenant_id": tenant_id,
            "notebook_id": notebook_id,
            "name": "x",
            "cron_expression": "0 0 * * *",
            "timezone": "UTC",
            "status": "active",
        }
    )
    assert await repo.increment_failure(sched.id) == 1
    assert await repo.increment_failure(sched.id) == 2
    await repo.reset_failure(sched.id)
    await test_session.refresh(sched)
    assert sched.consecutive_failures == 0


@pytest.mark.asyncio
async def test_repo_cleanup_old_drafts(test_session, tenant_and_notebook):
    tenant_id, notebook_id, _ = tenant_and_notebook
    repo = ScheduleRepository(test_session)
    sched = await repo.create(
        {
            "tenant_id": tenant_id,
            "notebook_id": notebook_id,
            "name": "d",
            "cron_expression": "0 0 * * *",
            "timezone": "UTC",
            "status": "draft",
            "is_enabled": False,
        }
    )
    old = datetime.utcnow() - timedelta(days=10)
    await test_session.execute(
        __import__("sqlalchemy").update(repo._model).where(repo._model.id == sched.id).values(updated_at=old)
    )
    await test_session.commit()
    removed = await repo.cleanup_old_drafts(datetime.utcnow() - timedelta(days=7))
    assert removed >= 1


def test_should_notify_policy_matrix():
    svc = ScheduleService
    body = "report body"
    assert svc._should_notify("always", "ok", body) is True
    assert svc._should_notify("always", "alert", body) is True
    assert svc._should_notify("on_alert", "ok", body) is False
    assert svc._should_notify("on_alert", "alert", body) is True
    assert svc._should_notify("on_change", "no_change", body) is False
    assert svc._should_notify("on_change", "alert", body) is True
    assert svc._should_notify("always", "ok", "") is False


def test_preview_fire_times_matches_timezone():
    fires = ScheduleService.preview_fire_times("0 9 * * *", "UTC", count=3)
    assert len(fires) == 3
    assert fires[0] < fires[1] < fires[2]


def test_feature_check_disabled(monkeypatch):
    monkeypatch.setattr(monitors, "is_feature_enabled", lambda _f: False)
    err = monitors._feature_check()
    assert err is not None
    data = json.loads(err)
    assert data["ok"] is False
    assert data["error"]["code"] == "feature_disabled"
