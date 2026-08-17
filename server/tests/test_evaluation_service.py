from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.evaluation import (
    AdvisorChangeSet,
    EvaluationArtifact,
    EvaluationAssessment,
    EvaluationAuditEvent,
    EvaluationCase,
    EvaluationCaseRun,
    EvaluationRun,
    EvaluationSuite,
    EvaluationSuiteVersion,
    PromotionDecision,
)
from server.models.tenant import Tenant
from server.models.user import User
from server.repositories.evaluation import _has_no_preflight_blockers
from server.services.evaluation import EvaluationService

pytestmark = pytest.mark.asyncio


async def _seed_published_suite_version_with_cases(test_session: AsyncSession) -> tuple[str, str]:
    owner = User(
        id=uuid4(),
        email=f"evaluation-runner-owner-{uuid4()}@example.test",
        hashed_password="fakehash",
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(owner)
    await test_session.flush()
    tenant = Tenant(
        id=uuid4(),
        name="Evaluation Runner Tenant",
        slug=f"evaluation-runner-{uuid4().hex[:8]}",
        owner_id=owner.id,
        is_personal=True,
    )
    test_session.add(tenant)
    await test_session.flush()
    suite = EvaluationSuite(
        tenant_id=tenant.id,
        slug=f"eval-runner-suite-{uuid4()}",
        name="Runner suite",
        description="Evaluation runner smoke suite",
        owner_id=owner.id,
        lifecycle="published",
    )
    test_session.add(suite)
    await test_session.flush()
    version = EvaluationSuiteVersion(
        tenant_id=tenant.id,
        suite_id=suite.id,
        version_num=1,
        status="published",
        contract_version="evaluation.suite_version.v1",
        manifest_json={"contract_version": "evaluation.suite_version.v1", "suite_id": "runner", "version": 1},
        gate_policy_json={"version": "gate-policy.v1", "security_hard_fail": True, "min_overall_pass_rate": 1.0},
        case_count=2,
        content_hash="sha256:published",
        created_by=owner.id,
    )
    test_session.add(version)
    await test_session.flush()
    test_session.add_all(
        [
            EvaluationCase(
                tenant_id=tenant.id,
                suite_version_id=version.id,
                case_key="case-pass",
                title="Passing case",
                target_kinds_json=["semantic_model"],
                operation="answer_question",
                question="What is revenue?",
                expected_contract_json={"policy": {"security_hard_fail": True}},
                provenance_json={"source": "manual"},
                tags_json=["runner"],
                content_hash="sha256:case-pass",
                immutable=True,
            ),
            EvaluationCase(
                tenant_id=tenant.id,
                suite_version_id=version.id,
                case_key="case-security-fail",
                title="Security failure case",
                target_kinds_json=["semantic_model"],
                operation="answer_question",
                question="Read restricted text",
                expected_contract_json={"policy": {"security_hard_fail": True}},
                provenance_json={"source": "manual"},
                tags_json=["runner"],
                content_hash="sha256:case-fail",
                immutable=True,
            ),
        ]
    )
    await test_session.commit()
    return str(tenant.id), str(version.id)


def _complete_snapshot() -> dict:
    return {
        "contract_version": "evaluation.target_snapshot.v1",
        "target_kind": "semantic_model",
        "target_ref": "semantic_model:sales",
        "app": {
            "git_sha": "abc123",
            "image_digest": "sha256:image",
            "migration_revision": "add_evaluation_authoritative_model",
        },
        "source": {"snapshot_id": "source-1", "snapshot_hash": "sha256:source"},
        "semantic_model": {"version_id": "semver-1", "version_hash": "sha256:semantic"},
        "principal": {"tenant_id": "tenant-1", "actor_type": "agent", "actor_id": "agent-1", "scopes": []},
        "dataset": {"snapshot_id": "dataset-1", "snapshot_hash": "sha256:dataset"},
        "feature_flags": {"evaluation_governance": True},
        "time_fixture": {"now": "2026-08-16T00:00:00Z", "timezone": "UTC"},
    }


def test_claimable_run_filter_compiles_for_postgres_json_columns() -> None:
    from sqlalchemy.dialects import postgresql

    compiled = str(select(EvaluationRun).where(_has_no_preflight_blockers()).compile(dialect=postgresql.dialect()))

    assert "json_array_length" in compiled
    assert "preflight_blockers_json =" not in compiled


async def test_evaluation_preflight_blocks_missing_required_target_pins(test_session: AsyncSession) -> None:
    tenant_id, suite_version_id = await _seed_published_suite_version_with_cases(test_session)

    run = await EvaluationService(test_session).create_preflight_run(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_snapshot_payload={
            "contract_version": "evaluation.target_snapshot.v1",
            "target_kind": "semantic_model",
            "target_ref": "semantic_model:sales",
            "app": {"git_sha": "abc123"},
            "principal": {"tenant_id": "tenant-1", "actor_type": "human", "actor_id": "user-1", "scopes": []},
            "feature_flags": {},
            "time_fixture": {"timezone": "UTC"},
        },
        actor_id="agent-1",
        idempotency_key="preflight-missing-pins",
    )

    assert run.status == "blocked"
    assert "source.snapshot_hash" in run.preflight_blockers_json
    assert "semantic_model.version_hash" in run.preflight_blockers_json
    audit_actions = (
        await test_session.execute(
            select(EvaluationAuditEvent.action, EvaluationAuditEvent.outcome).where(EvaluationAuditEvent.run_id == run.id)
        )
    ).all()
    assert audit_actions == [("evaluation.run.preflight", "blocked")]


async def test_runner_claims_run_persists_results_and_hard_fail_gate(test_session: AsyncSession) -> None:
    tenant_id, suite_version_id = await _seed_published_suite_version_with_cases(test_session)
    service = EvaluationService(test_session)
    run = await service.create_preflight_run(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_snapshot_payload=_complete_snapshot(),
        actor_id="agent-1",
        idempotency_key="runner-hard-fail",
    )

    claimed = await service.claim_next_run(tenant_id=tenant_id, worker_id="worker-a", lease_seconds=60)
    assert claimed is not None and claimed.id == run.id
    assert await service.claim_next_run(tenant_id=tenant_id, worker_id="worker-b", lease_seconds=60) is None

    completed = await service.complete_run_with_case_results(
        tenant_id=tenant_id,
        run_id=str(run.id),
        worker_id="worker-a",
        case_results=[
            {
                "case_key": "case-pass",
                "status": "passed",
                "assessments": [
                    {"category": "answer", "status": "passed", "score": "1.0", "hard_fail": False},
                ],
                "result": {"answer": "Revenue is 10"},
            },
            {
                "case_key": "case-security-fail",
                "status": "failed",
                "assessments": [
                    {"category": "security", "status": "failed", "score": "0.0", "hard_fail": True},
                ],
                "result": {"answer": "restricted free text count", "token": "raw-token"},
                "error": {"sql": "select * from private_table"},
            },
        ],
    )

    assert completed.status == "failed"
    assert completed.summary_json["hard_failures"] == 1
    assert completed.summary_json["gate_decision"] == "failed"
    case_runs = (
        await test_session.execute(select(EvaluationCaseRun).where(EvaluationCaseRun.run_id == run.id))
    ).scalars().all()
    assert {case_run.status for case_run in case_runs} == {"passed", "failed"}
    assert all(case_run.immutable for case_run in case_runs)
    serialized = str([case_run.result_json | case_run.error_json for case_run in case_runs])
    assert "raw-token" not in serialized
    assert "private_table" not in serialized
    assessments = (
        await test_session.execute(select(EvaluationAssessment).join(EvaluationCaseRun).where(EvaluationCaseRun.run_id == run.id))
    ).scalars().all()
    assert any(assessment.category == "security" and assessment.hard_fail for assessment in assessments)


async def test_preflight_idempotency_reclaim_stop_artifact_and_promotion(test_session: AsyncSession) -> None:
    tenant_id, suite_version_id = await _seed_published_suite_version_with_cases(test_session)
    service = EvaluationService(test_session)

    first = await service.create_preflight_run(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_snapshot_payload=_complete_snapshot(),
        actor_id="agent-1",
        idempotency_key="same-key",
    )
    second = await service.create_preflight_run(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_snapshot_payload=_complete_snapshot(),
        actor_id="agent-1",
        idempotency_key="same-key",
    )
    assert second.id == first.id

    first_claim = await service.claim_next_run(tenant_id=tenant_id, worker_id="worker-a", lease_seconds=-1)
    assert first_claim is not None and first_claim.lease_holder == "worker-a"
    reclaimed = await service.claim_next_run(tenant_id=tenant_id, worker_id="worker-b", lease_seconds=60)
    assert reclaimed is not None and reclaimed.attempt == 2
    await service.request_run_stop(tenant_id=tenant_id, run_id=str(first.id), actor_id="owner")
    stopped = await service.heartbeat_run(tenant_id=tenant_id, run_id=str(first.id), worker_id="worker-b", lease_seconds=60)
    assert stopped.status == "canceled"

    artifact = await service.record_run_artifact(
        tenant_id=tenant_id,
        run_id=str(first.id),
        artifact_type="runner.log",
        uri="memory://runner-log",
        content={"events": ["claimed", "stopped"]},
    )
    assert artifact.immutable is True
    assert (await test_session.get(EvaluationArtifact, artifact.id)).content_hash.startswith("sha256:")

    verification_run = await service.create_preflight_run(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_snapshot_payload=_complete_snapshot(),
        actor_id="agent-1",
        idempotency_key=f"verification-{uuid4()}",
    )
    regression_run = await service.create_preflight_run(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_snapshot_payload=_complete_snapshot(),
        actor_id="agent-1",
        idempotency_key=f"regression-{uuid4()}",
    )
    verification_run.status = "passed"
    verification_run.summary_json = {"gate_decision": "passed"}
    regression_run.status = "failed"
    regression_run.summary_json = {"gate_decision": "failed"}
    change_set = AdvisorChangeSet(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_ref="semantic_model:sales",
        base_version_ref="semantic_model:sales@1",
        base_etag="etag-1",
        status="verified",
        evidence_json={},
        verification_run_id=verification_run.id,
        regression_run_id=regression_run.id,
        created_by="advisor",
    )
    test_session.add(change_set)
    await test_session.commit()

    rejected = await service.decide_promotion(tenant_id=tenant_id, change_set_id=str(change_set.id), actor_id="owner")
    assert rejected.decision == "rejected"
    regression_run.status = "passed"
    regression_run.summary_json = {"gate_decision": "passed"}
    change_set.status = "verified"
    await test_session.commit()
    accepted = await service.decide_promotion(tenant_id=tenant_id, change_set_id=str(change_set.id), actor_id="owner")
    assert accepted.decision == "accepted"
    decisions = (
        await test_session.execute(select(PromotionDecision).where(PromotionDecision.change_set_id == change_set.id))
    ).scalars().all()
    assert {decision.decision for decision in decisions} == {"rejected", "accepted"}
