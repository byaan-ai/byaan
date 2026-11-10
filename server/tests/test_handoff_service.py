from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from server.models.github_repository import GitHubRepository
from server.models.handoff import Handoff, HandoffEvent
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.models.user import User
from server.repositories.handoffs import HandoffMessageRepository, HandoffRepository, utcnow
from server.schemas.handoff import HandoffBrief
from server.services import handoff_service
from server.services.handoff_notifier import build_card_blocks
from server.services.handoff_service import Actor, HandoffError
from server.services.slack_agent_service import SlackAgentService

pytestmark = pytest.mark.handoff


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("server.services.handoff_notifier.notify_event", noop)


@pytest_asyncio.fixture
async def ctx(test_session):
    uid, tid = uuid4(), uuid4()
    test_session.add(User(id=uid, email="dev@acme.test", hashed_password="x", is_active=True, is_verified=True))
    await test_session.flush()
    test_session.add(Tenant(id=tid, name="T", slug=f"t-{tid}", owner_id=uid, is_personal=True))
    await test_session.flush()
    test_session.add(TenantMember(tenant_id=tid, user_id=uid, role=TenantRole.OWNER))
    api = GitHubRepository(
        tenant_id=tid,
        user_id=uid,
        repo_full_name="acme/care-backend",
        default_branch="main",
        handoff_enabled=True,
    )
    web = GitHubRepository(
        tenant_id=tid, user_id=uid, repo_full_name="acme/care-web", default_branch="main", handoff_enabled=True
    )
    off = GitHubRepository(tenant_id=tid, user_id=uid, repo_full_name="acme/off", default_branch="main")
    test_session.add_all([api, web, off])
    await test_session.commit()
    return SimpleNamespace(tenant=tid, user=uid, api=api, web=web, off=off)


async def propose(session, **kwargs):
    return (await handoff_service.propose(session, **kwargs)).handoff


BRIEF = HandoffBrief(
    problem="tz=None skips 214 reminders",
    suspected_files=["reminders/scheduler.py:88"],
    acceptance_check="test_reminder_without_patient_tz passes",
)


async def test_repo_resolution_order(test_session, ctx):
    resolve = handoff_service.resolve_repo
    assert (await resolve(test_session, ctx.tenant, repo_hint="care-web"))[0].id == ctx.web.id
    assert (await resolve(test_session, ctx.tenant, repo_hint="other/care-web"))[0] is None
    repo, candidates = await resolve(test_session, ctx.tenant, repo_hint=None)
    assert repo is None and {c.id for c in candidates} == {ctx.api.id, ctx.web.id}
    assert (await resolve(test_session, ctx.tenant, repo_hint="acme/off"))[0] is None


async def test_propose_supersedes_open_proposal_in_same_notebook(test_session, ctx):
    notebook_id = None
    first = await propose(
        test_session,
        tenant_id=ctx.tenant,
        requested_by=ctx.user,
        origin="notebook",
        title="a",
        brief=BRIEF,
        repo_hint="care-web",
        notebook_id=notebook_id,
    )
    assert first.status == "proposed" and first.base_branch == "main"
    with pytest.raises(HandoffError):
        await handoff_service.start(test_session, ctx.tenant, uuid4(), Actor(user_id=ctx.user))


async def test_monitor_proposal_rules_and_dedupe(test_session, ctx):
    from server.models.notebooks import Notebook
    from server.models.schedules import Schedule

    notebook = Notebook(tenant_id=ctx.tenant, notebook_name="m")
    test_session.add(notebook)
    await test_session.commit()
    schedule = Schedule(
        tenant_id=ctx.tenant, notebook_id=notebook.id, name="Daily reminders", cron_expression="0 9 * * *"
    )
    test_session.add(schedule)
    await test_session.commit()

    vague = HandoffBrief(problem="reminders look off", suspected_files=["somewhere"], acceptance_check="")
    with pytest.raises(HandoffError, match="file:line"):
        await handoff_service.propose(
            test_session,
            tenant_id=ctx.tenant,
            requested_by=None,
            origin="notebook",
            title="x",
            brief=vague,
            repo_hint="care-backend",
            schedule_id=schedule.id,
            notebook_id=notebook.id,
        )

    handoff = await propose(
        test_session,
        tenant_id=ctx.tenant,
        requested_by=None,
        origin="notebook",
        title="Fix tz",
        brief=BRIEF,
        repo_hint="care-backend",
        schedule_id=schedule.id,
        notebook_id=notebook.id,
    )
    assert handoff.fingerprint
    with pytest.raises(HandoffError):
        await handoff_service.propose(
            test_session,
            tenant_id=ctx.tenant,
            requested_by=None,
            origin="notebook",
            title="Fix tz again",
            brief=BRIEF,
            repo_hint="care-backend",
            schedule_id=schedule.id,
            notebook_id=notebook.id,
        )


async def test_start_is_idempotent_and_cancel_refuses_pushing(test_session, ctx):
    handoff = await propose(
        test_session,
        tenant_id=ctx.tenant,
        requested_by=ctx.user,
        origin="notebook",
        title="t",
        brief=BRIEF,
        repo_hint="care-web",
    )
    handoff_id = handoff.id
    actor = Actor(user_id=ctx.user, label="Hadi")
    await handoff_service.start(test_session, ctx.tenant, handoff_id, actor)
    with pytest.raises(HandoffError, match="already started"):
        await handoff_service.start(test_session, ctx.tenant, handoff_id, actor)

    await HandoffRepository(test_session).update_fields(handoff_id, status="pushing")
    with pytest.raises(HandoffError, match="pushing"):
        await handoff_service.cancel(test_session, ctx.tenant, handoff_id, actor)


async def test_message_routing(test_session, ctx):
    handoff = await propose(
        test_session,
        tenant_id=ctx.tenant,
        requested_by=ctx.user,
        origin="notebook",
        title="t",
        brief=BRIEF,
        repo_hint="care-web",
    )
    handoff_id = handoff.id
    repo = HandoffRepository(test_session)
    actor = Actor(user_id=ctx.user, label="Soha")

    await repo.update_fields(handoff_id, status="waiting_on_human")
    assert (
        await handoff_service.add_message(test_session, ctx.tenant, handoff_id, "yes, flag them", actor) == "answered"
    )

    await repo.update_fields(handoff_id, status="running")
    outcome = await handoff_service.add_message(test_session, ctx.tenant, handoff_id, "also log it", actor)
    assert outcome == "queued_for_running_session"

    await repo.update_fields(handoff_id, status="failed")
    assert await handoff_service.add_message(test_session, ctx.tenant, handoff_id, "more", actor) == "requeued"

    await repo.update_fields(handoff_id, status="dismissed")
    with pytest.raises(HandoffError):
        await handoff_service.add_message(test_session, ctx.tenant, handoff_id, "more", actor)


async def test_other_tenant_cannot_see_handoff(test_session, ctx):
    handoff = await propose(
        test_session,
        tenant_id=ctx.tenant,
        requested_by=ctx.user,
        origin="notebook",
        title="t",
        brief=BRIEF,
        repo_hint="care-web",
    )
    with pytest.raises(HandoffError, match="not found"):
        await handoff_service.get_for_tenant(test_session, uuid4(), handoff.id)


class FakeSlack:
    def __init__(self, info):
        self.info = info

    async def get_user_info(self, _user_id):
        return self.info


@pytest.mark.parametrize(
    ("user_team", "info", "byaan_user", "reason"),
    [
        ("T1", {"name": "Dev", "email": "dev@acme.test"}, True, None),
        ("T1", {"name": "Guest", "email": "stranger@x.test", "is_restricted": True}, False, None),
        ("T1", None, False, None),
        ("T-OTHER", {"name": "Ext", "email": "dev@acme.test"}, False, "members of this Slack workspace"),
        (None, None, False, "members of this Slack workspace"),
        ("T1", {"is_bot": True}, False, "can't control handoffs"),
    ],
)
async def test_any_slack_workspace_member_can_control_handoffs(test_session, ctx, user_team, info, byaan_user, reason):
    workspace = SimpleNamespace(slack_team_id="T1", tenant_id=ctx.tenant, installed_by=ctx.user)
    user_id, _name, rejection = await SlackAgentService.resolve_slack_actor(
        workspace, "U1", user_team, FakeSlack(info), test_session
    )
    assert (user_id == ctx.user) is byaan_user
    if reason:
        assert reason in rejection
    else:
        assert rejection is None


def _handoff(status: str, **fields) -> Handoff:
    return Handoff(
        id=uuid4(),
        tenant_id=uuid4(),
        repo_full_name="acme/api",
        origin="slack",
        title="Fix",
        brief={"problem": "p"},
        status=status,
        base_branch="main",
        github_repository_id=uuid4(),
        untested=False,
        ci_fix_round=0,
        cost_usd=0.0,
        **fields,
    )


def _action_ids(blocks) -> set[str]:
    return {e.get("action_id") for b in blocks if b["type"] == "actions" for e in b["elements"]}


@pytest.mark.parametrize(
    ("status", "fields", "expected"),
    [
        ("proposed", {}, {"handoff_start", "handoff_edit", "handoff_dismiss"}),
        ("running", {"branch": "byaan/h-1"}, {"handoff_take_over", "handoff_stop", "handoff_view_brief"}),
        ("pushing", {}, {"handoff_view_brief"}),
        (
            "ci_running",
            {"branch": "b", "pr_url": "https://x/1", "pr_number": 1},
            {"handoff_open_pr", "handoff_take_over", "handoff_stop", "handoff_view_brief"},
        ),
        (
            "done",
            {"pr_url": "https://x/1", "pr_number": 1},
            {"handoff_open_pr", "handoff_ask_changes", "handoff_view_brief"},
        ),
        ("queued", {"branch": "byaan/h-1"}, {"handoff_stop", "handoff_view_brief"}),
        ("failed", {}, {"handoff_restart", "handoff_view_brief"}),
    ],
)
def test_card_buttons_follow_status(status, fields, expected):
    assert _action_ids(build_card_blocks(_handoff(status, **fields), {})) == expected


def test_handoff_tools_registered_in_both_modes(monkeypatch):
    from server.services.unified_agent import _build_agent_tools

    assert "propose_handoff" not in {t.name for t in _build_agent_tools([{"db_type": "postgres"}])}
    names = {t.name for t in _build_agent_tools([{"db_type": "postgres"}], has_handoff_repos=True)}
    assert {
        "propose_handoff",
        "get_handoff_status",
        "start_handoff",
        "message_handoff",
        "restart_handoff",
        "cancel_handoff",
        "list_handoffs",
    } <= names

    scheduled = {t.name for t in _build_agent_tools([{"db_type": "postgres"}], scheduled_run=True)}
    assert "propose_handoff" not in scheduled
    allowed = {
        t.name
        for t in _build_agent_tools(
            [{"db_type": "postgres"}],
            scheduled_run=True,
            has_github_repos=True,
            has_handoff_repos=True,
        )
    }
    assert {"propose_handoff", "get_repo_file"} <= allowed
    assert "message_handoff" not in allowed and "start_handoff" not in allowed and "create_repo_skill" not in allowed


def test_activity_summary_from_tool_events():
    from server.models.handoff import HandoffEvent
    from server.services.handoff_service import activity_summary

    def tool(seq, name, target):
        return HandoffEvent(
            seq=seq, source="executor", kind="tool", message=f"{name} {target}", data={"tool": name, "target": target}
        )

    events = [
        tool(1, "Read", "/tmp/byaan-handoffs/abc/work/app.py"),
        tool(2, "Read", "db.py"),
        tool(3, "Read", "db.py"),
        tool(4, "Grep", "quantity"),
        tool(5, "Edit", "app.py"),
        tool(6, "Write", "tests/test_x.py"),
        tool(7, "Bash", "git diff"),
        tool(8, "mcp__byaan_handoff__submit_result", ""),
    ]
    summary = activity_summary(events)
    assert summary["read"] == ["app.py", "db.py"]
    assert summary["edited"] == ["app.py"] and summary["written"] == ["tests/test_x.py"]
    assert summary["reviewed"]
    assert summary["lines"][-1] == "Reviewed its changes with git"


def test_activity_summary_groups_commands_and_detects_git_review():
    from server.models.handoff import HandoffEvent
    from server.services.handoff_service import activity_summary

    def bash(seq, command):
        return HandoffEvent(
            seq=seq, source="executor", kind="tool", message=f"Bash {command}", data={"tool": "Bash", "target": command}
        )

    summary = activity_summary(
        [
            bash(1, "pip install -q -r requirements.txt 2>&1 | tail -1"),
            bash(2, "python -m pytest -q; ruff check . --fix"),
            bash(3, "if true; then python -m pytest; fi"),
            bash(4, "git -c safe.directory='*' diff --stat"),
        ]
    )
    assert summary["programs"] == {"pytest": 2, "git diff": 1, "pip install": 1, "ruff": 1}
    assert summary["reviewed"]


async def test_restart_on_a_pr_backed_failed_handoff_reruns_on_the_same_pr(test_session, ctx, monkeypatch):
    async def pr_open(*_args, **_kwargs):
        return False

    monkeypatch.setattr(handoff_service, "_pr_is_closed", pr_open)
    handoff = await propose(
        test_session,
        tenant_id=ctx.tenant,
        requested_by=ctx.user,
        origin="notebook",
        title="t",
        brief=BRIEF,
        repo_hint="care-web",
    )
    handoff_id = handoff.id
    actor = Actor(user_id=ctx.user, label="Soha")
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, status="done", pr_number=4, branch="byaan/h-1")
    await handoff_service.add_message(test_session, ctx.tenant, handoff_id, "Fix the CI failures", actor)
    await repo.update_fields(handoff_id, status="failed", error="Interrupted")

    retried = await handoff_service.restart(test_session, ctx.tenant, handoff_id, actor)

    assert retried.id == handoff_id and retried.status == "queued" and retried.pr_number == 4
    pending = await HandoffMessageRepository(test_session).pending(handoff_id, "followup")
    assert pending[-1].body == "Fix the CI failures"


def _no_pr_check(monkeypatch, closed=False):
    async def pr_closed(*_args, **_kwargs):
        return closed

    monkeypatch.setattr(handoff_service, "_pr_is_closed", pr_closed)


async def _draft(session, ctx, title="Fix tz", **extra):
    return await handoff_service.propose(
        session,
        tenant_id=ctx.tenant,
        requested_by=ctx.user,
        origin="notebook",
        title=title,
        brief=BRIEF,
        repo_hint="care-web",
        notebook_id=ctx.notebook_id,
        actor=Actor(user_id=ctx.user, label="Soha"),
        **extra,
    )


@pytest_asyncio.fixture
async def nb_ctx(test_session, ctx):
    from server.models.notebooks import Notebook

    notebook = Notebook(tenant_id=ctx.tenant, notebook_name="thread")
    test_session.add(notebook)
    await test_session.commit()
    ctx.notebook_id = notebook.id
    return ctx


async def test_redrafting_an_unstarted_task_revises_the_same_draft(test_session, nb_ctx):
    first = await _draft(test_session, nb_ctx)
    second = await _draft(test_session, nb_ctx, title="Fix tz (revised)")
    assert first.action == "created" and second.action == "revised_draft"
    assert second.handoff.id == first.handoff.id and second.handoff.title == "Fix tz (revised)"


async def test_redrafting_a_finished_task_saves_a_pending_revision_and_restart_applies_it(
    test_session, nb_ctx, monkeypatch
):
    _no_pr_check(monkeypatch)
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, status="done", pr_number=5, branch="byaan/h-1", started_at=utcnow())

    outcome = await _draft(test_session, nb_ctx, title="Also handle eleven")
    assert outcome.action == "pending_revision" and outcome.handoff.id == handoff_id
    assert outcome.handoff.status == "done"
    inbox = HandoffMessageRepository(test_session)
    assert (await inbox.pending(handoff_id, "revision"))[0].body.startswith("Revised task: Also handle eleven")

    restarted = await handoff_service.restart(test_session, nb_ctx.tenant, handoff_id, Actor(user_id=nb_ctx.user))
    assert restarted.id == handoff_id and restarted.status == "queued" and restarted.pr_number == 5
    assert await inbox.pending(handoff_id, "revision") == []
    assert (await inbox.pending(handoff_id, "followup"))[0].body.startswith("Revised task: Also handle eleven")


async def test_redrafting_a_running_task_is_delivered_to_it(test_session, nb_ctx):
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    await HandoffRepository(test_session).update_fields(handoff_id, status="running")
    outcome = await _draft(test_session, nb_ctx, title="Also log it")
    assert outcome.action == "sent_to_running" and outcome.handoff.id == handoff_id
    assert len(await HandoffMessageRepository(test_session).pending(handoff_id, "followup")) == 1


async def test_new_task_creates_a_separate_handoff(test_session, nb_ctx):
    first = await _draft(test_session, nb_ctx)
    other = await _draft(test_session, nb_ctx, title="Add /metrics", new_task=True)
    assert other.action == "created" and other.handoff.id != first.handoff.id
    assert (await HandoffRepository(test_session).status_of(first.handoff.id)) == "proposed"


async def test_merged_pr_means_the_next_draft_is_a_new_handoff(test_session, nb_ctx, monkeypatch):
    _no_pr_check(monkeypatch, closed=True)
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    await HandoffRepository(test_session).update_fields(handoff_id, status="done", pr_number=5, branch="b")
    outcome = await _draft(test_session, nb_ctx, title="Next change")
    assert outcome.action == "created" and outcome.handoff.id != handoff_id


async def test_failed_task_without_pr_restarts_on_the_same_handoff(test_session, nb_ctx, monkeypatch):
    _no_pr_check(monkeypatch)
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, status="failed", error="Interrupted", branch="byaan/h-1")
    actor = Actor(user_id=nb_ctx.user, label="Soha")

    assert await handoff_service.add_message(test_session, nb_ctx.tenant, handoff_id, "go again", actor) == "requeued"
    assert await repo.status_of(handoff_id) == "queued"
    await repo.update_fields(handoff_id, status="failed")
    restarted = await handoff_service.restart(test_session, nb_ctx.tenant, handoff_id, actor)
    assert restarted.id == handoff_id and restarted.status == "queued" and restarted.error is None


async def test_revision_targets_the_named_handoff_when_a_thread_has_several(test_session, nb_ctx, monkeypatch):
    _no_pr_check(monkeypatch)
    repo = HandoffRepository(test_session)
    task_a = (await _draft(test_session, nb_ctx, title="Task A")).handoff.id
    await repo.update_fields(task_a, status="done", pr_number=4, branch="byaan/a", started_at=utcnow())
    task_b = (await _draft(test_session, nb_ctx, title="Task B", new_task=True)).handoff.id
    await repo.update_fields(task_b, status="done", pr_number=5, branch="byaan/b", started_at=utcnow())

    outcome = await _draft(test_session, nb_ctx, title="Task A again", revise_handoff_id=task_a)

    assert outcome.action == "pending_revision" and outcome.handoff.id == task_a
    inbox = HandoffMessageRepository(test_session)
    assert len(await inbox.pending(task_a, "revision")) == 1
    assert await inbox.pending(task_b, "revision") == []


async def test_revise_and_new_task_together_is_rejected(test_session, nb_ctx):
    task = (await _draft(test_session, nb_ctx)).handoff.id
    with pytest.raises(HandoffError, match="not both"):
        await _draft(test_session, nb_ctx, revise_handoff_id=task, new_task=True)


async def test_message_on_a_handoff_with_a_pending_revision_keeps_the_revision(test_session, nb_ctx, monkeypatch):
    _no_pr_check(monkeypatch)
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    await HandoffRepository(test_session).update_fields(
        handoff_id, status="done", pr_number=4, branch="byaan/a", started_at=utcnow()
    )
    await _draft(test_session, nb_ctx, title="Handle eleven too")

    actor = Actor(user_id=nb_ctx.user, label="Soha")
    assert await handoff_service.add_message(test_session, nb_ctx.tenant, handoff_id, "and log it", actor) == "requeued"

    inbox = HandoffMessageRepository(test_session)
    body = (await inbox.pending(handoff_id, "followup"))[0].body
    assert body.startswith("Revised task: Handle eleven too") and body.endswith("Additional request: and log it")
    assert await inbox.pending(handoff_id, "revision") == []


async def test_restart_reports_what_it_applied_and_adopts_the_revised_brief(test_session, nb_ctx, monkeypatch):
    _no_pr_check(monkeypatch)

    async def no_title_sync(*_args, **_kwargs):
        return None

    monkeypatch.setattr("server.services.handoff_runner.sync_pr_title", no_title_sync)
    handoff_id = (await _draft(test_session, nb_ctx, title="Reject empty orders")).handoff.id
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, status="done", pr_number=6, branch="byaan/a", started_at=utcnow())
    revised = HandoffBrief(problem="Also reject orders over 50 items", acceptance_check="51 items is rejected")
    await handoff_service.propose(
        test_session,
        tenant_id=nb_ctx.tenant,
        requested_by=nb_ctx.user,
        origin="notebook",
        title="Reject empty and oversized orders",
        brief=revised,
        repo_hint="care-web",
        notebook_id=nb_ctx.notebook_id,
    )
    pending = await handoff_service.pending_revision(test_session, handoff_id)
    assert pending["title"] == "Reject empty and oversized orders"
    assert pending["brief"]["problem"] == "Also reject orders over 50 items"

    actor = Actor(user_id=nb_ctx.user, label="Soha")
    handoff, applied = await handoff_service.restart_with_outcome(test_session, nb_ctx.tenant, handoff_id, actor)
    assert applied == "pending_revision"
    assert handoff.title == "Reject empty and oversized orders"
    assert handoff.brief["problem"] == "Also reject orders over 50 items"
    assert await handoff_service.pending_revision(test_session, handoff_id) is None

    await repo.update_fields(handoff_id, status="done")
    _, applied = await handoff_service.restart_with_outcome(test_session, nb_ctx.tenant, handoff_id, actor)
    assert applied == "repeated_last_request"


async def test_status_drops_an_old_ci_failure_once_ci_passes(test_session, nb_ctx):
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    record = handoff_service.record
    await record(test_session, handoff_id, "ci", "ci", "CI failure", {"state": "failure"}, notify=False)
    await record(
        test_session,
        handoff_id,
        "executor",
        "status",
        "CI failed",
        {"failing": ["test"], "log_excerpt": "boom"},
        notify=False,
    )
    handoff = await HandoffRepository(test_session).get_unscoped(handoff_id)
    assert (await handoff_service.status_summary(test_session, handoff))["last_ci_failure"]["log_excerpt"] == "boom"

    await record(test_session, handoff_id, "ci", "ci", "CI success", {"state": "success"}, notify=False)
    assert (await handoff_service.status_summary(test_session, handoff))["last_ci_failure"] is None


class FakeSlackThread:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    async def post_message(self, channel, text, thread_ts=None, blocks=None, reply_broadcast=False, attachments=None):
        self.calls.append(("post", None))
        return {"ts": f"new-{len(self.calls)}"}

    async def update_message(self, channel, ts, text, blocks=None, attachments=None):
        self.calls.append(("update", ts))
        self.updated = attachments


async def test_slack_revision_freezes_the_old_card_and_posts_a_new_one(test_session, nb_ctx):
    from server.schemas.handoff import HandoffRepoConfig
    from server.services import handoff_notifier

    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, slack_card_ts="old-card")
    conversation = SimpleNamespace(id=uuid4(), slack_workspace_id=uuid4(), slack_channel_id="C1", slack_thread_ts="T1")
    client = FakeSlackThread()

    async def deliver(data):
        handoff = await repo.get_unscoped(handoff_id)
        event = await handoff_service.record(test_session, handoff_id, "system", "status", "x", data, notify=False)
        await handoff_notifier._deliver_slack(
            test_session, handoff, event, None, client, conversation, HandoffRepoConfig()
        )

    await deliver({"status": "running"})
    assert client.calls == [("update", "old-card")]

    client.calls.clear()
    await deliver({"revision": True})
    assert client.calls == [("update", "old-card"), ("post", None)]
    assert (await repo.get_unscoped(handoff_id)).slack_card_ts == "new-2"
    frozen = client.updated[0]["blocks"]
    assert "newer card below" in frozen[0]["text"]["text"]
    assert all(b["type"] != "actions" for b in frozen)

    frozen_with_pr = build_card_blocks(_handoff("done", pr_url="https://x/14", pr_number=14), {"superseded": True})
    assert _action_ids(frozen_with_pr) == {"handoff_open_pr"}


async def test_follow_up_push_reply_says_what_changed(test_session, nb_ctx):
    from server.services import handoff_notifier

    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, pr_number=9, pr_url="https://github.com/a/b/pull/9")
    handoff = await repo.get_unscoped(handoff_id)
    stats = {"files": 1, "added": 4, "removed": 0}

    async def reply(message, **data):
        event = await handoff_service.record(
            test_session, handoff_id, "executor", "status", message, {"status": "ci_running", **data}, notify=False
        )
        return await handoff_notifier._reply_text(test_session, handoff, event)

    update = await reply("Pushed the requested changes to PR #9", pr_updated=True, stats=stats, summary="Adds name")
    assert update.startswith("<https://github.com/a/b/pull/9|PR #9> updated:")
    assert "1 files · +4 −0 · CI running" in update and "Adds name" in update

    fix = await reply("Fix pushed, CI running again", pr_updated=False, stats=stats, summary="Lint")
    assert fix == "Fix pushed, CI running again"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_stop_and_failure_replies_leave_actions_to_the_card(status):
    from server.services.handoff_notifier import _reply_blocks

    event = HandoffEvent(kind="status", message="Soha stopped it", data={"status": status})
    blocks = _reply_blocks(_handoff(status), event, "Soha stopped it")
    assert [b["type"] for b in blocks] == ["section"]


async def test_start_handoff_tool_needs_a_draft_the_person_has_seen(test_session, nb_ctx, monkeypatch):
    import json

    from server.tools import handoffs as tools

    async def session_gen():
        yield test_session

    monkeypatch.setattr(tools, "is_feature_enabled", lambda _flag: True)
    monkeypatch.setattr(tools, "get_async_session", session_gen)
    start = tools.start_handoff.on_invoke_tool._invoke_tool_impl.__closure__[-1].cell_contents
    handoff_id = str((await _draft(test_session, nb_ctx)).handoff.id)

    def ctx(**extra):
        return SimpleNamespace(context={"tenant_id": str(nb_ctx.tenant), "user_id": str(nb_ctx.user), **extra})

    scheduled = json.loads(await start(ctx(scheduled_run=True), handoff_id))
    assert scheduled["error"]["code"] == "read_only"

    same_turn = json.loads(await start(ctx(**{tools.DRAFTED_THIS_TURN: {handoff_id}}), handoff_id))
    assert same_turn["error"]["code"] == "needs_review"
    assert (await HandoffRepository(test_session).get_unscoped(UUID(handoff_id))).status == "proposed"

    started = json.loads(await start(ctx(), handoff_id))
    assert started["ok"] and started["status"] == "queued"


async def _slack_conversation_id(session, ctx) -> UUID:
    from server.models.slack_conversation import SlackConversation
    from server.models.slack_workspace import SlackWorkspace

    workspace = SlackWorkspace(
        tenant_id=ctx.tenant, slack_team_id=f"T{uuid4().hex[:8]}", bot_token_encrypted="x", signing_secret_encrypted="x"
    )
    session.add(workspace)
    await session.flush()
    conversation = SlackConversation(
        slack_workspace_id=workspace.id, slack_channel_id="C1", slack_thread_ts="T1", slack_user_id="U1"
    )
    session.add(conversation)
    await session.flush()
    return conversation.id


async def test_thread_indicator_shows_only_while_a_handoff_works(test_session, nb_ctx):
    from server.services.handoff_notifier import HANDOFF_WORKING_STATUS, thread_indicator_status

    conversation_id = await _slack_conversation_id(test_session, nb_ctx)
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, slack_conversation_id=conversation_id)

    for status, expected in [
        ("proposed", ""),
        ("running", HANDOFF_WORKING_STATUS),
        ("ci_running", HANDOFF_WORKING_STATUS),
        ("waiting_on_human", ""),
        ("done", ""),
    ]:
        await repo.update_fields(handoff_id, status=status)
        assert await thread_indicator_status(test_session, conversation_id) == expected


async def test_chat_reply_keeps_the_indicator_while_a_handoff_works(test_session, nb_ctx, monkeypatch):
    import server.db.session as db_session
    from server.services.handoff_notifier import HANDOFF_WORKING_STATUS

    class Factory:
        async def __aenter__(self):
            return test_session

        async def __aexit__(self, *_exc):
            return False

    class FakeClient:
        statuses: list[str] = []

        async def set_assistant_status(self, channel, thread_ts, status):
            self.statuses.append(status)
            return True

    monkeypatch.setattr(db_session, "AsyncSessionFactory", lambda: Factory())
    monkeypatch.setattr("server.utils.deployment.is_feature_enabled", lambda _flag: True)
    conversation_id = await _slack_conversation_id(test_session, nb_ctx)
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    repo = HandoffRepository(test_session)
    await repo.update_fields(handoff_id, slack_conversation_id=conversation_id, status="running")
    client = FakeClient()

    await SlackAgentService._settle_status(client, "C1", "T1", conversation_id)
    await repo.update_fields(handoff_id, status="done")
    await SlackAgentService._settle_status(client, "C1", "T1", conversation_id)
    assert client.statuses == [HANDOFF_WORKING_STATUS, ""]


def test_card_shows_compact_rows_and_status_color():
    from server.services.handoff_notifier import card_color

    handoff = _handoff(
        "done",
        pr_url="https://x/12",
        pr_number=12,
    )
    handoff.ci_fix_round = 2
    handoff.title = "Add oldest endpoint"
    handoff.brief = {
        "problem": "Add a GET /orders/oldest endpoint that returns the oldest order as JSON. Keep data access in db.py.",
        "suspected_files": ["app.py", "db.py"],
        "acceptance_check": "CI",
    }
    blocks = build_card_blocks(handoff, {"minutes": 30})
    card = blocks[0]["text"]["text"]
    assert card.splitlines()[0] == "*Handoff*"
    assert [line.split(":*")[0] for line in card.splitlines()[2:]] == [
        "*Repo",
        "*Agent",
        "*Task",
        "*Scope",
        "*Limit",
        "*Status",
    ]
    assert "*Task:* Add a GET /orders/oldest endpoint that returns the oldest order as JSON." in card
    assert "Keep data access" not in card and "Done when" not in json.dumps(blocks)
    assert "CI fix round" not in json.dumps(blocks) and "*Status:* Done" in card
    assert card_color(handoff) == "#16A34A"
    assert card_color(handoff, pending_revision=True) == "#D97706"
    assert build_card_blocks(_handoff("proposed"), {})[0]["text"]["text"].startswith("*Handoff draft*")


def test_card_task_summary_is_one_readable_sentence():
    from server.services.handoff_notifier import _task_summary

    long = "Add " + "very " * 80 + "long task. Second sentence."
    summary = _task_summary({"problem": long}, "Title")
    assert summary.endswith("…") and len(summary) <= 201 and "Second" not in summary
    assert _task_summary({"problem": "Use db.py helpers. Then test."}, "T") == "Use db.py helpers."
    assert _task_summary({}, "Fallback title") == "Fallback title"


async def test_slack_chat_actions_are_credited_to_the_slack_name(test_session, nb_ctx, monkeypatch):
    from server.services.slack_context import SlackContext
    from server.tools import handoffs as tools

    slack_ctx = SlackContext(
        workspace_id=uuid4(), tenant_id=nb_ctx.tenant, team_id="T1", channel_id="C1", thread_ts="T", slack_user_id="U1"
    )

    async def slack_name(_session, _ctx):
        return "Soha Sarwar"

    monkeypatch.setattr(tools, "_slack_display_name", slack_name)
    ctx = SimpleNamespace(context={"user_id": str(nb_ctx.user)})

    monkeypatch.setattr(tools, "get_slack_context", lambda: slack_ctx)
    in_slack = await tools._actor(test_session, ctx)
    assert in_slack.label == "Soha Sarwar" and in_slack.user_id == nb_ctx.user

    monkeypatch.setattr(tools, "get_slack_context", lambda: None)
    in_byaan = await tools._actor(test_session, ctx)
    assert in_byaan.label != "Soha Sarwar" and in_byaan.user_id == nb_ctx.user


async def test_slack_name_lookup_failure_falls_back_to_the_byaan_name(test_session, nb_ctx, monkeypatch):
    from server.models.user import User
    from server.services.slack_context import SlackContext
    from server.tools import handoffs as tools

    slack_ctx = SlackContext(
        workspace_id=uuid4(), tenant_id=nb_ctx.tenant, team_id="T1", channel_id="C1", thread_ts="T", slack_user_id="U1"
    )
    monkeypatch.setattr(tools, "get_slack_context", lambda: slack_ctx)
    user = await test_session.get(User, nb_ctx.user)

    actor = await tools._actor(test_session, SimpleNamespace(context={"user_id": str(nb_ctx.user)}))
    assert actor.user_id == nb_ctx.user
    assert actor.label == (user.full_name or user.email)


@pytest.mark.parametrize("scheduled", [False, True])
async def test_scheduled_proposals_leave_the_card_to_byaan(test_session, nb_ctx, monkeypatch, scheduled):
    import json

    from server.tools import handoffs as tools

    async def session_gen():
        yield test_session

    drafted = (await _draft(test_session, nb_ctx)).handoff

    async def fake_propose(*_args, **_kwargs):
        return handoff_service.ProposalOutcome(handoff=drafted, action="created")

    monkeypatch.setattr(tools, "is_feature_enabled", lambda _flag: True)
    monkeypatch.setattr(tools, "get_async_session", session_gen)
    monkeypatch.setattr(tools.handoff_service, "propose", fake_propose)
    propose = tools.propose_handoff.on_invoke_tool._invoke_tool_impl.__closure__[-1].cell_contents
    context = {"tenant_id": str(nb_ctx.tenant), "user_id": str(nb_ctx.user)}
    if scheduled:
        context |= {"scheduled_run": True, "schedule_id": str(uuid4())}

    result = json.loads(await propose(SimpleNamespace(context=context), "Fix", "Problem", "Check", repo="acme/app"))
    assert ("card" in result) is not scheduled
    if scheduled:
        assert "Byaan posts its card" in result["note"]


async def test_app_handoffs_are_private_and_slack_handoffs_are_shared(test_session, nb_ctx):
    repo = HandoffRepository(test_session)
    handoff_id = (await _draft(test_session, nb_ctx)).handoff.id
    stranger = uuid4()

    async def visible(viewer):
        listed = {h.id for h in await repo.list_for_tenant(nb_ctx.tenant, viewer_id=viewer)}
        return handoff_id in listed and await repo.is_visible(handoff_id, viewer)

    assert await visible(nb_ctx.user)
    assert not await visible(stranger)
    await repo.update_fields(handoff_id, origin="slack")
    assert await visible(stranger)
