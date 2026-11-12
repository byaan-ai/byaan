from __future__ import annotations

import asyncio
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from server.config import storage
from server.db.base import Base
from server.models.github_repository import GitHubRepository
from server.models.handoff import Handoff, HandoffEvent
from server.models.messages import Message
from server.models.notebooks import Notebook
from server.models.tenant import Tenant
from server.models.user import User
from server.repositories.handoffs import utcnow
from server.services import handoff_git, handoff_notifier, handoff_runner
from server.services.github_app_service import HandoffGitHubAuth
from server.services.handoff_agent import AgentRunResult

pytestmark = pytest.mark.handoff


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FakeGitHub:
    has_ci_value = True
    checks: list[dict[str, Any]] = []
    pulls: dict[int, dict[str, Any]] = {}
    created: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []

    def __init__(self, token: str):
        self.token = token

    async def can_push(self, repo):
        return True

    async def has_ci(self, repo, sha):
        return self.has_ci_value

    async def find_open_pull(self, repo, head):
        return None

    async def create_pull(self, repo, *, head, base, title, body):
        number = len(FakeGitHub.created) + 1
        FakeGitHub.created.append({"head": head, "base": base, "title": title, "body": body})
        FakeGitHub.pulls[number] = {"number": number, "html_url": f"https://github.test/pr/{number}", "state": "open"}
        return FakeGitHub.pulls[number]

    async def update_pull(self, repo, number, **fields):
        FakeGitHub.updated.append(fields)
        return {}

    async def add_labels(self, repo, number, labels):
        return None

    async def get_pull(self, repo, number):
        pull = dict(FakeGitHub.pulls[number])
        pull["head"] = {"sha": _git(FakeGitHub.remote, "rev-parse", pull.get("branch", "HEAD")).strip()}
        return pull

    async def check_runs(self, repo, sha):
        return FakeGitHub.checks

    async def combined_status(self, repo, sha):
        return {"statuses": []}

    async def job_log_failure(self, repo, job_id, limit=12_000):
        return "AssertionError: expected clinic timezone"

    reviews: list[dict[str, Any]] = []

    async def review_feedback(self, repo, number):
        return FakeGitHub.reviews


class FakeAdapter:
    def __init__(self, edits: dict[str, str | None], submit: bool = True):
        self.edits = edits
        self.submit = submit
        self.prompts: list[str] = []

    async def run(self, spec, hooks):
        self.prompts.append(spec.prompt)
        for rel, content in self.edits.items():
            target = spec.cwd / rel
            if content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        await hooks.report_progress("patched", "Updated the scheduler", 0.6)
        if self.submit:
            await hooks.submit_result(
                "Use clinic timezone",
                "Falls back to the clinic tz.",
                "test_tz added",
                "",
                ["Read app/scheduler.py", "Patched the timezone fallback", "Reviewed the diff"],
            )
        return AgentRunResult(outcome="success", session_id="sess-1", cost_usd=0.5)


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDOFFS_STORAGE_DIR", str(tmp_path / "handoffs"))
    monkeypatch.delenv("HANDOFF_AGENT_GROUP", raising=False)
    storage.handoffs_root.cache_clear()

    hosts = tmp_path / "remote"
    bare = hosts / "acme" / "api.git"
    bare.mkdir(parents=True)
    _git(bare, "init", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "app").mkdir()
    (seed / "app" / "scheduler.py").write_text("def send():\n    return None\n")
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    _git(seed, "push", str(bare), "main")
    monkeypatch.setattr(handoff_git, "GITHUB_HOST", f"file://{hosts}")
    FakeGitHub.remote = bare
    FakeGitHub.pulls, FakeGitHub.created, FakeGitHub.updated, FakeGitHub.checks = {}, [], [], []
    FakeGitHub.has_ci_value = True

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    for module in (handoff_runner, handoff_notifier):
        monkeypatch.setattr(module, "AsyncSessionFactory", factory)

    async def fake_auth(*_args, **_kwargs):
        return HandoffGitHubAuth(token="tok", kind="org")

    async def fake_claude_token():
        return "oauth-token"

    monkeypatch.setattr(handoff_runner, "resolve_auth", fake_auth)
    monkeypatch.setattr(handoff_runner, "HandoffGitHub", FakeGitHub)
    monkeypatch.setattr("server.services.claude_oauth_service.get_active_token_with_refresh", fake_claude_token)
    monkeypatch.setattr("server.services.handoff_agent.resolve_cli", lambda: ("claude", False))

    uid, tid = uuid4(), uuid4()
    async with factory() as session:
        await session.execute(text("PRAGMA foreign_keys=ON"))
        session.add(User(id=uid, email=f"{uid}@t.com", hashed_password="x", is_active=True, is_verified=True))
        await session.flush()
        session.add(Tenant(id=tid, name="T", slug=f"t-{tid}", owner_id=uid, is_personal=True))
        await session.flush()
        repo = GitHubRepository(
            tenant_id=tid,
            user_id=uid,
            repo_full_name="acme/api",
            default_branch="main",
            handoff_enabled=True,
            handoff_config={"paths": {"allow": ["app/**", "tests/**"]}},
        )
        notebook = Notebook(tenant_id=tid, notebook_name="nb")
        session.add_all([repo, notebook])
        await session.commit()
        ids = {"tenant": tid, "user": uid, "repo": repo.id, "notebook": notebook.id}

    yield factory, ids
    await engine.dispose()
    storage.handoffs_root.cache_clear()


async def _queued(factory, ids, title="Fix reminder timezone") -> Any:
    async with factory() as session:
        handoff = Handoff(
            tenant_id=ids["tenant"],
            github_repository_id=ids["repo"],
            repo_full_name="acme/api",
            requested_by=ids["user"],
            origin="notebook",
            notebook_id=ids["notebook"],
            title=title,
            brief={"problem": "tz=None crashes the scheduler", "suspected_files": ["app/scheduler.py:1"]},
            status="queued",
            base_branch="main",
        )
        session.add(handoff)
        await session.commit()
        return handoff.id


async def _drain(runner):
    while runner._runs:
        await asyncio.gather(*list(runner._runs.values()), return_exceptions=True)


async def _check(runner, factory, handoff_id):
    async with factory() as session:
        await runner._check_ci(session, handoff_id)


async def _get(factory, handoff_id) -> Handoff:
    async with factory() as session:
        return (await session.execute(select(Handoff).where(Handoff.id == handoff_id))).scalar_one()


async def _events(factory, handoff_id) -> list[HandoffEvent]:
    async with factory() as session:
        result = await session.execute(
            select(HandoffEvent).where(HandoffEvent.handoff_id == handoff_id).order_by(HandoffEvent.seq)
        )
        return list(result.scalars().all())


async def test_happy_path_opens_pr_and_ci_loop(env):
    factory, ids = env
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    runner.adapter = FakeAdapter({"app/scheduler.py": "def send():\n    return 'clinic'\n"})

    await runner._claim()
    await _drain(runner)

    handoff = await _get(factory, handoff_id)
    assert handoff.status == "ci_running", handoff.error
    assert handoff.pr_number == 1
    assert FakeGitHub.created[0]["title"] == "[Byaan] Fix reminder timezone"
    assert "Falls back to the clinic tz." in FakeGitHub.created[0]["body"]
    assert "## Steps Claude took\n1. Read app/scheduler.py" in FakeGitHub.created[0]["body"]
    assert _git(FakeGitHub.remote, "rev-parse", handoff.branch).strip() == handoff.head_sha
    assert handoff.cost_usd == 0.5
    FakeGitHub.pulls[1]["branch"] = handoff.branch

    FakeGitHub.checks = [
        {"name": "test", "status": "completed", "conclusion": "failure", "id": 5, "app": {"slug": "github-actions"}}
    ]
    runner.adapter = FakeAdapter({"app/scheduler.py": "def send():\n    return 'clinic-fixed'\n"})
    await _check(runner, factory, handoff_id)
    await _drain(runner)
    handoff = await _get(factory, handoff_id)
    assert handoff.status == "ci_running"
    assert handoff.ci_fix_round == 1
    assert "AssertionError" in runner.adapter.prompts[0]

    FakeGitHub.checks = [{"name": "test", "status": "completed", "conclusion": "success"}]
    await _check(runner, factory, handoff_id)
    handoff = await _get(factory, handoff_id)
    assert handoff.status == "done"
    kinds = [e.kind for e in await _events(factory, handoff_id)]
    assert "progress" in kinds and "ci" in kinds

    async with factory() as session:
        messages = (await session.execute(select(Message.content))).scalars().all()
    assert any("PR #1" in m for m in messages)


async def test_ci_gives_up_after_three_rounds(env):
    factory, ids = env
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    runner.adapter = FakeAdapter({"app/scheduler.py": "v1\n"})
    await runner._claim()
    await _drain(runner)
    FakeGitHub.pulls[1]["branch"] = (await _get(factory, handoff_id)).branch
    FakeGitHub.checks = [{"name": "lint", "status": "completed", "conclusion": "failure"}]

    for round_no in range(1, 4):
        runner.adapter = FakeAdapter({"app/scheduler.py": f"v{round_no + 1}\n"})
        await _check(runner, factory, handoff_id)
        await _drain(runner)
    await _check(runner, factory, handoff_id)
    handoff = await _get(factory, handoff_id)
    assert handoff.status == "ci_failed"
    assert handoff.ci_fix_round == 3


async def test_no_ci_marks_untested(env):
    factory, ids = env
    FakeGitHub.has_ci_value = False
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    runner.adapter = FakeAdapter({"app/scheduler.py": "x\n"})
    await runner._claim()
    await _drain(runner)

    handoff = await _get(factory, handoff_id)
    assert handoff.status == "done" and handoff.untested
    assert FakeGitHub.created[0]["title"].startswith("UNTESTED - [Byaan]")


@pytest.mark.parametrize(
    ("edits", "expected"),
    [
        ({}, "no changes"),
        ({".github/workflows/ci.yml": "on: push\n"}, "outside the allowed paths"),
        ({"app/keys.py": 'AWS = "AKIAIOSFODNN7EXAMPLE"\n'}, "Possible secrets"),
    ],
)
async def test_push_guards_fail_without_pushing(env, edits, expected):
    factory, ids = env
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    runner.adapter = FakeAdapter(edits)
    await runner._claim()
    await _drain(runner)

    handoff = await _get(factory, handoff_id)
    assert handoff.status == "failed"
    assert expected in handoff.error
    assert not FakeGitHub.created
    assert handoff.branch not in _git(FakeGitHub.remote, "branch")


async def test_sweeper_fails_stale_runs(env):
    factory, ids = env
    handoff_id = await _queued(factory, ids)
    async with factory() as session:
        await session.execute(
            update(Handoff)
            .where(Handoff.id == handoff_id)
            .values(status="running", worker_id="dead:1", heartbeat_at=utcnow() - timedelta(minutes=5))
        )
        await session.commit()
    await handoff_runner.HandoffRunner()._sweep()
    assert (await _get(factory, handoff_id)).status == "failed"


async def test_one_run_per_repo_and_slot_limit(env, monkeypatch):
    factory, ids = env
    monkeypatch.setenv("HANDOFF_MAX_CONCURRENT", "3")
    first = await _queued(factory, ids, "first")
    second = await _queued(factory, ids, "second")
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    gate = asyncio.Event()

    class SlowAdapter(FakeAdapter):
        async def run(self, spec, hooks):
            await gate.wait()
            return await super().run(spec, hooks)

    runner.adapter = SlowAdapter({"app/scheduler.py": "y\n"})
    await runner._claim()
    await asyncio.sleep(0.2)
    statuses = {(await _get(factory, first)).status, (await _get(factory, second)).status}
    assert "queued" in statuses
    gate.set()
    await _drain(runner)


async def test_review_comments_are_offered_once(env):
    factory, ids = env
    FakeGitHub.has_ci_value = False
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    runner.adapter = FakeAdapter({"app/scheduler.py": "z\n"})
    await runner._claim()
    await _drain(runner)
    FakeGitHub.pulls[1]["branch"] = (await _get(factory, handoff_id)).branch
    FakeGitHub.reviews = [
        {
            "id": "c1",
            "author": "hadi",
            "is_bot": False,
            "body": "Also log the fallback",
            "path": "app/scheduler.py",
            "line": 2,
        },
        {"id": "c2", "author": "ci-bot", "is_bot": True, "body": "coverage 80%"},
    ]

    for _ in range(2):
        async with factory() as session:
            await runner._check_reviews(session, handoff_id)

    reviews = [e for e in await _events(factory, handoff_id) if e.kind == "review"]
    assert len(reviews) == 1
    assert reviews[0].data["new_ids"] == ["c1"]


async def test_followup_on_red_ci_gets_the_failure_log(env):
    factory, ids = env
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    runner.adapter = FakeAdapter({"app/scheduler.py": "first\n"})
    await runner._claim()
    await _drain(runner)
    FakeGitHub.pulls[1]["branch"] = (await _get(factory, handoff_id)).branch
    FakeGitHub.checks = [
        {"name": "test", "status": "completed", "conclusion": "failure", "id": 9, "app": {"slug": "github-actions"}}
    ]

    async with factory() as session:
        from server.repositories.handoffs import HandoffMessageRepository

        await HandoffMessageRepository(session).add(handoff_id, "followup", "Fix the CI failures on this PR.")
        await session.execute(update(Handoff).where(Handoff.id == handoff_id).values(status="queued"))
        await session.commit()

    runner.adapter = FakeAdapter({"app/scheduler.py": "second\n"})
    await runner._claim()
    await _drain(runner)

    prompt = runner.adapter.prompts[0]
    assert "Fix the CI failures on this PR." in prompt
    assert "CI is currently failing on this pull request" in prompt
    assert "AssertionError: expected clinic timezone" in prompt


async def test_restart_after_unpushed_failure_reuses_clone_branch_and_session(env):
    from server.services.handoff_agent import AgentRunResult as Result

    factory, ids = env
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True

    class FailingAdapter(FakeAdapter):
        async def run(self, spec, hooks):
            self.prompts.append(spec.prompt)
            (spec.cwd / "app" / "scheduler.py").write_text("half done\n")
            return Result(outcome="error", session_id="sess-1", error="Claude stopped early")

    runner.adapter = FailingAdapter({})
    await runner._claim()
    await _drain(runner)
    failed = await _get(factory, handoff_id)
    assert failed.status == "failed" and failed.head_sha is None and failed.claude_session_id == "sess-1"
    workspace = handoff_git.Workspace.for_handoff(handoff_id)
    marker = workspace.root / "marker"
    marker.write_text("same clone")

    async with factory() as session:
        await session.execute(update(Handoff).where(Handoff.id == handoff_id).values(status="queued"))
        await session.commit()

    class ResumingAdapter(FakeAdapter):
        async def run(self, spec, hooks):
            self.resume = spec.resume_session_id
            self.partial = (spec.cwd / "app" / "scheduler.py").read_text()
            return await super().run(spec, hooks)

    runner.adapter = ResumingAdapter({"app/scheduler.py": "finished\n"})
    await runner._claim()
    await _drain(runner)

    done = await _get(factory, handoff_id)
    assert done.branch == failed.branch and done.pr_number == 1
    assert runner.adapter.resume == "sess-1"
    assert runner.adapter.partial == "half done\n"
    assert marker.exists()


async def test_rerun_with_no_new_changes_on_an_open_pr_ends_done(env):
    factory, ids = env
    handoff_id = await _queued(factory, ids)
    runner = handoff_runner.HandoffRunner()
    runner._running = True
    runner.adapter = FakeAdapter({"app/scheduler.py": "done once\n"})
    await runner._claim()
    await _drain(runner)
    assert (await _get(factory, handoff_id)).pr_number == 1

    async with factory() as session:
        await session.execute(update(Handoff).where(Handoff.id == handoff_id).values(status="queued"))
        await session.commit()
    runner.adapter = FakeAdapter({})
    await runner._claim()
    await _drain(runner)

    handoff = await _get(factory, handoff_id)
    assert handoff.status == "done" and handoff.error is None
    events = await _events(factory, handoff_id)
    assert any("No new changes were needed; PR #1" in e.message for e in events)


def test_open_pr_prompt_asks_for_a_coverage_check_when_restarted_without_requests():
    runner = handoff_runner.HandoffRunner()
    handoff = Handoff(
        title="Add /orders/count",
        pr_number=8,
        brief={"problem": "Return the order count", "acceptance_check": "GET /orders/count returns a count"},
    )
    plan = handoff_runner.RunPlan(mode="run")

    restart = runner._user_prompt(handoff, plan)
    assert "Apply the requested changes" not in restart
    assert "re-started without new instructions" in restart
    assert "Done when: GET /orders/count returns a count" in restart

    followup = runner._user_prompt(handoff, plan, "- Soha: exclude cancelled orders")
    assert "Apply the requested changes below" in followup
    assert followup.endswith("Requested changes:\n- Soha: exclude cancelled orders")


async def test_a_follow_up_delivered_after_submit_needs_a_fresh_result(env):
    from server.repositories.handoffs import HandoffMessageRepository

    factory, ids = env
    handoff_id = await _queued(factory, ids)
    hooks = handoff_runner._Hooks(handoff_id, Path("/tmp/work"))
    await hooks.submit_result("Add stats", "Counts per status", "", "", [])
    async with factory() as session:
        await HandoffMessageRepository(session).add(handoff_id, "followup", "sort the statuses", author_label="Soha")
        await session.commit()

    [(message_id, text)] = await hooks.pending_followups()
    assert text.startswith("Follow-up from Soha: sort the statuses")
    assert "call submit_result again" in text
    await hooks.mark_delivered([message_id])
    assert hooks.result is None


def test_first_run_prompt_includes_follow_ups_sent_before_claude_started():
    runner = handoff_runner.HandoffRunner()
    handoff = Handoff(title="Add count-by-day", brief={"problem": "Count orders per day"})
    prompt = runner._user_prompt(handoff, handoff_runner.RunPlan(mode="run"), "- Soha: sort the days newest first")
    assert prompt.startswith("Task: Add count-by-day")
    assert prompt.endswith("Requested changes:\n- Soha: sort the days newest first")


def test_branch_slug_is_short_and_keeps_whole_words():
    assert handoff_runner._slug("Add order counts grouped by status endpoint") == "add-order-counts-grouped-status"
    assert handoff_runner._slug("Fix the timezone for reminders") == "fix-timezone-reminders"
    assert handoff_runner._slug("Supercalifragilisticexpialidociousendpointname") == (
        "supercalifragilisticexpialidocio"
    )
    assert handoff_runner._slug("!!!") == "fix"
