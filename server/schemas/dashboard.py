from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DashboardManifestSchemaVersion = Literal["dashboard.manifest.v1"]
DashboardRunContractVersion = Literal["dashboard.run.v1"]

DashboardActorType = Literal["human", "agent", "service"]
DashboardDataViewKind = Literal["semantic_metric", "saved_query", "context_search"]
DashboardTileType = Literal["kpi", "line", "bar", "area", "table", "text", "evidence", "status"]
DashboardRunMode = Literal["live", "pinned_snapshot"]
DashboardSensitivity = Literal["public", "internal", "confidential", "restricted"]
DashboardFreshnessStatus = Literal["fresh", "stale", "unknown", "partial", "blocked"]
DashboardMigrationState = Literal["new_structured", "legacy_unstructured", "candidate", "needs_review", "reviewed"]
DashboardFilterType = Literal["string", "number", "integer", "boolean", "date", "datetime", "enum", "date_range"]
DashboardFilterOperator = Literal["eq", "ne", "gt", "lt", "gte", "lte", "in", "between", "contains", "like"]
DashboardViewStatus = Literal[
    "pending",
    "running",
    "success",
    "empty",
    "partial",
    "stale",
    "permission_denied",
    "error",
    "blocked",
]
DashboardScope = Literal[
    "dashboard:read",
    "dashboard:query",
    "dashboard:create",
    "dashboard:edit",
    "dashboard:publish",
    "dashboard:share",
    "dashboard:export",
]


class DashboardStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DashboardOutputField(DashboardStrictModel):
    name: str = Field(min_length=1)
    data_type: str = Field(min_length=1)
    description: str = ""
    unit: str | None = None
    nullable: bool = True
    sensitivity: DashboardSensitivity = "internal"


class DashboardEvidenceLocator(DashboardStrictModel):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    title: str = Field(min_length=1)
    locator: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0, le=1)


class DashboardLineageRef(DashboardStrictModel):
    id: str = Field(min_length=1)
    kind: Literal["dashboard", "tile", "data_view", "metric", "saved_query", "semantic_model", "source_snapshot"]
    name: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    version: str | None = None


class DashboardFreshnessPolicy(DashboardStrictModel):
    mode: Literal["live", "cache_first", "pinned_snapshot"] = "live"
    max_age_seconds: int = Field(default=3600, ge=0)
    allow_stale: bool = True
    require_as_of: bool = True


class DashboardAccessPolicy(DashboardStrictModel):
    required_scopes: list[DashboardScope] = Field(default_factory=lambda: ["dashboard:read", "dashboard:query"])
    row_policy_refs: list[str] = Field(default_factory=list)
    column_policy_refs: list[str] = Field(default_factory=list)
    redaction_policy_refs: list[str] = Field(default_factory=list)


class DashboardSemanticBinding(DashboardStrictModel):
    id: str = Field(min_length=1)
    model_slug: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    source_snapshot_ids: list[str] = Field(default_factory=list)
    allowed_metrics: list[str] = Field(default_factory=list)
    allowed_dimensions: list[str] = Field(default_factory=list)
    readiness: Literal["published", "blocked"] = "published"

    @model_validator(mode="after")
    def require_published_binding(self) -> DashboardSemanticBinding:
        if self.readiness != "published":
            raise ValueError("dashboard semantic bindings must pin published model versions")
        return self


class DashboardSavedQueryBinding(DashboardStrictModel):
    query_id: str = Field(min_length=1)
    compatibility_reason: str = Field(min_length=1)
    filter_contract: dict[str, Any] = Field(default_factory=dict)
    lineage: list[DashboardLineageRef] = Field(default_factory=list)


class DashboardSemanticMetricBinding(DashboardStrictModel):
    semantic_binding_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    dimensions: list[str] = Field(default_factory=list)
    grain: str | None = None
    sort: list[dict[str, Any]] = Field(default_factory=list)


class DashboardContextSearchBinding(DashboardStrictModel):
    source_binding_id: str = Field(min_length=1)
    query_template: str = Field(min_length=1)
    evidence_required: bool = True


class DashboardDataView(DashboardStrictModel):
    id: str = Field(min_length=1)
    kind: DashboardDataViewKind
    question: str = Field(min_length=1)
    output_schema: list[DashboardOutputField] = Field(default_factory=list)
    filter_fields: list[str] = Field(default_factory=list)
    sensitivity: DashboardSensitivity = "internal"
    row_limit: int = Field(default=500, ge=1, le=5000)
    byte_limit: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    freshness_policy: DashboardFreshnessPolicy = Field(default_factory=DashboardFreshnessPolicy)
    evidence: list[DashboardEvidenceLocator] = Field(default_factory=list)
    lineage: list[DashboardLineageRef] = Field(default_factory=list)
    semantic_metric: DashboardSemanticMetricBinding | None = None
    saved_query: DashboardSavedQueryBinding | None = None
    context_search: DashboardContextSearchBinding | None = None

    @model_validator(mode="after")
    def require_kind_binding(self) -> DashboardDataView:
        bindings = {
            "semantic_metric": self.semantic_metric,
            "saved_query": self.saved_query,
            "context_search": self.context_search,
        }
        if bindings[self.kind] is None:
            raise ValueError(f"{self.kind} data views require a matching binding payload")
        unexpected = [kind for kind, value in bindings.items() if kind != self.kind and value is not None]
        if unexpected:
            raise ValueError(f"{self.kind} data views cannot include {', '.join(unexpected)} bindings")
        return self


class DashboardFilter(DashboardStrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    source: Literal["semantic_field", "saved_query_contract"]
    field: str = Field(min_length=1)
    filter_type: DashboardFilterType
    operators: list[DashboardFilterOperator] = Field(min_length=1)
    affected_data_view_ids: list[str] = Field(default_factory=list)
    default_value: Any = None
    required: bool = False
    domain: list[Any] | None = None
    timezone: str | None = None


class DashboardTileAccessibleFallback(DashboardStrictModel):
    summary: str = ""
    table_fields: list[str] = Field(default_factory=list)


class DashboardTile(DashboardStrictModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    tile_type: DashboardTileType
    business_question: str = Field(min_length=1)
    data_view_id: str | None = None
    encoding: dict[str, Any] = Field(default_factory=dict)
    formatting: dict[str, Any] = Field(default_factory=dict)
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    accessible_fallback: DashboardTileAccessibleFallback = Field(default_factory=DashboardTileAccessibleFallback)

    @model_validator(mode="after")
    def require_data_view_for_data_tiles(self) -> DashboardTile:
        if self.tile_type != "text" and not self.data_view_id:
            raise ValueError(f"{self.tile_type} tiles require data_view_id")
        return self


class DashboardLayoutSection(DashboardStrictModel):
    id: str = Field(min_length=1)
    title: str = ""
    tile_ids: list[str] = Field(default_factory=list)


class DashboardLayout(DashboardStrictModel):
    sections: list[DashboardLayoutSection] = Field(default_factory=list)


class DashboardAction(DashboardStrictModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    action_type: Literal["drill", "export", "share", "reload", "publish"]
    required_scope: DashboardScope
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class DashboardProvenance(DashboardStrictModel):
    created_by_actor_type: DashboardActorType
    created_by: str = Field(min_length=1)
    source: Literal["human", "agent", "migration", "import"]
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DashboardMigration(DashboardStrictModel):
    state: DashboardMigrationState = "new_structured"
    legacy_dashboard_id: str | None = None
    legacy_notebook_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class DashboardManifest(DashboardStrictModel):
    schema_version: DashboardManifestSchemaVersion
    dashboard_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    audience: list[str]
    semantic_bindings: list[DashboardSemanticBinding]
    data_views: list[DashboardDataView]
    filters: list[DashboardFilter]
    layout: DashboardLayout
    tiles: list[DashboardTile]
    actions: list[DashboardAction]
    freshness_policy: DashboardFreshnessPolicy
    access_policy: DashboardAccessPolicy
    provenance: DashboardProvenance
    migration: DashboardMigration

    @model_validator(mode="after")
    def validate_manifest_references(self) -> DashboardManifest:
        data_view_ids = _unique_ids("data_views", [data_view.id for data_view in self.data_views])
        tile_ids = _unique_ids("tiles", [tile.id for tile in self.tiles])
        binding_ids = _unique_ids("semantic_bindings", [binding.id for binding in self.semantic_bindings])
        _unique_ids("filters", [dashboard_filter.id for dashboard_filter in self.filters])

        for data_view in self.data_views:
            if data_view.semantic_metric and data_view.semantic_metric.semantic_binding_id not in binding_ids:
                raise ValueError(f"data view {data_view.id} references unknown semantic binding")

        for dashboard_filter in self.filters:
            unknown_views = set(dashboard_filter.affected_data_view_ids) - data_view_ids
            if unknown_views:
                raise ValueError(f"filter {dashboard_filter.id} references unknown data views")

        for tile in self.tiles:
            if tile.data_view_id and tile.data_view_id not in data_view_ids:
                raise ValueError(f"tile {tile.id} references unknown data view")

        for section in self.layout.sections:
            unknown_tiles = set(section.tile_ids) - tile_ids
            if unknown_tiles:
                raise ValueError(f"layout section {section.id} references unknown tiles")

        return self


class DashboardRunError(DashboardStrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    policy_reason: str | None = None


class DashboardPagination(DashboardStrictModel):
    cursor: str | None = None
    has_more: bool = False
    limit: int = Field(default=500, ge=1, le=5000)


class DashboardRunViewResult(DashboardStrictModel):
    data_view_id: str = Field(min_length=1)
    status: DashboardViewStatus
    result: list[dict[str, Any]] | dict[str, Any] | None = None
    schema_: list[DashboardOutputField] = Field(default_factory=list, alias="schema")
    row_count: int = Field(default=0, ge=0)
    cached: bool = False
    stale: bool = False
    as_of: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    error: DashboardRunError | None = None
    evidence: list[DashboardEvidenceLocator] = Field(default_factory=list)
    lineage: list[DashboardLineageRef] = Field(default_factory=list)
    pagination: DashboardPagination = Field(default_factory=DashboardPagination)
    result_artifact_id: str | None = None


class DashboardRun(DashboardStrictModel):
    contract_version: DashboardRunContractVersion
    run_id: str = Field(min_length=1)
    dashboard_id: str = Field(min_length=1)
    dashboard_version_id: str = Field(min_length=1)
    actor_type: DashboardActorType
    actor_id: str = Field(min_length=1)
    correlation_id: str | None = None
    session_id: str | None = None
    idempotency_key: str | None = None
    mode: DashboardRunMode = "live"
    normalized_filters: dict[str, Any] = Field(default_factory=dict)
    filter_digest: str = Field(min_length=1)
    pinned_versions: dict[str, Any] = Field(default_factory=dict)
    execution_plan_digest: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime | None = None
    overall_freshness: DashboardFreshnessStatus = "unknown"
    views: list[DashboardRunViewResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[DashboardRunError] = Field(default_factory=list)
    pagination: DashboardPagination = Field(default_factory=DashboardPagination)

    @model_validator(mode="after")
    def require_snapshot_artifacts(self) -> DashboardRun:
        if self.mode != "pinned_snapshot":
            return self
        missing_artifacts = [
            view.data_view_id
            for view in self.views
            if view.status not in {"blocked", "permission_denied", "error"} and not view.result_artifact_id
        ]
        if missing_artifacts:
            raise ValueError("pinned_snapshot runs require immutable result_artifact_id for successful views")
        return self


def _unique_ids(collection_name: str, ids: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            duplicates.add(item_id)
        seen.add(item_id)
    if duplicates:
        raise ValueError(f"{collection_name} contains duplicate ids: {', '.join(sorted(duplicates))}")
    return seen
