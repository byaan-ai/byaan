from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.mcp import tool_wrappers
from server.mcp.tool_wrappers import (
    create_advisor_change_set_wrapper,
    create_evaluation_case_draft_wrapper,
    describe_evaluation_failure_wrapper,
    describe_evaluation_suite_wrapper,
    get_evaluation_run_wrapper,
    list_evaluation_cases_wrapper,
    preview_evaluation_ground_truth_wrapper,
    run_advisor_gate_wrapper,
    run_evaluation_wrapper,
    search_evaluation_suites_wrapper,
    submit_evaluation_feedback_wrapper,
)
from server.models.conversation_evaluation import ConversationEvaluation
from server.models.custom_skill import CustomSkill
from server.models.evaluation import (
    EvaluationAssessment,
    EvaluationCase,
    EvaluationCaseRun,
    EvaluationRun,
    EvaluationSuite,
    EvaluationSuiteVersion,
    EvaluationTargetSnapshot,
)
from server.models.notebooks import Notebook
from server.models.skill_suggestion import SkillSuggestion
from server.models.tenant import Tenant
from server.models.user import User

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _patch_mcp_session_factory(test_engine, monkeypatch: pytest.MonkeyPatch):
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(tool_wrappers, "AsyncSessionFactory", test_session_factory)


async def _seed_suite(test_session: AsyncSession) -> tuple[Tenant, User, EvaluationSuite, EvaluationSuiteVersion]:
    tenant = (await test_session.execute(select(Tenant))).scalars().first()
    if tenant is None:
        user = User(
            id=uuid4(),
            email=f"evaluation-mcp-owner-{uuid4()}@example.test",
            hashed_password="fakehash",
            is_active=True,
            is_verified=True,
        )
        test_session.add(user)
        await test_session.flush()
        tenant = Tenant(
            id=uuid4(),
            name="Evaluation MCP Tenant",
            slug=f"evaluation-mcp-{uuid4().hex[:8]}",
            owner_id=user.id,
            is_personal=True,
        )
        test_session.add(tenant)
        await test_session.flush()
    owner = await test_session.get(User, tenant.owner_id)
    assert owner is not None
    suite = EvaluationSuite(
        tenant_id=tenant.id,
        slug=f"mcp-suite-{uuid4()}",
        name="MCP Evaluation suite",
        description="Suite exposed through MCP",
        owner_id=owner.id,
        target_kinds_json=["agent_answer", "semantic_model"],
        lifecycle="draft",
    )
    test_session.add(suite)
    await test_session.flush()
    version = EvaluationSuiteVersion(
        tenant_id=tenant.id,
        suite_id=suite.id,
        version_num=1,
        status="draft",
        contract_version="evaluation.suite_version.v1",
        manifest_json={"contract_version": "evaluation.suite_version.v1", "suite_id": "mcp", "version": 1},
        gate_policy_json={"version": "gate-policy.v1", "security_hard_fail": True, "min_overall_pass_rate": 1.0},
        case_count=0,
        content_hash="sha256:mcp-suite",
        created_by=owner.id,
    )
    test_session.add(version)
    await test_session.commit()
    return tenant, owner, suite, version


def _expected_contract() -> dict:
    return {
        "semantic_intent": {"description": "The answer must exclude refunded rows."},
        "ground_truth_sql": {"sql": "SELECT SUM(revenue) AS revenue FROM fact_sales", "dialect": "duckdb"},
        "answer": {"must_include_any": ["exclude refunds"], "must_not_include": ["password=plain-password"]},
        "evidence": {"required": True},
        "policy": {"security_hard_fail": True},
    }


def _target_snapshot(tenant_id: UUID) -> dict:
    return {
        "contract_version": "evaluation.target_snapshot.v1",
        "target_kind": "agent_answer",
        "target_ref": "agent:revenue-answer",
        "app": {
            "git_sha": "abc123",
            "image_digest": "sha256:image",
            "migration_revision": "add_evaluation_authoritative_model",
        },
        "source": {"snapshot_id": "source-1", "snapshot_hash": "sha256:source"},
        "semantic_model": {"version_id": "semver-1", "version_hash": "sha256:semantic"},
        "prompt": {"version": "prompt-v1", "prompt_hash": "sha256:prompt"},
        "tool_registry_hash": "sha256:tools",
        "skill_registry_hash": "sha256:skills",
        "llm": {"provider": "openai", "model": "gpt", "params_hash": "sha256:params"},
        "principal": {"tenant_id": str(tenant_id), "actor_type": "agent", "actor_id": "mcp-agent", "scopes": []},
        "dataset": {"snapshot_id": "dataset-1", "snapshot_hash": "sha256:dataset"},
        "feature_flags": {"evaluation_governance": True},
        "time_fixture": {"now": "2026-08-16T00:00:00Z", "timezone": "UTC"},
    }


async def test_evaluation_mcp_suite_case_and_run_contract(test_session: AsyncSession) -> None:
    tenant, owner, suite, suite_version = await _seed_suite(test_session)

    search_payload = json.loads(
        await search_evaluation_suites_wrapper("MCP", "agent_answer", "draft", tenant.id, owner.id)
    )
    assert search_payload["success"] is True
    assert search_payload["items"][0]["id"] == str(suite.id)

    describe_payload = json.loads(await describe_evaluation_suite_wrapper(str(suite.id), tenant.id, owner.id, True))
    assert describe_payload["success"] is True
    assert describe_payload["suite"]["versions"][0]["manifest"]["suite_id"] == "mcp"

    ground_truth_payload = json.loads(
        await preview_evaluation_ground_truth_wrapper(json.dumps(_expected_contract()), tenant.id, owner.id)
    )
    assert ground_truth_payload["success"] is True
    assert ground_truth_payload["ground_truth"]["readonly"] is True
    assert "SELECT SUM" not in json.dumps(ground_truth_payload)

    case_payload = json.loads(
        await create_evaluation_case_draft_wrapper(
            str(suite_version.id),
            json.dumps(
                {
                    "case_key": "mcp-case-one",
                    "title": "MCP case one",
                    "target_kinds": ["agent_answer"],
                    "operation": "answer_question",
                    "question": "What revenue should exclude refunds?",
                    "expected_contract": _expected_contract(),
                    "provenance": {"source": "manual", "trace_id": "trace-123", "principal": {"token": "raw-token"}},
                    "tags": ["mcp"],
                }
            ),
            tenant.id,
            owner.id,
        )
    )
    assert case_payload["success"] is True
    assert case_payload["created"] is True
    assert "raw-token" not in json.dumps(case_payload)
    assert "plain-password" not in json.dumps(case_payload)

    list_payload = json.loads(
        await list_evaluation_cases_wrapper(str(suite_version.id), tenant.id, owner.id, include_expected_contract=False)
    )
    assert list_payload["success"] is True
    assert list_payload["items"][0]["has_ground_truth_sql"] is True
    assert "expected_contract" not in list_payload["items"][0]

    run_payload = json.loads(
        await run_evaluation_wrapper(
            str(suite_version.id),
            json.dumps(_target_snapshot(tenant.id)),
            "mcp-run-one",
            tenant.id,
            owner.id,
        )
    )
    assert run_payload["success"] is True
    assert run_payload["run"]["status"] == "queued"

    run_report = json.loads(await get_evaluation_run_wrapper(run_payload["run"]["id"], tenant.id, owner.id))
    assert run_report["success"] is True
    assert run_report["run"]["id"] == run_payload["run"]["id"]


async def test_evaluation_mcp_feedback_advisor_and_failure_surfaces_are_redacted(test_session: AsyncSession) -> None:
    tenant, owner, _suite, suite_version = await _seed_suite(test_session)
    notebook = Notebook(tenant_id=tenant.id, created_by=owner.id, notebook_name="MCP feedback notebook")
    test_session.add(notebook)
    await test_session.flush()
    legacy_feedback = ConversationEvaluation(
        tenant_id=tenant.id,
        notebook_id=notebook.id,
        trigger="manual",
        verdict="mistake",
        findings={
            "summary": "Wrong answer included refunds",
            "correction": "exclude refunds",
            "trace_id": "trace-123",
            "token": "raw-token",
            "sql": "select * from restricted_table",
        },
    )
    test_session.add(legacy_feedback)
    await test_session.flush()
    skill = CustomSkill(
        tenant_id=tenant.id,
        created_by=owner.id,
        name="Revenue skill",
        description="Revenue rules",
        instructions="Old instructions",
        scope="org",
    )
    test_session.add(skill)
    await test_session.flush()
    suggestion = SkillSuggestion(
        tenant_id=tenant.id,
        skill_id=skill.id,
        suggestion_type="edit",
        title="Exclude refunds",
        rationale="Refund rows caused a miss.",
        confidence="high",
        evidence={"token": "raw-token", "summary": "Need safer instructions"},
        patch={"after": "password=plain-password"},
        proposed_instructions="Always exclude refunds. token=raw-token",
        source={"origin": "mcp"},
        status="pending",
    )
    test_session.add(suggestion)
    await test_session.commit()

    feedback_payload = json.loads(
        await submit_evaluation_feedback_wrapper(
            str(suite_version.id),
            json.dumps({"legacy_conversation_evaluation_id": str(legacy_feedback.id), "tags": ["feedback"]}),
            tenant.id,
            owner.id,
        )
    )
    assert feedback_payload["success"] is True
    assert feedback_payload["case"]["provenance"]["source"] == "legacy_conversation_evaluation"
    assert "raw-token" not in json.dumps(feedback_payload)
    assert "restricted_table" not in json.dumps(feedback_payload)

    affected_case_id = feedback_payload["case"]["id"]
    advisor_payload = json.loads(
        await create_advisor_change_set_wrapper(
            str(suggestion.id),
            str(suite_version.id),
            [affected_case_id],
            tenant.id,
            owner.id,
        )
    )
    assert advisor_payload["success"] is True
    assert advisor_payload["change_set"]["status"] == "draft"
    assert advisor_payload["advisor_suggestions"][0]["affected_case_ids"] == [affected_case_id]
    assert "raw-token" not in json.dumps(advisor_payload)
    assert "plain-password" not in json.dumps(advisor_payload)

    verification_payload = json.loads(
        await run_advisor_gate_wrapper(
            advisor_payload["change_set"]["id"],
            json.dumps(_target_snapshot(tenant.id)),
            "verification",
            "mcp-advisor-verification",
            tenant.id,
            owner.id,
        )
    )
    assert verification_payload["success"] is True
    assert verification_payload["change_set"]["verification_run_id"] == verification_payload["run"]["id"]

    snapshot = EvaluationTargetSnapshot(
        tenant_id=tenant.id,
        target_kind="agent_answer",
        target_ref="agent:failed",
        contract_version="evaluation.target_snapshot.v1",
        snapshot_json={"target_ref": "agent:failed"},
        pin_digest="sha256:failed",
        blockers_json=[],
    )
    test_session.add(snapshot)
    await test_session.flush()
    failed_run = EvaluationRun(
        tenant_id=tenant.id,
        suite_version_id=suite_version.id,
        target_snapshot_id=snapshot.id,
        status="failed",
        actor_type="agent",
        actor_id="mcp-agent",
        preflight_blockers_json=[],
        summary_json={"gate_decision": "failed", "token": "raw-token"},
    )
    test_session.add(failed_run)
    await test_session.flush()
    case = await test_session.get(EvaluationCase, UUID(affected_case_id))
    assert case is not None
    case_run = EvaluationCaseRun(
        tenant_id=tenant.id,
        run_id=failed_run.id,
        case_id=case.id,
        status="failed",
        attempt=1,
        input_digest="sha256:input",
        output_digest="sha256:output",
        result_json={"answer": "bad answer"},
        error_json={"sql": "select * from secret_table"},
        immutable=True,
    )
    test_session.add(case_run)
    await test_session.flush()
    test_session.add(
        EvaluationAssessment(
            tenant_id=tenant.id,
            case_run_id=case_run.id,
            category="security",
            status="failed",
            score="0",
            hard_fail=True,
            details_json={"token": "raw-token", "reason": "leaked SQL"},
            immutable=True,
        )
    )
    await test_session.commit()

    failure_payload = json.loads(await describe_evaluation_failure_wrapper(str(failed_run.id), tenant.id, owner.id))
    assert failure_payload["success"] is True
    assert failure_payload["failures"][0]["status"] == "failed"
    serialized = json.dumps(failure_payload)
    assert "raw-token" not in serialized
    assert "secret_table" not in serialized
