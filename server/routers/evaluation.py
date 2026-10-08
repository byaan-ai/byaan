from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.auth.dependencies import AuthContext, require_scope
from server.auth.scopes import Scope
from server.db.session import get_async_session
from server.schemas.standard_response import success_response
from server.serializers.evaluation import (
    advisor_change_set_payload,
    advisor_suggestion_payload,
    evaluation_artifact_payload,
    evaluation_case_payload,
    evaluation_case_run_payload,
    evaluation_run_payload,
    evaluation_suite_payload,
    evaluation_suite_version_payload,
    promotion_payload,
)
from server.services.evaluation import EvaluationService

router = APIRouter()


class EvaluationRouterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationPreflightRunRequest(EvaluationRouterModel):
    suite_version_id: UUID
    target_snapshot: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    actor_type: Literal["human", "agent", "service"] = "agent"
    actor_id: str | None = Field(default=None, min_length=1, max_length=160)


class EvaluationCreateSuiteRequest(EvaluationRouterModel):
    slug: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    target_kinds: list[str] = Field(min_length=1)
    gate_policy: dict[str, Any] = Field(default_factory=dict)


class EvaluationCreateDraftVersionRequest(EvaluationRouterModel):
    copy_from_version_id: UUID | None = None


class EvaluationCaseDraftRequest(EvaluationRouterModel):
    case_key: str = Field(min_length=1, max_length=160)
    title: str | None = Field(default=None, max_length=255)
    target_kinds: list[str] = Field(min_length=1)
    operation: str = Field(default="answer_question", min_length=1, max_length=80)
    question: str = Field(min_length=1)
    expected_contract: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=lambda: {"source": "import"})
    tags: list[str] = Field(default_factory=list)


class EvaluationCaseImportRequest(EvaluationRouterModel):
    format: Literal["json", "jsonl", "csv"] = "json"
    cases: list[EvaluationCaseDraftRequest] | None = None
    content: str | None = None


class EvaluationRunLeaseRequest(EvaluationRouterModel):
    worker_id: str = Field(min_length=1, max_length=160)
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class EvaluationArtifactRequest(EvaluationRouterModel):
    artifact_type: str = Field(min_length=1, max_length=80)
    uri: str = Field(min_length=1)
    content: Any


class EvaluationCompleteRunRequest(EvaluationRouterModel):
    worker_id: str = Field(min_length=1, max_length=160)
    case_results: list[dict[str, Any]] = Field(min_length=1)


class EvaluationFeedbackCaseDraftRequest(EvaluationRouterModel):
    suite_version_id: UUID
    question: str | None = Field(default=None, min_length=1)
    tags: list[str] = Field(default_factory=list)


class EvaluationSkillSuggestionAdvisorRequest(EvaluationRouterModel):
    suite_version_id: UUID | None = None
    affected_case_ids: list[UUID] = Field(default_factory=list)


class EvaluationAdvisorGateRequest(EvaluationRouterModel):
    target_snapshot: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


def _service_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    if "not found" in detail:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if "leased by this worker" in detail or "case results do not match" in detail or "immutable" in detail:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _advisor_review_payload(review: dict[str, Any]) -> dict[str, Any]:
    verification_run = review.get("verification_run")
    regression_run = review.get("regression_run")
    return {
        "change_set": advisor_change_set_payload(review["change_set"]),
        "advisor_suggestions": [advisor_suggestion_payload(suggestion) for suggestion in review["advisor_suggestions"]],
        "verification_run": evaluation_run_payload(verification_run) if verification_run else None,
        "regression_run": evaluation_run_payload(regression_run) if regression_run else None,
        "promotion_decisions": [promotion_payload(promotion) for promotion in review["promotion_decisions"]],
        "gate_summary": review["gate_summary"],
    }


def _case_request_payload(payload: EvaluationCaseDraftRequest) -> dict[str, Any]:
    return {
        "case_key": payload.case_key,
        "title": payload.title or payload.case_key,
        "target_kinds": payload.target_kinds,
        "operation": payload.operation,
        "question": payload.question,
        "expected_contract": payload.expected_contract,
        "provenance": payload.provenance,
        "tags": payload.tags,
    }


def _parse_import_cases(payload: EvaluationCaseImportRequest) -> list[dict[str, Any]]:
    if payload.format == "json":
        return [_case_request_payload(case) for case in payload.cases or []]
    if not payload.content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Import content is required")
    if payload.format == "jsonl":
        cases = []
        for line_number, line in enumerate(payload.content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid JSONL at line {line_number}: {exc.msg}",
                ) from exc
        return cases
    reader = csv.DictReader(io.StringIO(payload.content))
    cases = []
    for row in reader:
        expected_text = row.get("expected_contract_json") or row.get("expected_contract") or "{}"
        provenance_text = row.get("provenance_json") or row.get("provenance") or '{"source":"import"}'
        try:
            expected_contract = json.loads(expected_text)
            provenance = json.loads(provenance_text)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid CSV JSON field: {exc.msg}"
            ) from exc
        cases.append(
            {
                "case_key": row.get("case_key") or "",
                "title": row.get("title") or row.get("case_key") or "",
                "target_kinds": [item.strip() for item in (row.get("target_kinds") or "").split(";") if item.strip()],
                "operation": row.get("operation") or "answer_question",
                "question": row.get("question") or "",
                "expected_contract": expected_contract,
                "provenance": provenance,
                "tags": [item.strip() for item in (row.get("tags") or "").split(";") if item.strip()],
            }
        )
    return cases


@router.get("/evaluation/suites")
async def list_evaluation_suites(
    query: str = "",
    target_kind: str = "",
    status_filter: str = Query(default="", alias="status"),
    limit: int = 50,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    suites = await EvaluationService(session).list_suites(
        tenant_id=auth.tenant_id,
        query=query,
        target_kind=target_kind,
        status=status_filter,
        limit=max(1, min(limit, 100)),
    )
    return success_response(
        data={"items": [evaluation_suite_payload(suite) for suite in suites], "total": len(suites)},
        message="Evaluation suites listed",
    )


@router.post("/evaluation/suites", status_code=status.HTTP_201_CREATED)
async def create_evaluation_suite(
    payload: EvaluationCreateSuiteRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        suite, version = await EvaluationService(session).create_suite_with_draft(
            tenant_id=auth.tenant_id,
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            target_kinds=payload.target_kinds,
            gate_policy=payload.gate_policy,
            actor_id=auth.user_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={
            "suite": evaluation_suite_payload(
                suite,
                versions=[version],
                include_versions=True,
                include_manifests=True,
            )
        },
        message="Evaluation suite draft created",
    )


@router.get("/evaluation/suites/{suite_id}")
async def describe_evaluation_suite(
    suite_id: UUID,
    include_manifests: bool = False,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        suite, versions = await EvaluationService(session).describe_suite(
            tenant_id=auth.tenant_id,
            suite_id=suite_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={
            "suite": evaluation_suite_payload(
                suite,
                versions=versions,
                include_versions=True,
                include_manifests=include_manifests,
            )
        },
        message="Evaluation suite described",
    )


@router.post("/evaluation/suites/{suite_id}/draft-version", status_code=status.HTTP_201_CREATED)
async def create_evaluation_draft_suite_version(
    suite_id: UUID,
    payload: EvaluationCreateDraftVersionRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        version = await EvaluationService(session).create_draft_suite_version(
            tenant_id=auth.tenant_id,
            suite_id=suite_id,
            actor_id=auth.user_id,
            copy_from_version_id=payload.copy_from_version_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={"version": evaluation_suite_version_payload(version, include_manifest=True)},
        message="Evaluation draft version created",
    )


@router.get("/evaluation/suite-versions/{suite_version_id}/cases")
async def list_evaluation_cases(
    suite_version_id: UUID,
    include_expected_contract: bool = True,
    limit: int = 100,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        cases = await EvaluationService(session).list_cases(
            tenant_id=auth.tenant_id,
            suite_version_id=suite_version_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    limit = max(1, min(limit, 500))
    return success_response(
        data={
            "items": [
                evaluation_case_payload(case, include_expected_contract=include_expected_contract)
                for case in cases[:limit]
            ],
            "total": len(cases),
            "has_more": len(cases) > limit,
        },
        message="Evaluation cases listed",
    )


@router.post("/evaluation/suite-versions/{suite_version_id}/cases", status_code=status.HTTP_201_CREATED)
async def create_evaluation_case_draft(
    suite_version_id: UUID,
    payload: EvaluationCaseDraftRequest,
    response: Response,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        case, created = await EvaluationService(session).create_case_draft(
            tenant_id=auth.tenant_id,
            suite_version_id=suite_version_id,
            case_key=payload.case_key,
            title=payload.title or payload.case_key,
            target_kinds=payload.target_kinds,
            operation=payload.operation,
            question=payload.question,
            expected_contract=payload.expected_contract,
            provenance=payload.provenance,
            tags=payload.tags,
            actor_id=str(auth.user_id),
            actor_type="human",
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return success_response(
        data={"case": evaluation_case_payload(case), "created": created},
        message="Evaluation case draft created",
    )


@router.post("/evaluation/suite-versions/{suite_version_id}/cases/import", status_code=status.HTTP_201_CREATED)
async def import_evaluation_case_drafts(
    suite_version_id: UUID,
    payload: EvaluationCaseImportRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        imported = await EvaluationService(session).import_case_drafts(
            tenant_id=auth.tenant_id,
            suite_version_id=suite_version_id,
            cases=_parse_import_cases(payload),
            actor_id=str(auth.user_id),
            actor_type="human",
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    created = [evaluation_case_payload(case) for case in imported["created"]]
    existing = [evaluation_case_payload(case) for case in imported["existing"]]
    return success_response(
        data={
            "created": created,
            "existing": existing,
            "created_count": len(created),
            "existing_count": len(existing),
            "total": imported["total"],
        },
        message="Evaluation cases imported",
    )


@router.post("/evaluation/suite-versions/{suite_version_id}/publish")
async def publish_evaluation_suite_version(
    suite_version_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        version = await EvaluationService(session).publish_suite_version(
            tenant_id=auth.tenant_id,
            suite_version_id=suite_version_id,
            actor_id=str(auth.user_id),
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={"version": evaluation_suite_version_payload(version, include_manifest=True)},
        message="Evaluation suite version published",
    )


@router.get("/evaluation/suite-versions/{suite_version_id}/runs")
async def list_evaluation_runs(
    suite_version_id: UUID,
    limit: int = 50,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        runs = await EvaluationService(session).list_runs(
            tenant_id=auth.tenant_id,
            suite_version_id=suite_version_id,
            limit=max(1, min(limit, 100)),
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={"items": [evaluation_run_payload(run) for run in runs], "total": len(runs)},
        message="Evaluation runs listed",
    )


@router.get("/evaluation/suite-versions/{suite_version_id}/advisor-change-sets")
async def list_evaluation_advisor_change_sets(
    suite_version_id: UUID,
    limit: int = 50,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        change_sets = await EvaluationService(session).list_advisor_change_sets(
            tenant_id=auth.tenant_id,
            suite_version_id=suite_version_id,
            limit=max(1, min(limit, 100)),
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={
            "items": [advisor_change_set_payload(change_set) for change_set in change_sets],
            "total": len(change_sets),
        },
        message="Advisor change sets listed",
    )


@router.get("/evaluation/runs/compare")
async def compare_evaluation_runs(
    baseline_run_id: UUID,
    candidate_run_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        comparison = await EvaluationService(session).compare_runs(
            tenant_id=auth.tenant_id,
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data={"comparison": comparison}, message="Evaluation runs compared")


@router.get("/evaluation/runs/{run_id}")
async def get_evaluation_run(
    run_id: UUID,
    include_case_results: bool = True,
    limit: int = 100,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        run, case_runs = await EvaluationService(session).get_run_report(
            tenant_id=auth.tenant_id,
            run_id=run_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    limit = max(1, min(limit, 500))
    data: dict[str, Any] = {"run": evaluation_run_payload(run)}
    if include_case_results:
        data["case_runs"] = [
            evaluation_case_run_payload(case_run, assessments=assessments)
            for case_run, assessments in case_runs[:limit]
        ]
        data["total_case_runs"] = len(case_runs)
        data["has_more_case_runs"] = len(case_runs) > limit
    return success_response(data=data, message="Evaluation run described")


@router.get("/evaluation/runs/{run_id}/failures")
async def describe_evaluation_failures(
    run_id: UUID,
    limit: int = 100,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        run, case_runs = await EvaluationService(session).get_run_report(
            tenant_id=auth.tenant_id,
            run_id=run_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    failures = []
    for case_run, assessments in case_runs:
        failed_assessments = [
            assessment for assessment in assessments if assessment.status != "passed" or assessment.hard_fail
        ]
        if case_run.status != "passed" or failed_assessments:
            failures.append(evaluation_case_run_payload(case_run, assessments=failed_assessments))
    limit = max(1, min(limit, 500))
    return success_response(
        data={
            "run": evaluation_run_payload(run),
            "failures": failures[:limit],
            "total": len(failures),
            "has_more": len(failures) > limit,
        },
        message="Evaluation failures described",
    )


@router.post("/evaluation/runs/preflight", status_code=status.HTTP_202_ACCEPTED)
async def create_evaluation_preflight_run(
    payload: EvaluationPreflightRunRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        run = await EvaluationService(session).create_preflight_run(
            tenant_id=auth.tenant_id,
            suite_version_id=payload.suite_version_id,
            target_snapshot_payload=payload.target_snapshot,
            actor_id=payload.actor_id or str(auth.user_id),
            idempotency_key=payload.idempotency_key,
            actor_type=payload.actor_type,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=evaluation_run_payload(run), message="Evaluation preflight run created")


@router.post("/evaluation/runs/claim")
async def claim_evaluation_run(
    payload: EvaluationRunLeaseRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    run = await EvaluationService(session).claim_next_run(
        tenant_id=auth.tenant_id,
        worker_id=payload.worker_id,
        lease_seconds=payload.lease_seconds,
    )
    return success_response(data=evaluation_run_payload(run) if run else None, message="Evaluation run claim checked")


@router.post("/evaluation/runs/{run_id}/heartbeat")
async def heartbeat_evaluation_run(
    run_id: UUID,
    payload: EvaluationRunLeaseRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        run = await EvaluationService(session).heartbeat_run(
            tenant_id=auth.tenant_id,
            run_id=run_id,
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=evaluation_run_payload(run), message="Evaluation run heartbeat recorded")


@router.post("/evaluation/runs/{run_id}/stop")
async def request_evaluation_run_stop(
    run_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        run = await EvaluationService(session).request_run_stop(
            tenant_id=auth.tenant_id,
            run_id=run_id,
            actor_id=str(auth.user_id),
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=evaluation_run_payload(run), message="Evaluation run stop requested")


@router.post("/evaluation/runs/{run_id}/artifacts", status_code=status.HTTP_201_CREATED)
async def record_evaluation_run_artifact(
    run_id: UUID,
    payload: EvaluationArtifactRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        artifact = await EvaluationService(session).record_run_artifact(
            tenant_id=auth.tenant_id,
            run_id=run_id,
            artifact_type=payload.artifact_type,
            uri=payload.uri,
            content=payload.content,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=evaluation_artifact_payload(artifact), message="Evaluation run artifact recorded")


@router.post("/evaluation/runs/{run_id}/complete")
async def complete_evaluation_run(
    run_id: UUID,
    payload: EvaluationCompleteRunRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        run = await EvaluationService(session).complete_run_with_case_results(
            tenant_id=auth.tenant_id,
            run_id=run_id,
            worker_id=payload.worker_id,
            case_results=payload.case_results,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=evaluation_run_payload(run), message="Evaluation run completed")


@router.post("/evaluation/advisor-change-sets/{change_set_id}/promotion-decision")
async def decide_evaluation_promotion(
    change_set_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        promotion = await EvaluationService(session).decide_promotion(
            tenant_id=auth.tenant_id,
            change_set_id=change_set_id,
            actor_id=str(auth.user_id),
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=promotion_payload(promotion), message="Evaluation promotion decision recorded")


@router.get("/evaluation/advisor-change-sets/{change_set_id}/review")
async def review_evaluation_advisor_change_set(
    change_set_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_READ)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        review = await EvaluationService(session).get_advisor_review(
            tenant_id=auth.tenant_id,
            change_set_id=change_set_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(data=_advisor_review_payload(review), message="Advisor change set reviewed")


@router.post("/evaluation/advisor-change-sets/{change_set_id}/verification", status_code=status.HTTP_202_ACCEPTED)
async def run_evaluation_advisor_verification(
    change_set_id: UUID,
    payload: EvaluationAdvisorGateRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        change_set, run = await EvaluationService(session).create_advisor_gate_run(
            tenant_id=auth.tenant_id,
            change_set_id=change_set_id,
            gate_kind="verification",
            target_snapshot_payload=payload.target_snapshot,
            actor_id=str(auth.user_id),
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={"change_set": advisor_change_set_payload(change_set), "run": evaluation_run_payload(run)},
        message="Advisor verification run queued",
    )


@router.post("/evaluation/advisor-change-sets/{change_set_id}/regression", status_code=status.HTTP_202_ACCEPTED)
async def run_evaluation_advisor_regression(
    change_set_id: UUID,
    payload: EvaluationAdvisorGateRequest,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_EXPORT)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        change_set, run = await EvaluationService(session).create_advisor_gate_run(
            tenant_id=auth.tenant_id,
            change_set_id=change_set_id,
            gate_kind="regression",
            target_snapshot_payload=payload.target_snapshot,
            actor_id=str(auth.user_id),
            idempotency_key=payload.idempotency_key,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={"change_set": advisor_change_set_payload(change_set), "run": evaluation_run_payload(run)},
        message="Advisor regression run queued",
    )


@router.post("/evaluation/advisor-change-sets/{change_set_id}/apply")
async def apply_evaluation_advisor_change_set(
    change_set_id: UUID,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        promotion = await EvaluationService(session).decide_promotion(
            tenant_id=auth.tenant_id,
            change_set_id=change_set_id,
            actor_id=str(auth.user_id),
        )
        review = await EvaluationService(session).get_advisor_review(
            tenant_id=auth.tenant_id,
            change_set_id=change_set_id,
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    return success_response(
        data={"promotion": promotion_payload(promotion), "review": _advisor_review_payload(review)},
        message="Advisor apply decision recorded",
    )


@router.post("/evaluation/feedback/conversation-evaluations/{evaluation_id}/case-draft")
async def create_case_draft_from_conversation_evaluation(
    evaluation_id: UUID,
    payload: EvaluationFeedbackCaseDraftRequest,
    response: Response,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        case, created = await EvaluationService(session).promote_conversation_evaluation_to_case_draft(
            tenant_id=auth.tenant_id,
            evaluation_id=evaluation_id,
            suite_version_id=payload.suite_version_id,
            question=payload.question,
            tags=payload.tags,
            actor_id=str(auth.user_id),
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return success_response(
        data={"case": evaluation_case_payload(case), "created": created},
        message="Evaluation case draft created from feedback",
    )


@router.post("/evaluation/skill-suggestions/{suggestion_id}/advisor-change-set")
async def create_advisor_change_set_from_skill_suggestion(
    suggestion_id: UUID,
    payload: EvaluationSkillSuggestionAdvisorRequest,
    response: Response,
    auth: AuthContext = Depends(require_scope(Scope.DASHBOARD_SHARE)),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        change_set, suggestions, created = await EvaluationService(
            session
        ).create_advisor_change_set_from_skill_suggestion(
            tenant_id=auth.tenant_id,
            suggestion_id=suggestion_id,
            suite_version_id=payload.suite_version_id,
            affected_case_ids=payload.affected_case_ids,
            actor_id=str(auth.user_id),
        )
    except ValueError as exc:
        raise _service_error(exc) from exc
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return success_response(
        data={
            "change_set": advisor_change_set_payload(change_set),
            "advisor_suggestions": [advisor_suggestion_payload(suggestion) for suggestion in suggestions],
            "created": created,
        },
        message="Advisor change set created from skill suggestion",
    )
