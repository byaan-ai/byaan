from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.evaluation import (
    AdvisorChangeSet,
    EvaluationArtifact,
    EvaluationAssessment,
    EvaluationCase,
    EvaluationCaseRun,
    EvaluationRun,
    EvaluationSuite,
    EvaluationSuiteVersion,
    EvaluationTargetSnapshot,
)
from server.models.tenant import Tenant
from server.models.user import User

pytestmark = pytest.mark.asyncio


async def _seed_suite_version(test_session: AsyncSession) -> tuple[Tenant, User, EvaluationSuiteVersion]:
    tenant = (await test_session.execute(select(Tenant))).scalars().first()
    assert tenant is not None
    owner = await test_session.get(User, tenant.owner_id)
    assert owner is not None
    suite = EvaluationSuite(
        tenant_id=tenant.id,
        slug=f"rest-eval-suite-{uuid4()}",
        name="REST Evaluation suite",
        description="Evaluation suite exposed through REST",
        owner_id=owner.id,
        target_kinds_json=["semantic_model"],
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
        manifest_json={"contract_version": "evaluation.suite_version.v1", "suite_id": "rest", "version": 1},
        gate_policy_json={"version": "gate-policy.v1", "security_hard_fail": True, "min_overall_pass_rate": 1.0},
        case_count=2,
        content_hash="sha256:rest-suite",
        created_by=owner.id,
    )
    test_session.add(version)
    await test_session.flush()
    test_session.add_all(
        [
            EvaluationCase(
                tenant_id=tenant.id,
                suite_version_id=version.id,
                case_key="case-one",
                title="Case one",
                target_kinds_json=["semantic_model"],
                operation="answer_question",
                question="What is revenue?",
                expected_contract_json={"policy": {"security_hard_fail": True}},
                provenance_json={"source": "manual"},
                tags_json=["rest"],
                content_hash="sha256:case-one",
                immutable=True,
            ),
            EvaluationCase(
                tenant_id=tenant.id,
                suite_version_id=version.id,
                case_key="case-two",
                title="Case two",
                target_kinds_json=["semantic_model"],
                operation="answer_question",
                question="What is margin?",
                expected_contract_json={"policy": {"security_hard_fail": True}},
                provenance_json={"source": "manual"},
                tags_json=["rest"],
                content_hash="sha256:case-two",
                immutable=True,
            ),
        ]
    )
    await test_session.commit()
    return tenant, owner, version


def _complete_snapshot(tenant_id: str) -> dict:
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
        "principal": {"tenant_id": tenant_id, "actor_type": "agent", "actor_id": "agent-1", "scopes": []},
        "dataset": {"snapshot_id": "dataset-1", "snapshot_hash": "sha256:dataset"},
        "feature_flags": {"evaluation_governance": True},
        "time_fixture": {"now": "2026-08-16T00:00:00Z", "timezone": "UTC"},
    }


def _import_case_payload(case_key: str = "imported-case-one") -> dict:
    return {
        "case_key": case_key,
        "title": "Imported case one",
        "target_kinds": ["semantic_model"],
        "operation": "answer_question",
        "question": "What is governed revenue?",
        "expected_contract": {
            "answer": {"must_include_all": ["revenue"]},
            "policy": {"security_hard_fail": True},
        },
        "provenance": {"source": "import", "principal": {"source": "rest-test"}},
        "tags": ["imported", "release-gate"],
    }


async def _seed_completed_run(
    test_session: AsyncSession,
    *,
    tenant_id,
    suite_version_id,
    gate_decision: str,
) -> EvaluationRun:
    snapshot = EvaluationTargetSnapshot(
        tenant_id=tenant_id,
        target_kind="semantic_model",
        target_ref=f"semantic_model:{gate_decision}",
        contract_version="evaluation.target_snapshot.v1",
        snapshot_json={"target_ref": f"semantic_model:{gate_decision}"},
        pin_digest=f"sha256:{gate_decision}",
        blockers_json=[],
    )
    test_session.add(snapshot)
    await test_session.flush()
    run = EvaluationRun(
        tenant_id=tenant_id,
        suite_version_id=suite_version_id,
        target_snapshot_id=snapshot.id,
        status="passed" if gate_decision == "passed" else "failed",
        actor_type="agent",
        actor_id="agent-1",
        preflight_blockers_json=[],
        summary_json={"gate_decision": gate_decision},
    )
    test_session.add(run)
    await test_session.flush()
    return run


async def _seed_case_result(
    test_session: AsyncSession,
    *,
    tenant_id,
    run_id,
    case_id,
    status: str,
    hard_fail: bool = False,
) -> EvaluationCaseRun:
    case_run = EvaluationCaseRun(
        tenant_id=tenant_id,
        run_id=run_id,
        case_id=case_id,
        status=status,
        attempt=1,
        input_digest=f"sha256:input-{status}",
        output_digest=f"sha256:output-{status}",
        result_json={"answer": status, "token": "super-secret-token"},
        error_json={"sql": "select * from restricted_table"} if status != "passed" else {},
        immutable=True,
    )
    test_session.add(case_run)
    await test_session.flush()
    test_session.add(
        EvaluationAssessment(
            tenant_id=tenant_id,
            case_run_id=case_run.id,
            category="security" if hard_fail else "answer",
            status=status,
            score="0.0" if status != "passed" else "1.0",
            hard_fail=hard_fail,
            details_json={"reason": "contains token=super-secret-token"},
            immutable=True,
        )
    )
    await test_session.flush()
    return case_run


async def test_evaluation_rest_read_surfaces_and_compare(test_client, test_session: AsyncSession) -> None:
    tenant, _owner, suite_version = await _seed_suite_version(test_session)
    suite = await test_session.get(EvaluationSuite, suite_version.suite_id)
    assert suite is not None
    cases = (
        (
            await test_session.execute(
                select(EvaluationCase)
                .where(EvaluationCase.suite_version_id == suite_version.id)
                .order_by(EvaluationCase.case_key)
            )
        )
        .scalars()
        .all()
    )
    baseline_run = await _seed_completed_run(
        test_session,
        tenant_id=tenant.id,
        suite_version_id=suite_version.id,
        gate_decision="passed",
    )
    candidate_run = await _seed_completed_run(
        test_session,
        tenant_id=tenant.id,
        suite_version_id=suite_version.id,
        gate_decision="failed",
    )
    await _seed_case_result(
        test_session, tenant_id=tenant.id, run_id=baseline_run.id, case_id=cases[0].id, status="passed"
    )
    await _seed_case_result(
        test_session,
        tenant_id=tenant.id,
        run_id=candidate_run.id,
        case_id=cases[0].id,
        status="failed",
        hard_fail=True,
    )
    await test_session.commit()

    suites_response = await test_client.get("/api/evaluation/suites?query=REST&target_kind=semantic_model")
    assert suites_response.status_code == 200
    suites_payload = suites_response.json()["data"]
    assert suites_payload["total"] == 1
    assert suites_payload["items"][0]["id"] == str(suite.id)

    suite_response = await test_client.get(f"/api/evaluation/suites/{suite.id}?include_manifests=true")
    assert suite_response.status_code == 200
    assert suite_response.json()["data"]["suite"]["versions"][0]["manifest"]["suite_id"] == "rest"

    run_response = await test_client.get(f"/api/evaluation/runs/{candidate_run.id}")
    assert run_response.status_code == 200
    run_payload = run_response.json()["data"]
    assert run_payload["run"]["id"] == str(candidate_run.id)
    assert run_payload["case_runs"][0]["assessments"][0]["hard_fail"] is True
    assert "super-secret-token" not in json.dumps(run_payload)
    assert "restricted_table" not in json.dumps(run_payload)

    failures_response = await test_client.get(f"/api/evaluation/runs/{candidate_run.id}/failures")
    assert failures_response.status_code == 200
    assert failures_response.json()["data"]["total"] == 1

    compare_response = await test_client.get(
        f"/api/evaluation/runs/compare?baseline_run_id={baseline_run.id}&candidate_run_id={candidate_run.id}"
    )
    assert compare_response.status_code == 200
    comparison = compare_response.json()["data"]["comparison"]
    assert comparison["summary"]["regression_count"] == 1
    assert comparison["regressions"][0]["case_id"] == str(cases[0].id)


async def test_evaluation_rest_create_import_publish_and_run_closed_loop(
    test_client, test_session: AsyncSession
) -> None:
    tenant = (await test_session.execute(select(Tenant))).scalars().first()
    assert tenant is not None

    create_response = await test_client.post(
        "/api/evaluation/suites",
        json={
            "slug": f"commercial-loop-{uuid4().hex[:8]}",
            "name": "Commercial Evaluation Loop",
            "description": "Explicit non-production acceptance fixture",
            "target_kinds": ["semantic_model"],
            "gate_policy": {"security_hard_fail": True, "min_overall_pass_rate": 1.0},
        },
    )
    assert create_response.status_code == 201
    suite = create_response.json()["data"]["suite"]
    draft_version_id = suite["versions"][0]["id"]

    import_response = await test_client.post(
        f"/api/evaluation/suite-versions/{draft_version_id}/cases/import",
        json={"format": "json", "cases": [_import_case_payload("case-one"), _import_case_payload("case-two")]},
    )
    assert import_response.status_code == 201
    assert import_response.json()["data"]["created_count"] == 2

    publish_response = await test_client.post(f"/api/evaluation/suite-versions/{draft_version_id}/publish")
    assert publish_response.status_code == 200
    assert publish_response.json()["data"]["version"]["status"] == "published"

    import_after_publish = await test_client.post(
        f"/api/evaluation/suite-versions/{draft_version_id}/cases",
        json=_import_case_payload("case-after-publish"),
    )
    assert import_after_publish.status_code == 409

    preflight_response = await test_client.post(
        "/api/evaluation/runs/preflight",
        json={
            "suite_version_id": draft_version_id,
            "target_snapshot": _complete_snapshot(str(tenant.id)),
            "idempotency_key": "commercial-loop",
            "actor_type": "agent",
            "actor_id": "agent-release-gate",
        },
    )
    assert preflight_response.status_code == 202
    run_id = preflight_response.json()["data"]["id"]

    claim_response = await test_client.post(
        "/api/evaluation/runs/claim",
        json={"worker_id": "commercial-loop-worker", "lease_seconds": 60},
    )
    assert claim_response.status_code == 200
    assert claim_response.json()["data"]["id"] == run_id

    artifact_response = await test_client.post(
        f"/api/evaluation/runs/{run_id}/artifacts",
        json={"artifact_type": "runner.log", "uri": "memory://runner-log", "content": {"token": "super-secret-token"}},
    )
    assert artifact_response.status_code == 201
    assert "super-secret-token" not in json.dumps(artifact_response.json())

    complete_response = await test_client.post(
        f"/api/evaluation/runs/{run_id}/complete",
        json={
            "worker_id": "commercial-loop-worker",
            "case_results": [
                {
                    "case_key": "case-one",
                    "status": "passed",
                    "assessments": [{"category": "answer", "status": "passed", "score": "1.0", "hard_fail": False}],
                    "result": {"answer": "revenue is governed"},
                },
                {
                    "case_key": "case-two",
                    "status": "failed",
                    "assessments": [{"category": "answer", "status": "failed", "score": "0", "hard_fail": True}],
                    "result": {"answer": "incorrect"},
                    "error": {"token": "super-secret-token", "sql": "select * from private_table"},
                },
            ],
        },
    )
    assert complete_response.status_code == 200
    completed = complete_response.json()["data"]
    assert completed["status"] == "failed"
    assert completed["summary"]["gate_decision"] == "failed"

    saved_artifacts = (
        (await test_session.execute(select(EvaluationArtifact).where(EvaluationArtifact.run_id == run_id)))
        .scalars()
        .all()
    )
    assert len(saved_artifacts) == 1
    assert "private_table" not in json.dumps((await test_client.get(f"/api/evaluation/runs/{run_id}/failures")).json())


async def test_evaluation_advisor_review_gate_and_apply(test_client, test_session: AsyncSession) -> None:
    tenant, _owner, suite_version = await _seed_suite_version(test_session)
    verification_run = await _seed_completed_run(
        test_session,
        tenant_id=tenant.id,
        suite_version_id=suite_version.id,
        gate_decision="passed",
    )
    regression_run = await _seed_completed_run(
        test_session,
        tenant_id=tenant.id,
        suite_version_id=suite_version.id,
        gate_decision="failed",
    )
    change_set = AdvisorChangeSet(
        tenant_id=tenant.id,
        suite_version_id=suite_version.id,
        target_ref="semantic_model:sales",
        base_version_ref="semantic_model:sales:v1",
        base_etag="sha256:base",
        status="draft",
        evidence_json={"token": "super-secret-token", "summary": "Review this staged patch"},
        verification_run_id=verification_run.id,
        regression_run_id=regression_run.id,
        created_by="advisor-1",
    )
    test_session.add(change_set)
    await test_session.commit()
    change_set_id = change_set.id

    review_response = await test_client.get(f"/api/evaluation/advisor-change-sets/{change_set_id}/review")
    assert review_response.status_code == 200
    review = review_response.json()["data"]
    assert review["gate_summary"]["ready_to_apply"] is False
    assert "super-secret-token" not in json.dumps(review)

    verify_response = await test_client.post(
        f"/api/evaluation/advisor-change-sets/{change_set_id}/verification",
        json={"target_snapshot": _complete_snapshot(str(tenant.id)), "idempotency_key": "advisor-rest-verify"},
    )
    assert verify_response.status_code == 202

    test_session.expire_all()
    latest_change_set = await test_session.get(AdvisorChangeSet, change_set_id)
    assert latest_change_set is not None
    queued_gate = await test_session.get(EvaluationRun, latest_change_set.verification_run_id)
    assert queued_gate is not None
    queued_gate.status = "passed"
    queued_gate.summary_json = {"gate_decision": "passed"}
    latest_change_set.regression_run_id = queued_gate.id
    await test_session.commit()

    apply_response = await test_client.post(f"/api/evaluation/advisor-change-sets/{change_set_id}/apply")
    assert apply_response.status_code == 200
    assert apply_response.json()["data"]["promotion"]["decision"] == "accepted"
