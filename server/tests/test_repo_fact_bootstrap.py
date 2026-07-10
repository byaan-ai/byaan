from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.models.custom_skill import CustomSkill
from server.models.github_repository import GitHubRepository
from server.models.skill_citation import SkillCitation
from server.models.tenant import Tenant
from server.models.user import User
from server.prompts.repo_fact_prompts import parse_last_json_array
from server.services import repo_analysis_service
from server.services.repo_analysis_service import _normalize_ws, _run_data_truths_pass

pytestmark = pytest.mark.asyncio


ORDER_MODEL = """class Order(Base):
    __tablename__ = "orders"
    status = Column(String)
    STATUSES = ["pending", "paid", "refunded"]
    org_id = Column(GUID)
"""


class _SessionFactoryStub:
    """Yields the test session so the pass writes to the in-memory test DB."""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        return False


def _fence(claims: list[dict]) -> str:
    return f"Here are the facts I found.\n\n```json\n{json.dumps(claims)}\n```\n\nThat is all."


async def _seed(session) -> tuple[Tenant, User, GitHubRepository]:
    user = User(id=uuid4(), email=f"u-{uuid4().hex[:6]}@t.com", hashed_password="x", is_active=True, is_verified=True)
    session.add(user)
    await session.flush()
    tenant = Tenant(id=uuid4(), name="Acme", slug=f"acme-{uuid4().hex[:6]}", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    repo = GitHubRepository(id=uuid4(), tenant_id=tenant.id, user_id=user.id, repo_full_name="acme/shop")
    session.add(repo)
    await session.commit()
    return tenant, user, repo


def _patch_llm(monkeypatch, session, raw: str) -> None:
    monkeypatch.setattr(repo_analysis_service, "AsyncSessionFactory", _SessionFactoryStub(session))
    monkeypatch.setattr(repo_analysis_service.CompletionService, "complete", AsyncMock(return_value=raw), raising=True)


async def _run(tenant, user, repo, session, file_contents, commit_sha="abc123"):
    await _run_data_truths_pass(
        repo_id=repo.id,
        tenant_id=tenant.id,
        user_id=user.id,
        llm_connection_id="conn-1",
        use_claude_sdk=True,
        repo_full_name=repo.repo_full_name,
        data_truths_paths=list(file_contents.keys()),
        file_contents=file_contents,
        commit_sha=commit_sha,
    )


async def test_parse_last_json_array_tolerates_prose_and_brackets():
    raw = _fence([{"family": "enum", "snippet": "x = [1, 2, 3]"}])
    parsed = parse_last_json_array(raw)
    assert isinstance(parsed, list)
    assert parsed[0]["snippet"] == "x = [1, 2, 3]"
    assert parse_last_json_array("no json here") is None
    assert parse_last_json_array("```json\n[not valid]\n```") is None


async def test_happy_path_creates_skill_and_citations(test_session, monkeypatch):
    tenant, user, repo = await _seed(test_session)
    snippet = 'STATUSES = ["pending", "paid", "refunded"]'
    claim = {
        "claim_key": "order-status-enum",
        "family": "enum",
        "claim": "Order status is only ever pending, paid or refunded.",
        "query_rule": "Filter status to these three values; never assume others exist.",
        "path": "server/models/order.py",
        "start_line": 4,
        "end_line": 4,
        "snippet": snippet,
    }
    _patch_llm(monkeypatch, test_session, _fence([claim]))

    await _run(tenant, user, repo, test_session, {"server/models/order.py": ORDER_MODEL})

    skill = (
        await test_session.execute(select(CustomSkill).where(CustomSkill.github_analysis_type == "data_truths"))
    ).scalar_one()
    assert skill.name == "Data Truths & Conventions"
    assert "# Data Truths & Conventions" in skill.instructions
    assert "## Enums & Value Sets" in skill.instructions
    assert "Order status is only ever pending" in skill.instructions
    assert "(cited: server/models/order.py:4-4)" in skill.instructions

    citation = (await test_session.execute(select(SkillCitation))).scalar_one()
    assert citation.repo_id == repo.id
    assert citation.commit_sha == "abc123"
    assert citation.blob_sha is None
    assert citation.claim_key == "order-status-enum"
    assert citation.status == "valid"
    assert citation.start_line == 4 and citation.end_line == 4
    assert citation.snippet_hash == hashlib.sha256(_normalize_ws(snippet).encode("utf-8")).hexdigest()


async def test_hallucinated_snippet_dropped(test_session, monkeypatch):
    tenant, user, repo = await _seed(test_session)
    real = {
        "claim_key": "org-scope",
        "family": "scope",
        "claim": "Every order row is scoped by org_id.",
        "query_rule": "Always filter orders by org_id.",
        "path": "server/models/order.py",
        "start_line": 5,
        "end_line": 5,
        "snippet": "org_id = Column(GUID)",
    }
    fake = {
        "claim_key": "made-up",
        "family": "enum",
        "claim": "There is a secret deleted_at soft-delete column.",
        "query_rule": "Exclude soft-deleted rows.",
        "path": "server/models/order.py",
        "start_line": 9,
        "end_line": 9,
        "snippet": "deleted_at = Column(DateTime)",
    }
    _patch_llm(monkeypatch, test_session, _fence([real, fake]))

    await _run(tenant, user, repo, test_session, {"server/models/order.py": ORDER_MODEL})

    citations = (await test_session.execute(select(SkillCitation))).scalars().all()
    assert len(citations) == 1
    assert citations[0].claim_key == "org-scope"

    skill = (
        await test_session.execute(select(CustomSkill).where(CustomSkill.github_analysis_type == "data_truths"))
    ).scalar_one()
    assert "org_id" in skill.instructions
    assert "secret deleted_at" not in skill.instructions
    assert "soft-delete" not in skill.instructions


async def test_malformed_output_skipped_and_other_skills_untouched(test_session, monkeypatch):
    tenant, user, repo = await _seed(test_session)

    from server.repositories.custom_skill import CustomSkillRepository

    existing = await CustomSkillRepository(test_session).upsert_github_skill(
        tenant_id=tenant.id,
        created_by=user.id,
        github_repo_id=repo.id,
        github_analysis_type="codebase",
        name="Codebase Overview",
        description="d",
        instructions="original codebase instructions",
    )

    _patch_llm(monkeypatch, test_session, "I could not produce any structured output at all.")

    await _run(tenant, user, repo, test_session, {"server/models/order.py": ORDER_MODEL})

    data_truths = (
        (await test_session.execute(select(CustomSkill).where(CustomSkill.github_analysis_type == "data_truths")))
        .scalars()
        .all()
    )
    assert data_truths == []
    assert (await test_session.execute(select(SkillCitation))).scalars().all() == []

    await test_session.refresh(existing)
    assert existing.instructions == "original codebase instructions"


async def test_line_recomputation_ignores_llm_line_numbers(test_session, monkeypatch):
    tenant, user, repo = await _seed(test_session)
    claim = {
        "claim_key": "order-status-enum",
        "family": "enum",
        "claim": "Order status enum.",
        "query_rule": "Filter to known values.",
        "path": "server/models/order.py",
        "start_line": 99,
        "end_line": 100,
        "snippet": 'STATUSES = ["pending", "paid", "refunded"]',
    }
    _patch_llm(monkeypatch, test_session, _fence([claim]))

    await _run(tenant, user, repo, test_session, {"server/models/order.py": ORDER_MODEL})

    citation = (await test_session.execute(select(SkillCitation))).scalar_one()
    assert citation.start_line == 4
    assert citation.end_line == 4

    skill = (
        await test_session.execute(select(CustomSkill).where(CustomSkill.github_analysis_type == "data_truths"))
    ).scalar_one()
    assert "server/models/order.py:4-4" in skill.instructions
    assert "99" not in skill.instructions
