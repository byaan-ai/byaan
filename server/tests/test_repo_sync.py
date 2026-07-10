from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.models.custom_skill import CustomSkill
from server.models.github_repository import GitHubRepository
from server.models.skill_citation import SkillCitation
from server.models.skill_loop_settings import SkillLoopSettings
from server.models.skill_suggestion import SkillSuggestion
from server.models.tenant import Tenant
from server.models.user import User
from server.services import github_service
from server.services.repo_sync_service import RepoSyncService

pytestmark = pytest.mark.asyncio


REVERIFY_EDIT = """The cited enum changed.

```json
{
  "still_valid": false,
  "summary": "The status enum gained an 'archived' value.",
  "edit": {
    "section": "Order statuses",
    "before": "active|inactive",
    "after": "active|inactive|archived",
    "proposed_instructions": "Order status may be active, inactive, or archived."
  },
  "confidence": "high"
}
```"""

REVERIFY_STILL_VALID = """No material change.

```json
{"still_valid": true, "summary": "Cited claim still holds.", "edit": null, "confidence": "medium"}
```"""

NEWFACT_WORTH = """This is a new fact.

```json
{
  "worth_new_fact": true,
  "title": "Orders can be archived",
  "rationale": "A new 'archived' status was added to the orders model.",
  "proposed_instructions": "Treat archived orders as inactive when summing active orders.",
  "confidence": "high"
}
```"""


async def _seed_tenant(session) -> Tenant:
    user = User(id=uuid4(), email=f"u-{uuid4().hex[:6]}@t.com", hashed_password="x", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(id=uuid4(), name="Acme", slug=f"acme-{uuid4().hex[:6]}", owner_id=user.id)
    session.add(tenant)
    await session.commit()
    return tenant


async def _seed_repo(session, tenant, *, last_sha="base", sync_enabled=True, status="completed") -> GitHubRepository:
    repo = GitHubRepository(
        id=uuid4(),
        tenant_id=tenant.id,
        user_id=tenant.owner_id,
        source="github",
        repo_full_name="acme/app",
        default_branch="main",
        skill_sync_enabled=sync_enabled,
        last_analyzed_sha=last_sha,
        analysis_status=status,
        is_active=True,
    )
    session.add(repo)
    await session.commit()
    return repo


async def _seed_skill(session, tenant, name="Orders skill") -> CustomSkill:
    skill = CustomSkill(
        id=uuid4(),
        tenant_id=tenant.id,
        created_by=tenant.owner_id,
        name=name,
        description="desc",
        instructions="## Order statuses\nactive|inactive",
    )
    session.add(skill)
    await session.commit()
    return skill


async def _seed_citation(session, repo, skill, path, snippet, start=5, end=5) -> SkillCitation:
    citation = SkillCitation(
        id=uuid4(),
        skill_id=skill.id,
        repo_id=repo.id,
        path=path,
        start_line=start,
        end_line=end,
        commit_sha="base",
        snippet_hash="h",
        snippet=snippet,
        status="valid",
    )
    session.add(citation)
    await session.commit()
    return citation


def _service() -> RepoSyncService:
    svc = RepoSyncService()
    svc._resolve_token = AsyncMock(return_value="tok")
    svc._notify = AsyncMock()
    svc._run_agent = AsyncMock()
    return svc


def _file(filename, status="modified", patch="@@ -1 +1 @@\n-a\n+b"):
    return {"filename": filename, "status": status, "patch": patch, "additions": 1, "deletions": 1}


def _compare(files, html_url="https://gh/compare"):
    return {"files": files, "total_commits": 1, "html_url": html_url, "truncated": False}


async def _suggestions(session) -> list[SkillSuggestion]:
    return list((await session.execute(select(SkillSuggestion))).scalars().all())


async def test_skip_when_same_sha(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant, last_sha="head")
    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    compare_mock = AsyncMock()
    monkeypatch.setattr(github_service, "compare_commits", compare_mock)

    svc = _service()
    await svc.tick(test_session)

    compare_mock.assert_not_awaited()
    assert await _suggestions(test_session) == []
    await test_session.refresh(repo)
    assert repo.last_analyzed_sha == "head"


async def test_cursor_advances_on_no_diff(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant, last_sha="base")
    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    monkeypatch.setattr(github_service, "compare_commits", AsyncMock(return_value=None))

    svc = _service()
    await svc.tick(test_session)

    await test_session.refresh(repo)
    assert repo.last_analyzed_sha == "head"
    assert await _suggestions(test_session) == []


async def test_vendored_paths_filtered(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant)
    skill = await _seed_skill(test_session, tenant)
    citation = await _seed_citation(test_session, repo, skill, "node_modules/a.js", "some code")

    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    monkeypatch.setattr(
        github_service, "compare_commits", AsyncMock(return_value=_compare([_file("node_modules/a.js")]))
    )
    content_mock = AsyncMock(return_value="unrelated")
    monkeypatch.setattr(github_service, "get_file_content", content_mock)

    svc = _service()
    await svc.tick(test_session)

    content_mock.assert_not_awaited()
    await test_session.refresh(citation)
    assert citation.status == "valid"
    await test_session.refresh(repo)
    assert repo.last_analyzed_sha == "head"


def _citation(snippet, start=5, end=5) -> SkillCitation:
    return SkillCitation(
        skill_id=uuid4(),
        repo_id=uuid4(),
        path="server/orders.py",
        start_line=start,
        end_line=end,
        commit_sha="base",
        snippet_hash="h",
        snippet=snippet,
        status="valid",
    )


async def test_reresolve_same_place_keeps_valid():
    svc = RepoSyncService()
    citation = _citation("def foo():\n    return 1", start=1, end=2)
    content = "def foo():\n    return 1\n"
    assert svc._reresolve(citation, content) == "valid"
    assert citation.start_line == 1 and citation.end_line == 2


async def test_reresolve_moved_updates_lines():
    svc = RepoSyncService()
    citation = _citation("def foo():\n    return 1", start=1, end=2)
    content = "# new header\ndef foo():\n    return 1\n"
    assert svc._reresolve(citation, content) == "valid"
    assert citation.start_line == 2 and citation.end_line == 3


async def test_reresolve_missing_and_removed_are_unresolved():
    svc = RepoSyncService()
    citation = _citation("def foo():\n    return 1", start=1, end=2)
    assert svc._reresolve(citation, "totally different content") == "unresolved"
    assert svc._reresolve(_citation("x"), None) == "unresolved"


async def test_reverify_creates_edit_suggestion(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant)
    skill = await _seed_skill(test_session, tenant)
    citation = await _seed_citation(test_session, repo, skill, "server/orders.py", "STATUS = active|inactive")

    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    monkeypatch.setattr(
        github_service, "compare_commits", AsyncMock(return_value=_compare([_file("server/orders.py")]))
    )
    monkeypatch.setattr(github_service, "get_file_content", AsyncMock(return_value="STATUS = something else"))

    svc = _service()
    svc._run_agent = AsyncMock(return_value=REVERIFY_EDIT)
    await svc.tick(test_session)

    await test_session.refresh(citation)
    assert citation.status == "unresolved"

    suggestions = await _suggestions(test_session)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.suggestion_type == "edit"
    assert s.skill_id == skill.id
    assert s.source["origin"] == "codebase"
    assert s.source["head_sha"] == "head"
    assert s.source["repo_full_name"] == "acme/app"
    assert s.patch["section"] == "Order statuses"
    assert s.patch["after"] == "active|inactive|archived"
    assert s.proposed_instructions
    svc._notify.assert_awaited_once()

    await test_session.refresh(repo)
    assert repo.last_analyzed_sha == "head"


async def test_still_valid_no_suggestion_but_cursor_advances(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant)
    skill = await _seed_skill(test_session, tenant)
    await _seed_citation(test_session, repo, skill, "server/orders.py", "STATUS = active|inactive")

    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    monkeypatch.setattr(
        github_service, "compare_commits", AsyncMock(return_value=_compare([_file("server/orders.py")]))
    )
    monkeypatch.setattr(github_service, "get_file_content", AsyncMock(return_value="STATUS = something else"))

    svc = _service()
    svc._run_agent = AsyncMock(return_value=REVERIFY_STILL_VALID)
    await svc.tick(test_session)

    assert await _suggestions(test_session) == []
    svc._notify.assert_not_awaited()
    await test_session.refresh(repo)
    assert repo.last_analyzed_sha == "head"


async def test_per_tick_skill_cap_respected(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant)
    files = []
    for i in range(4):
        skill = await _seed_skill(test_session, tenant, name=f"skill-{i}")
        await _seed_citation(test_session, repo, skill, f"server/f{i}.py", f"SNIPPET_{i}")
        files.append(_file(f"server/f{i}.py"))

    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    monkeypatch.setattr(github_service, "compare_commits", AsyncMock(return_value=_compare(files)))
    monkeypatch.setattr(github_service, "get_file_content", AsyncMock(return_value="nothing matches"))
    monkeypatch.setattr(
        "server.services.repo_sync_service.get_skill_loop_config",
        lambda: {"code_sync_enabled": True, "code_sessions_per_day": 10, "code_max_skills_per_tick": 3},
    )

    svc = _service()
    svc._run_agent = AsyncMock(return_value=REVERIFY_STILL_VALID)
    await svc.tick(test_session)

    assert svc._run_agent.await_count == 3


async def test_per_day_session_budget_respected(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant)
    files = []
    for i in range(2):
        skill = await _seed_skill(test_session, tenant, name=f"skill-{i}")
        await _seed_citation(test_session, repo, skill, f"server/f{i}.py", f"SNIPPET_{i}")
        files.append(_file(f"server/f{i}.py"))

    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    monkeypatch.setattr(github_service, "compare_commits", AsyncMock(return_value=_compare(files)))
    monkeypatch.setattr(github_service, "get_file_content", AsyncMock(return_value="nothing matches"))
    monkeypatch.setattr(
        "server.services.repo_sync_service.get_skill_loop_config",
        lambda: {"code_sync_enabled": True, "code_sessions_per_day": 1, "code_max_skills_per_tick": 3},
    )

    svc = _service()
    svc._run_agent = AsyncMock(return_value=REVERIFY_STILL_VALID)
    await svc.tick(test_session)
    assert svc._run_agent.await_count == 1

    # Second tick same day: budget already spent, no further agent sessions.
    repo.last_analyzed_sha = "base"
    await test_session.commit()
    await svc.tick(test_session)
    assert svc._run_agent.await_count == 1


async def test_new_fact_probe_creates_new_skill(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    await _seed_repo(test_session, tenant)

    monkeypatch.setattr(github_service, "get_latest_commit_sha", AsyncMock(return_value="head"))
    monkeypatch.setattr(
        github_service, "compare_commits", AsyncMock(return_value=_compare([_file("server/models/order.py")]))
    )
    monkeypatch.setattr(github_service, "get_file_content", AsyncMock(return_value="content"))

    svc = _service()
    svc._run_agent = AsyncMock(return_value=NEWFACT_WORTH)
    await svc.tick(test_session)

    suggestions = await _suggestions(test_session)
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s.suggestion_type == "new_skill"
    assert s.skill_id is None
    assert s.source["origin"] == "codebase"
    assert s.proposed_instructions
    svc._notify.assert_awaited_once()


async def test_tenant_with_settings_disabled_skipped(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    repo = await _seed_repo(test_session, tenant)
    test_session.add(SkillLoopSettings(tenant_id=tenant.id, enabled=False, digest_enabled=True, digest_hour=17))
    await test_session.commit()

    sha_mock = AsyncMock(return_value="head")
    monkeypatch.setattr(github_service, "get_latest_commit_sha", sha_mock)

    svc = _service()
    await svc.tick(test_session)

    sha_mock.assert_not_awaited()
    await test_session.refresh(repo)
    assert repo.last_analyzed_sha == "base"


async def test_repo_with_sync_disabled_skipped(test_session, monkeypatch):
    tenant = await _seed_tenant(test_session)
    await _seed_repo(test_session, tenant, sync_enabled=False)

    sha_mock = AsyncMock(return_value="head")
    monkeypatch.setattr(github_service, "get_latest_commit_sha", sha_mock)

    svc = _service()
    await svc.tick(test_session)

    sha_mock.assert_not_awaited()
    assert await _suggestions(test_session) == []
