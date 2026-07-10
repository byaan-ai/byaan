from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, inspect, select

from server.models.github_repository import GitHubRepository
from server.models.skill_citation import SkillCitation
from server.models.skill_version import SkillVersion
from server.models.tenant import Tenant
from server.models.user import User
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.skill_citation import SkillCitationRepository
from server.services import github_service
from server.utils.config_loader import get_skill_loop_config


@pytest_asyncio.fixture
async def seeded(test_session):
    user = User(id=uuid4(), email=f"{uuid4().hex}@test.com", hashed_password="x", is_active=True, is_verified=True)
    test_session.add(user)
    await test_session.flush()
    tenant = Tenant(id=uuid4(), name="T", slug=f"t-{uuid4().hex}", owner_id=user.id, is_personal=True)
    test_session.add(tenant)
    await test_session.flush()
    repo = GitHubRepository(
        id=uuid4(), tenant_id=tenant.id, user_id=user.id, repo_full_name="acme/widgets", default_branch="main"
    )
    test_session.add(repo)
    await test_session.commit()
    return {"session": test_session, "tenant": tenant, "user": user, "repo": repo}


def test_migration_artifacts_registered():
    table = SkillCitation.__table__
    assert table.name == "skill_citations"
    assert {"repo_id", "path", "snippet", "snippet_hash", "commit_sha", "status"} <= set(table.columns.keys())
    gh_cols = set(GitHubRepository.__table__.columns.keys())
    assert {"tracked_branch", "skill_sync_enabled"} <= gh_cols


def test_effective_branch():
    repo = GitHubRepository(repo_full_name="a/b", default_branch="main")
    assert repo.effective_branch == "main"
    repo.tracked_branch = "develop"
    assert repo.effective_branch == "develop"


@pytest.mark.asyncio
async def test_migration_columns_present_in_db(seeded):
    session = seeded["session"]

    def _columns(sync_conn):
        return {c["name"] for c in inspect(sync_conn).get_columns("skill_citations")}

    conn = await session.connection()
    cols = await conn.run_sync(_columns)
    assert {"skill_id", "repo_id", "path", "snippet"} <= cols


@pytest.mark.asyncio
async def test_bulk_replace_and_list_for_repo_paths(seeded):
    session, repo, tenant, user = seeded["session"], seeded["repo"], seeded["tenant"], seeded["user"]
    skill_repo = CustomSkillRepository(session)
    skill = await skill_repo.create(
        tenant_id=tenant.id, created_by=user.id, name="s", description="d", instructions="i"
    )
    citation_repo = SkillCitationRepository(session)

    def _cite(path: str, claim: str) -> dict:
        return {
            "repo_id": repo.id,
            "path": path,
            "start_line": 1,
            "end_line": 5,
            "commit_sha": "abc123",
            "snippet_hash": "h",
            "snippet": "code",
            "claim_key": claim,
        }

    await citation_repo.bulk_replace_for_skill(skill.id, [_cite("a.py", "c1"), _cite("b.py", "c2")])
    assert len(await citation_repo.list_for_skill(skill.id)) == 2

    await citation_repo.bulk_replace_for_skill(skill.id, [_cite("a.py", "c1")])
    remaining = await citation_repo.list_for_skill(skill.id)
    assert len(remaining) == 1
    assert remaining[0].path == "a.py"

    matched = await citation_repo.list_for_repo_paths(repo.id, ["a.py", "zzz.py"])
    assert [c.path for c in matched] == ["a.py"]
    assert await citation_repo.list_for_repo_paths(repo.id, []) == []

    stats = await citation_repo.stats_for_repo(repo.id)
    assert stats == {"total": 1, "unresolved": 0}


@pytest.mark.asyncio
async def test_upsert_github_skill_versions_on_content_change(seeded):
    session, tenant, user, repo = seeded["session"], seeded["tenant"], seeded["user"], seeded["repo"]
    skill_repo = CustomSkillRepository(session)

    async def _version_count(skill_id) -> int:
        result = await session.execute(
            select(func.count()).select_from(SkillVersion).where(SkillVersion.skill_id == skill_id)
        )
        return result.scalar_one()

    created = await skill_repo.upsert_github_skill(
        tenant_id=tenant.id,
        created_by=user.id,
        github_repo_id=repo.id,
        github_analysis_type="overview",
        name="repo-skill",
        description="v1",
        instructions="v1 instructions",
    )
    assert await _version_count(created.id) == 0

    await skill_repo.upsert_github_skill(
        tenant_id=tenant.id,
        created_by=user.id,
        github_repo_id=repo.id,
        github_analysis_type="overview",
        name="repo-skill",
        description="v1",
        instructions="v1 instructions",
    )
    assert await _version_count(created.id) == 0

    updated = await skill_repo.upsert_github_skill(
        tenant_id=tenant.id,
        created_by=user.id,
        github_repo_id=repo.id,
        github_analysis_type="overview",
        name="repo-skill",
        description="v2",
        instructions="v2 instructions",
    )
    assert updated.id == created.id
    assert updated.instructions == "v2 instructions"
    assert await _version_count(created.id) == 1
    versions = await session.execute(select(SkillVersion).where(SkillVersion.skill_id == created.id))
    snapshot = versions.scalar_one()
    assert snapshot.instructions == "v1 instructions"
    assert snapshot.changed_by == "loop"


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError("unexpected raise_for_status")


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        return self._response


@pytest.mark.asyncio
async def test_compare_commits_parses_payload(monkeypatch):
    big_patch = "x" * 9000
    payload = {
        "total_commits": 3,
        "html_url": "https://github.com/acme/widgets/compare/a...b",
        "files": (
            [{"filename": "kept.py", "status": "modified", "additions": 2, "deletions": 1, "patch": "@@ diff"}]
            + [{"filename": "big.py", "status": "modified", "additions": 1, "deletions": 0, "patch": big_patch}]
            + [{"filename": "bin.dat", "status": "added", "additions": 0, "deletions": 0}]
            + [
                {"filename": f"f{i}.py", "status": "modified", "additions": 1, "deletions": 1, "patch": "p"}
                for i in range(400)
            ]
        ),
    }
    monkeypatch.setattr(github_service.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResponse(200, payload)))

    result = await github_service.compare_commits("tok", "acme/widgets", "base", "head")
    assert result["total_commits"] == 3
    assert result["html_url"].endswith("a...b")
    assert result["truncated"] is True
    assert len(result["files"]) == github_service.COMPARE_MAX_FILES

    kept = result["files"][0]
    assert kept["filename"] == "kept.py"
    big = result["files"][1]
    assert len(big["patch"].encode("utf-8")) <= github_service.COMPARE_MAX_PATCH_BYTES
    binary = result["files"][2]
    assert binary["patch"] is None


@pytest.mark.asyncio
async def test_compare_commits_handles_404(monkeypatch):
    monkeypatch.setattr(github_service.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResponse(404, {})))
    assert await github_service.compare_commits("tok", "acme/widgets", "base", "head") is None


def test_skill_loop_config_defaults(monkeypatch):
    for var in (
        "SKILL_LOOP_CODE_SYNC_ENABLED",
        "SKILL_LOOP_CODE_SESSIONS_PER_DAY",
        "SKILL_LOOP_CODE_MAX_SKILLS_PER_TICK",
    ):
        monkeypatch.delenv(var, raising=False)
    config = get_skill_loop_config()
    assert config["code_sync_enabled"] is True
    assert config["code_sessions_per_day"] == 10
    assert config["code_max_skills_per_tick"] == 3

    monkeypatch.setenv("SKILL_LOOP_CODE_SYNC_ENABLED", "false")
    monkeypatch.setenv("SKILL_LOOP_CODE_SESSIONS_PER_DAY", "42")
    config = get_skill_loop_config()
    assert config["code_sync_enabled"] is False
    assert config["code_sessions_per_day"] == 42
