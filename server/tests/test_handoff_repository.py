from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import update

from server.models.github_repository import GitHubRepository
from server.models.handoff import Handoff
from server.models.tenant import Tenant
from server.models.user import User
from server.repositories.handoffs import (
    ALLOWED_TRANSITIONS,
    HandoffEventRepository,
    HandoffMessageRepository,
    HandoffRepository,
    sources_for,
    utcnow,
)

pytestmark = pytest.mark.handoff


@pytest_asyncio.fixture
async def repo_ctx(test_session):
    uid, tid = uuid4(), uuid4()
    test_session.add(User(id=uid, email=f"{uid}@test.com", hashed_password="x", is_active=True, is_verified=True))
    await test_session.flush()
    test_session.add(Tenant(id=tid, name="T", slug=f"t-{tid}", owner_id=uid, is_personal=True))
    await test_session.flush()
    gh = GitHubRepository(tenant_id=tid, user_id=uid, repo_full_name="acme/api", default_branch="main")
    test_session.add(gh)
    await test_session.commit()
    return tid, uid, gh.id


async def _make(session, tid, repo_id, status="queued") -> Handoff:
    handoff = Handoff(
        tenant_id=tid,
        github_repository_id=repo_id,
        repo_full_name="acme/api",
        origin="notebook",
        title="Fix",
        brief={"problem": "p"},
        status=status,
        base_branch="main",
    )
    session.add(handoff)
    await session.commit()
    return handoff


def test_every_target_is_a_known_status():
    statuses = set(ALLOWED_TRANSITIONS)
    for targets in ALLOWED_TRANSITIONS.values():
        assert targets <= statuses


def test_terminal_statuses_only_reopen_for_followups():
    assert ALLOWED_TRANSITIONS["failed"] == {"queued"}
    assert ALLOWED_TRANSITIONS["cancelled"] == {"queued"}
    assert ALLOWED_TRANSITIONS["done"] == {"queued"}
    assert ALLOWED_TRANSITIONS["taken_over"] == set()
    assert ALLOWED_TRANSITIONS["dismissed"] == set()
    assert "pushing" not in sources_for("cancelled")


async def test_transition_is_conditional(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    handoff = await _make(test_session, tid, repo_id, status="proposed")
    repo = HandoffRepository(test_session)

    assert await repo.transition(handoff.id, "queued")
    assert not await repo.transition(handoff.id, "queued")
    assert await repo.status_of(handoff.id) == "queued"


async def test_transition_rejects_invalid_source(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    handoff = await _make(test_session, tid, repo_id, status="pushing")
    repo = HandoffRepository(test_session)

    assert not await repo.transition(handoff.id, "cancelled", from_statuses=("running", "queued"))
    with pytest.raises(ValueError):
        await repo.transition(handoff.id, "running", from_statuses=("done",))


async def test_terminal_transition_sets_finished_at(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    handoff = await _make(test_session, tid, repo_id, status="queued")
    repo = HandoffRepository(test_session)

    assert await repo.transition(handoff.id, "cancelled")
    refreshed = await repo.get_unscoped(handoff.id)
    await test_session.refresh(refreshed)
    assert refreshed.finished_at is not None


async def test_one_working_handoff_per_repo(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    first_id = (await _make(test_session, tid, repo_id)).id
    second_id = (await _make(test_session, tid, repo_id)).id
    repo = HandoffRepository(test_session)

    assert await repo.transition(first_id, "preparing", worker_id="w1")
    assert not await repo.transition(second_id, "preparing", worker_id="w2")
    assert await repo.status_of(second_id) == "queued"

    assert await repo.transition(first_id, "running")
    assert await repo.transition(first_id, "pushing")
    assert await repo.transition(first_id, "ci_running")
    assert await repo.transition(second_id, "preparing", worker_id="w2")


async def test_claim_poll_claims_once(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    handoff = await _make(test_session, tid, repo_id, status="ci_running")
    await test_session.execute(
        update(Handoff).where(Handoff.id == handoff.id).values(next_check_at=utcnow() - timedelta(seconds=1))
    )
    await test_session.commit()
    repo = HandoffRepository(test_session)

    assert await repo.claim_poll(("ci_running",), timedelta(seconds=30)) == [handoff.id]
    assert await repo.claim_poll(("ci_running",), timedelta(seconds=30)) == []


async def test_events_get_sequential_seq(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    handoff = await _make(test_session, tid, repo_id)
    events = HandoffEventRepository(test_session)

    first = await events.append(handoff.id, "system", "status", "a")
    second = await events.append(handoff.id, "agent", "progress", "b")

    assert (first.seq, second.seq) == (1, 2)
    assert [e.message for e in await events.after(handoff.id, 1)] == ["b"]
    assert (await events.latest_of_kind(handoff.id, "status")).message == "a"


async def test_inbox_delivery(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    handoff = await _make(test_session, tid, repo_id)
    inbox = HandoffMessageRepository(test_session)

    msg = await inbox.add(handoff.id, "followup", "also flag them")
    assert [m.id for m in await inbox.pending(handoff.id, "followup")] == [msg.id]

    await inbox.mark_delivered([msg.id])
    assert await inbox.pending(handoff.id, "followup") == []


async def test_fingerprint_cooldown(test_session, repo_ctx):
    tid, _, repo_id = repo_ctx
    handoff = await _make(test_session, tid, repo_id, status="dismissed")
    schedule_id = uuid4()
    await test_session.execute(
        update(Handoff)
        .where(Handoff.id == handoff.id)
        .values(fingerprint="fp", finished_at=utcnow() - timedelta(days=10))
    )
    await test_session.commit()
    repo = HandoffRepository(test_session)

    assert await repo.find_by_fingerprint(schedule_id, "fp", utcnow() - timedelta(days=7)) is None
