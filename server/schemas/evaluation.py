from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, Field, model_validator

EvaluationCaseContractVersion = Literal["evaluation.case.v1"]
EvaluationSuiteVersionContractVersion = Literal["evaluation.suite_version.v1"]
EvaluationTargetSnapshotContractVersion = Literal["evaluation.target_snapshot.v1"]

EvaluationTargetKind = Literal["connector", "semantic_model", "agent_answer", "dashboard", "policy", "end_to_end"]
EvaluationOperation = Literal[
    "answer_question",
    "execute_sql",
    "query_dashboard",
    "apply_policy",
    "end_to_end_task",
]
EvaluationActorType = Literal["human", "agent", "service"]
EvaluationResultMode = Literal["ordered_rows", "multiset", "scalar", "invariants_only"]


class EvaluationStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationSemanticIntent(EvaluationStrictModel):
    metric: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    grain: str | None = None
    timezone: str | None = None
    description: str = ""


class EvaluationGroundTruthSQL(EvaluationStrictModel):
    sql: str = Field(min_length=1)
    dialect: str = "duckdb"
    must_reference: list[str] = Field(default_factory=list)
    must_not_reference: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_readonly_sql(self) -> EvaluationGroundTruthSQL:
        try:
            expressions = sqlglot.parse(self.sql, read=self.dialect)
        except Exception as exc:  # pragma: no cover - exact parser errors vary by sqlglot version
            raise ValueError(f"ground truth SQL could not be parsed: {exc}") from exc
        if len(expressions) != 1:
            raise ValueError("ground truth SQL must contain exactly one read-only statement")
        expression = expressions[0]
        if expression is None or expression.key not in {"select", "with", "union"}:
            raise ValueError("ground truth SQL must be read-only")
        return self


class EvaluationExpectedField(EvaluationStrictModel):
    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    unit: str | None = None
    nullable: bool = True
    logical_type: str | None = None


class EvaluationNormalizedResult(EvaluationStrictModel):
    mode: EvaluationResultMode = "multiset"
    rows: list[dict[str, Any]] = Field(default_factory=list)
    invariants: dict[str, Any] = Field(default_factory=dict)
    canonical_hash: str | None = None
    large_result_chunk_hashes: list[str] = Field(default_factory=list)


class EvaluationTolerance(EvaluationStrictModel):
    absolute: float | None = Field(default=None, ge=0)
    relative: float | None = Field(default=None, ge=0)
    per_field: dict[str, dict[str, float]] = Field(default_factory=dict)


class EvaluationAnswerExpectation(EvaluationStrictModel):
    must_include_any: list[str] = Field(default_factory=list)
    must_include_all: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    refusal_allowed: bool = False
    clarification_allowed: bool = False


class EvaluationEvidenceExpectation(EvaluationStrictModel):
    required: bool = True
    lineage_refs: list[str] = Field(default_factory=list)
    min_confidence: float | None = Field(default=None, ge=0, le=1)


class EvaluationPolicyExpectation(EvaluationStrictModel):
    required_scopes: list[str] = Field(default_factory=list)
    forbidden_fields: list[str] = Field(default_factory=list)
    expected_decision: Literal["allow", "deny", "redact"] | None = None
    security_hard_fail: bool = True


class EvaluationDashboardExpectation(EvaluationStrictModel):
    manifest_id: str | None = None
    run_contract_version: Literal["dashboard.run.v1"] = "dashboard.run.v1"
    required_data_view_ids: list[str] = Field(default_factory=list)


class EvaluationHumanMCPParityExpectation(EvaluationStrictModel):
    required: bool = False
    compare_fields: list[str] = Field(default_factory=list)


class EvaluationExpectedContract(EvaluationStrictModel):
    semantic_intent: EvaluationSemanticIntent = Field(default_factory=EvaluationSemanticIntent)
    ground_truth_sql: EvaluationGroundTruthSQL | None = None
    expected_schema: list[EvaluationExpectedField] = Field(default_factory=list)
    normalized_result: EvaluationNormalizedResult = Field(default_factory=EvaluationNormalizedResult)
    tolerance: EvaluationTolerance = Field(default_factory=EvaluationTolerance)
    answer: EvaluationAnswerExpectation = Field(default_factory=EvaluationAnswerExpectation)
    evidence: EvaluationEvidenceExpectation = Field(default_factory=EvaluationEvidenceExpectation)
    policy: EvaluationPolicyExpectation = Field(default_factory=EvaluationPolicyExpectation)
    dashboard: EvaluationDashboardExpectation = Field(default_factory=EvaluationDashboardExpectation)
    human_mcp_parity: EvaluationHumanMCPParityExpectation = Field(
        default_factory=EvaluationHumanMCPParityExpectation
    )


class EvaluationCaseProvenance(EvaluationStrictModel):
    source: Literal["human_feedback", "manual", "legacy_conversation_evaluation", "import", "advisor"]
    feedback_id: str | None = None
    trace_id: str | None = None
    principal: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class EvaluationCaseContract(EvaluationStrictModel):
    contract_version: EvaluationCaseContractVersion
    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    target_kinds: list[EvaluationTargetKind] = Field(min_length=1)
    operation: EvaluationOperation
    question: str = Field(min_length=1)
    expected: EvaluationExpectedContract
    tags: list[str] = Field(default_factory=list)
    provenance: EvaluationCaseProvenance


class EvaluationGatePolicy(EvaluationStrictModel):
    version: str = "gate-policy.v1"
    security_hard_fail: bool = True
    min_overall_pass_rate: float = Field(default=1.0, ge=0, le=1)
    max_new_regressions: int = Field(default=0, ge=0)
    require_manual_review_for: list[str] = Field(default_factory=list)


class EvaluationSuiteVersionManifest(EvaluationStrictModel):
    contract_version: EvaluationSuiteVersionContractVersion
    suite_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    cases: list[EvaluationCaseContract]
    gate_policy: EvaluationGatePolicy
    owner: str | None = None
    description: str = ""


class EvaluationAppPin(EvaluationStrictModel):
    git_sha: str | None = None
    image_digest: str | None = None
    migration_revision: str | None = None


class EvaluationConnectorPin(EvaluationStrictModel):
    version: str | None = None
    connection_id: str | None = None


class EvaluationSourcePin(EvaluationStrictModel):
    snapshot_id: str | None = None
    snapshot_hash: str | None = None


class EvaluationSemanticModelPin(EvaluationStrictModel):
    version_id: str | None = None
    version_hash: str | None = None


class EvaluationDashboardPin(EvaluationStrictModel):
    version_id: str | None = None
    manifest_hash: str | None = None
    renderer_version: str | None = None


class EvaluationPromptPin(EvaluationStrictModel):
    version: str | None = None
    prompt_hash: str | None = None


class EvaluationLLMPin(EvaluationStrictModel):
    provider: str | None = None
    model: str | None = None
    params_hash: str | None = None


class EvaluationPrincipalPin(EvaluationStrictModel):
    tenant_id: str = Field(min_length=1)
    actor_type: EvaluationActorType
    actor_id: str = Field(min_length=1)
    scopes: list[str] = Field(default_factory=list)
    rls: dict[str, Any] = Field(default_factory=dict)
    cls: list[str] = Field(default_factory=list)


class EvaluationDatasetPin(EvaluationStrictModel):
    snapshot_id: str | None = None
    snapshot_hash: str | None = None


class EvaluationTimeFixture(EvaluationStrictModel):
    now: str | None = None
    timezone: str = "UTC"


class EvaluationTargetSnapshot(EvaluationStrictModel):
    contract_version: EvaluationTargetSnapshotContractVersion
    target_kind: EvaluationTargetKind
    target_ref: str = Field(min_length=1)
    app: EvaluationAppPin
    connector: EvaluationConnectorPin = Field(default_factory=EvaluationConnectorPin)
    source: EvaluationSourcePin = Field(default_factory=EvaluationSourcePin)
    semantic_model: EvaluationSemanticModelPin = Field(default_factory=EvaluationSemanticModelPin)
    dashboard: EvaluationDashboardPin = Field(default_factory=EvaluationDashboardPin)
    prompt: EvaluationPromptPin = Field(default_factory=EvaluationPromptPin)
    tool_registry_hash: str | None = None
    skill_registry_hash: str | None = None
    llm: EvaluationLLMPin = Field(default_factory=EvaluationLLMPin)
    principal: EvaluationPrincipalPin
    dataset: EvaluationDatasetPin = Field(default_factory=EvaluationDatasetPin)
    feature_flags: dict[str, Any]
    time_fixture: EvaluationTimeFixture

    def required_pin_blockers(self) -> list[str]:
        blockers: list[str] = []
        required_paths = [
            ("app.git_sha", self.app.git_sha),
            ("app.image_digest", self.app.image_digest),
            ("app.migration_revision", self.app.migration_revision),
            ("source.snapshot_hash", self.source.snapshot_hash),
            ("dataset.snapshot_hash", self.dataset.snapshot_hash),
            ("time_fixture.now", self.time_fixture.now),
        ]
        if self.target_kind in {"semantic_model", "agent_answer", "dashboard", "end_to_end"}:
            required_paths.append(("semantic_model.version_hash", self.semantic_model.version_hash))
        if self.target_kind in {"dashboard", "end_to_end"}:
            required_paths.extend(
                [
                    ("dashboard.version_id", self.dashboard.version_id),
                    ("dashboard.manifest_hash", self.dashboard.manifest_hash),
                    ("dashboard.renderer_version", self.dashboard.renderer_version),
                ]
            )
        if self.target_kind in {"agent_answer", "end_to_end"}:
            required_paths.extend(
                [
                    ("prompt.version", self.prompt.version),
                    ("tool_registry_hash", self.tool_registry_hash),
                    ("skill_registry_hash", self.skill_registry_hash),
                    ("llm.params_hash", self.llm.params_hash),
                ]
            )
        for path, value in required_paths:
            if value in (None, "", [], {}):
                blockers.append(path)
        return blockers
