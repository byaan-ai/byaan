from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.models.conversation_evaluation import ConversationEvaluation
from server.models.messages import Message
from server.models.notebooks import Notebook
from server.models.skill_suggestion import SkillSuggestion
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember, TenantRole
from server.models.threads import Thread
from server.models.user import User
from server.services.conversation_evaluation_service import ConversationEvaluationService
from server.templates.emails import get_skill_digest_html, get_skill_digest_text

pytestmark = pytest.mark.asyncio


VERIFY_MISTAKE = """I re-ran the numbers and the revenue total was wrong.

```json
{
  "verdict": "mistake",
  "summary": "The assistant reported 100 but the correct total is 250.",
  "evidence": [{"claim": "total is 100", "check": "SELECT SUM(amount)", "result": "250"}],
  "correction": "The correct revenue total is 250."
}
```"""

PROPOSE_SURVIVES = """Proposing a guardrail.

```json
{
  "survives": true,
  "skill_id": null,
  "skill_name": "Revenue rules",
  "suggestion_type": "new_skill",
  "title": "Always filter out refunds in revenue totals",
  "rationale": "Refund rows inflated the total; this survives because it is scoped to revenue queries.",
  "section": "Revenue",
  "before": "",
  "after": "Exclude refunds when summing revenue.",
  "proposed_instructions": "When computing revenue, exclude rows where type = 'refund'.",
  "confidence": "high"
}
```"""

PROPOSE_DIES = """After critique this overfits.

```json
{
  "survives": false,
  "skill_id": null,
  "skill_name": "Revenue rules",
  "suggestion_type": "new_skill",
  "title": "n/a",
  "rationale": "Would overfit to a single case.",
  "section": "Revenue",
  "before": "",
  "after": "",
  "proposed_instructions": "",
  "confidence": "low"
}
```"""

VERIFY_AMBIGUOUS = """It depends on the date range.

```json
{
  "verdict": "ambiguous",
  "summary": "Answer depends on an unspecified date range.",
  "evidence": [],
  "correction": "Which date range should revenue be calculated over?"
}
```"""

VERIFY_CONFIRMED_WITH_PROSE = """Everything checks out.

```json
{"verdict": "confirmed", "summary": "The total of 250 is correct.", "evidence": [], "correction": null}
```

Thanks for reading, that concludes my audit."""


async def _seed_tenant(session) -> Tenant:
    user = User(
        id=uuid4(),
        email="owner@test.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    session.add(user)
    await session.flush()
    tenant = Tenant(id=uuid4(), name="Acme", slug=f"acme-{uuid4().hex[:6]}", owner_id=user.id)
    session.add(tenant)
    await session.flush()
    session.add(TenantMember(user_id=user.id, tenant_id=tenant.id, role=TenantRole.OWNER.value))
    await session.commit()
    return tenant


async def _seed_notebook(session, tenant, messages, last_offset_minutes=40) -> Notebook:
    notebook = Notebook(id=uuid4(), tenant_id=tenant.id, notebook_name="NB")
    session.add(notebook)
    await session.flush()
    thread = Thread(id=notebook.id, notebook_id=notebook.id)
    session.add(thread)
    await session.flush()

    base = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=last_offset_minutes)
    for idx, (role, content) in enumerate(messages):
        session.add(
            Message(
                id=uuid4(),
                thread_id=thread.id,
                role=role,
                content=content,
                created_at=base + timedelta(seconds=idx),
            )
        )
    await session.commit()
    return notebook


def _service() -> ConversationEvaluationService:
    svc = ConversationEvaluationService()
    svc._notify = AsyncMock()
    return svc


async def test_gate_skips_conversation_without_assistant(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "what is revenue?")])
    svc = _service()
    svc._run_agent = AsyncMock()

    verdict = await svc._evaluate_notebook(test_session, notebook, "scheduled")

    assert verdict == "skipped"
    svc._run_agent.assert_not_called()
    rows = (await test_session.execute(select(ConversationEvaluation))).scalars().all()
    assert len(rows) == 1
    assert rows[0].verdict == "skipped"
    assert "assistant" in rows[0].findings["note"]


async def test_verdict_json_parsing_variants():
    from server.prompts.skill_loop_prompts import parse_last_json_block

    assert parse_last_json_block(VERIFY_MISTAKE)["verdict"] == "mistake"
    assert parse_last_json_block(VERIFY_CONFIRMED_WITH_PROSE)["verdict"] == "confirmed"
    assert parse_last_json_block("no json here at all") is None
    assert parse_last_json_block("```json\n{not valid}\n```") is None


def test_build_slack_thread_url():
    from server.services.conversation_evaluation_service import build_slack_thread_url

    assert build_slack_thread_url("C0123", "1720900000.123456") == "https://slack.com/archives/C0123/p1720900000123456"
    assert build_slack_thread_url("C0123", None) == "https://slack.com/archives/C0123"
    assert build_slack_thread_url(None, "1720900000.123456") is None


async def test_malformed_verifier_output_records_ambiguous(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "100")])
    svc = _service()
    svc._run_agent = AsyncMock(return_value="I could not produce structured output.")

    verdict = await svc._evaluate_notebook(test_session, notebook, "scheduled")

    assert verdict == "ambiguous"
    row = (await test_session.execute(select(ConversationEvaluation))).scalars().one()
    assert row.verdict == "ambiguous"
    assert row.findings.get("parse_error") is True


async def test_mistake_creates_suggestion_with_evidence_and_source(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It is 100")])
    svc = _service()
    svc._run_agent = AsyncMock(side_effect=[VERIFY_MISTAKE, PROPOSE_SURVIVES])

    verdict = await svc._evaluate_notebook(test_session, notebook, "scheduled")

    assert verdict == "mistake"
    suggestion = (await test_session.execute(select(SkillSuggestion))).scalars().one()
    assert suggestion.suggestion_type == "new_skill"
    assert suggestion.title.startswith("Always filter out refunds")
    assert suggestion.evidence["summary"].startswith("The assistant reported")
    assert suggestion.evidence["evidence"][0]["result"] == "250"
    assert suggestion.source["origin"] == "app"
    assert suggestion.source["notebook_id"] == str(notebook.id)
    assert suggestion.proposed_instructions
    svc._notify.assert_awaited_once()


async def test_refuter_kills_suggestion(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It is 100")])
    svc = _service()
    svc._run_agent = AsyncMock(side_effect=[VERIFY_MISTAKE, PROPOSE_DIES])

    verdict = await svc._evaluate_notebook(test_session, notebook, "scheduled")

    assert verdict == "mistake"
    suggestions = (await test_session.execute(select(SkillSuggestion))).scalars().all()
    assert suggestions == []
    svc._notify.assert_not_called()


async def test_ambiguous_creates_clarification(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It depends")])
    svc = _service()
    svc._run_agent = AsyncMock(return_value=VERIFY_AMBIGUOUS)

    verdict = await svc._evaluate_notebook(test_session, notebook, "scheduled")

    assert verdict == "ambiguous"
    suggestion = (await test_session.execute(select(SkillSuggestion))).scalars().one()
    assert suggestion.suggestion_type == "clarification"
    assert suggestion.title.startswith("Which date range")
    assert suggestion.patch is None


async def test_confirmed_reevaluation_resolves_pending_clarification(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It depends")])
    svc = _service()
    svc._notify_resolved = AsyncMock()
    svc._run_agent = AsyncMock(return_value=VERIFY_AMBIGUOUS)
    await svc._evaluate_notebook(test_session, notebook, "scheduled")

    svc._run_agent = AsyncMock(return_value=VERIFY_CONFIRMED_WITH_PROSE)
    verdict = await svc._evaluate_notebook(test_session, notebook, "scheduled")

    assert verdict == "confirmed"
    suggestion = (await test_session.execute(select(SkillSuggestion))).scalars().one()
    assert suggestion.status == "superseded"
    assert suggestion.reviewed_via == "skill_loop"
    assert "confirmed the assistant's answer" in suggestion.review_note
    svc._notify_resolved.assert_awaited_once()


async def test_mistake_reevaluation_resolves_clarification_and_links_new_suggestion(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It depends")])
    svc = _service()
    svc._notify_resolved = AsyncMock()
    svc._run_agent = AsyncMock(return_value=VERIFY_AMBIGUOUS)
    await svc._evaluate_notebook(test_session, notebook, "scheduled")

    svc._run_agent = AsyncMock(side_effect=[VERIFY_MISTAKE, PROPOSE_SURVIVES])
    verdict = await svc._evaluate_notebook(test_session, notebook, "scheduled")

    assert verdict == "mistake"
    suggestions = (await test_session.execute(select(SkillSuggestion))).scalars().all()
    by_type = {s.suggestion_type: s for s in suggestions}
    assert by_type["clarification"].status == "superseded"
    assert by_type["new_skill"].title in by_type["clarification"].review_note
    assert by_type["new_skill"].status == "pending"


async def test_new_clarification_supersedes_older_question(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It depends")])
    svc = _service()
    svc._notify_resolved = AsyncMock()
    svc._run_agent = AsyncMock(return_value=VERIFY_AMBIGUOUS)

    await svc._evaluate_notebook(test_session, notebook, "scheduled")
    await svc._evaluate_notebook(test_session, notebook, "scheduled")

    suggestions = (await test_session.execute(select(SkillSuggestion))).scalars().all()
    statuses = sorted(s.status for s in suggestions)
    assert statuses == ["pending", "superseded"]
    superseded = next(s for s in suggestions if s.status == "superseded")
    assert "newer clarification" in superseded.review_note


async def test_resolution_scoped_to_notebook(test_session):
    tenant = await _seed_tenant(test_session)
    notebook_a = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It depends")])
    notebook_b = await _seed_notebook(test_session, tenant, [("user", "users?"), ("assistant", "42")])
    svc = _service()
    svc._notify_resolved = AsyncMock()
    svc._run_agent = AsyncMock(return_value=VERIFY_AMBIGUOUS)
    await svc._evaluate_notebook(test_session, notebook_a, "scheduled")

    svc._run_agent = AsyncMock(return_value=VERIFY_CONFIRMED_WITH_PROSE)
    await svc._evaluate_notebook(test_session, notebook_b, "scheduled")

    suggestion = (await test_session.execute(select(SkillSuggestion))).scalars().one()
    assert suggestion.source["notebook_id"] == str(notebook_a.id)
    assert suggestion.status == "pending"
    svc._notify_resolved.assert_not_called()


async def test_racing_clarifications_do_not_cancel_each_other(test_session):
    from server.services.skill_suggestion_service import SkillSuggestionService

    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It depends")])
    source = {"origin": "app", "notebook_id": str(notebook.id)}
    service = SkillSuggestionService(test_session)
    s1 = await service.create_suggestion(
        tenant_id=tenant.id, suggestion_type="clarification", title="Q1", rationale="", confidence="low", source=source
    )
    s2 = await service.create_suggestion(
        tenant_id=tenant.id, suggestion_type="clarification", title="Q2", rationale="", confidence="low", source=source
    )

    svc = _service()
    svc._notify_resolved = AsyncMock()
    await svc._resolve_open_clarifications(test_session, tenant.id, notebook.id, "ambiguous", s1)
    await svc._resolve_open_clarifications(test_session, tenant.id, notebook.id, "ambiguous", s2)

    await test_session.refresh(s1)
    await test_session.refresh(s2)
    assert s1.status == "superseded"
    assert s2.status == "pending"


async def test_human_review_claim_beats_auto_resolution(test_session):
    from server.repositories.skill_suggestion import SkillSuggestionRepository
    from server.services.skill_suggestion_service import SkillSuggestionService

    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "revenue?"), ("assistant", "It depends")])
    service = SkillSuggestionService(test_session)
    suggestion = await service.create_suggestion(
        tenant_id=tenant.id,
        suggestion_type="clarification",
        title="Q",
        rationale="",
        confidence="low",
        source={"origin": "app", "notebook_id": str(notebook.id)},
    )
    assert await SkillSuggestionRepository(test_session).claim_for_review(suggestion.id, tenant.id)

    svc = _service()
    svc._notify_resolved = AsyncMock()
    await svc._resolve_open_clarifications(test_session, tenant.id, notebook.id, "confirmed", None)

    await test_session.refresh(suggestion)
    assert suggestion.status == "reviewing"
    svc._notify_resolved.assert_not_called()


def test_resolution_reply_only_targets_posted_review_card():
    from server.services.slack_suggestion_service import _points_at_review_card

    origin_source = {"origin": "slack", "slack_channel_id": "C_SRC", "slack_thread_ts": "999.111"}
    card_posted = SkillSuggestion(source=origin_source, slack_channel_id="C_REVIEW", slack_message_ts="1700.0001")
    card_failed = SkillSuggestion(source=origin_source, slack_channel_id="C_SRC", slack_message_ts="999.111")
    legacy_failed = SkillSuggestion(
        source={"origin": "slack", "channel": "C_SRC", "thread_ts": "999.111"},
        slack_channel_id="C_SRC",
        slack_message_ts="999.111",
    )
    never_posted = SkillSuggestion(source=origin_source, slack_channel_id=None, slack_message_ts=None)

    assert _points_at_review_card(card_posted) is True
    assert _points_at_review_card(card_failed) is False
    assert _points_at_review_card(legacy_failed) is False
    assert _points_at_review_card(never_posted) is False


async def test_candidate_dedup_skips_evaluated_notebook(test_session):
    tenant = await _seed_tenant(test_session)
    notebook = await _seed_notebook(test_session, tenant, [("user", "q"), ("assistant", "a")], last_offset_minutes=45)
    svc = _service()

    first = await svc.find_candidate_notebooks(test_session, limit=10)
    assert any(c["notebook_id"] == notebook.id for c in first)

    test_session.add(
        ConversationEvaluation(
            tenant_id=tenant.id,
            notebook_id=notebook.id,
            trigger="scheduled",
            verdict="confirmed",
            evaluated_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await test_session.commit()

    second = await svc.find_candidate_notebooks(test_session, limit=10)
    assert all(c["notebook_id"] != notebook.id for c in second)


async def test_candidate_excludes_fresh_notebook(test_session):
    tenant = await _seed_tenant(test_session)
    fresh = await _seed_notebook(test_session, tenant, [("user", "q"), ("assistant", "a")], last_offset_minutes=2)
    svc = _service()

    candidates = await svc.find_candidate_notebooks(test_session, limit=10)
    assert all(c["notebook_id"] != fresh.id for c in candidates)


async def test_candidate_tenant_filter(test_session):
    tenant_a = await _seed_tenant(test_session)

    other_user = User(
        id=uuid4(),
        email=f"owner-{uuid4().hex[:6]}@test.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(other_user)
    await test_session.flush()
    tenant_b = Tenant(id=uuid4(), name="Beta", slug=f"beta-{uuid4().hex[:6]}", owner_id=other_user.id)
    test_session.add(tenant_b)
    await test_session.commit()

    nb_a = await _seed_notebook(test_session, tenant_a, [("user", "q"), ("assistant", "a")])
    nb_b = await _seed_notebook(test_session, tenant_b, [("user", "q"), ("assistant", "a")])
    svc = _service()

    unscoped = await svc.find_candidate_notebooks(test_session, limit=10)
    assert {c["notebook_id"] for c in unscoped} >= {nb_a.id, nb_b.id}

    scoped = await svc.find_candidate_notebooks(test_session, limit=10, tenant_id=tenant_a.id)
    assert {c["notebook_id"] for c in scoped} == {nb_a.id}


def test_digest_template_contains_stats():
    stats = {"evaluated": 7, "confirmed": 4, "mistake": 2, "questions": 1}
    suggestions = [{"title": "Exclude refunds", "skill_name": "Revenue rules"}]
    html = get_skill_digest_html("Acme", stats, suggestions, "http://localhost:5173")
    assert "Acme" in html
    assert ">7<" in html and ">4<" in html and ">2<" in html and ">1<" in html
    assert "Exclude refunds" in html
    assert "http://localhost:5173/skill-review" in html

    text = get_skill_digest_text("Acme", stats, suggestions, "http://localhost:5173")
    assert "Evaluated: 7" in text
    assert "Exclude refunds — Revenue rules" in text


async def test_digest_sends_only_to_owner_and_admin(test_session):
    tenant = await _seed_tenant(test_session)

    admin = User(id=uuid4(), email="admin@test.com", hashed_password="x", is_active=True, is_verified=True)
    member = User(id=uuid4(), email="member@test.com", hashed_password="x", is_active=True, is_verified=True)
    viewer = User(id=uuid4(), email="viewer@test.com", hashed_password="x", is_active=True, is_verified=True)
    test_session.add_all([admin, member, viewer])
    await test_session.flush()
    test_session.add_all(
        [
            TenantMember(user_id=admin.id, tenant_id=tenant.id, role=TenantRole.ADMIN.value),
            TenantMember(user_id=member.id, tenant_id=tenant.id, role=TenantRole.MEMBER.value),
            TenantMember(user_id=viewer.id, tenant_id=tenant.id, role=TenantRole.VIEWER.value),
        ]
    )
    await test_session.commit()

    fake_email = AsyncMock()
    fake_email.send_skill_digest_email = AsyncMock(return_value={"success": True})

    import server.services.tenant_service as tenant_service

    original = tenant_service._get_email_service
    tenant_service._get_email_service = lambda: fake_email
    try:
        svc = _service()
        sent = await svc.send_tenant_digest(test_session, tenant.id)
    finally:
        tenant_service._get_email_service = original

    assert sent is True
    recipients = {call.kwargs["to_email"] for call in fake_email.send_skill_digest_email.await_args_list}
    assert recipients == {"owner@test.com", "admin@test.com"}
