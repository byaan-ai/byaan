from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ExecuteQueryRequest(BaseModel):
    query: str
    connection_id: str | UUID
    notebook_id: str | UUID
    db_type: str
    name: str


class ExecuteQueryResponse(BaseModel):
    success: bool
    message: str | None = None
    data: list[dict[str, Any]] | dict[str, Any] | None = None
    error: str | None = None
    generated_schema: dict[str, Any] | list[dict[str, Any]] | None = None
    query_id: str | UUID | None = None


class QueryListItem(BaseModel):
    id: UUID
    name: str
    query_type: str = "sql"  # "sql", "mongo", "duckdb", "skill_api"
    skill_name: str | None = None  # Only for skill_api queries


class QueryListResponse(BaseModel):
    queries: list[QueryListItem]


class ApiConfigSchema(BaseModel):
    url: str
    method: str = "GET"
    body: str | None = None
    is_graphql: bool = False
    graphql_query: str | None = None
    graphql_variables: dict | None = None


class QueryRead(BaseModel):
    id: UUID
    name: str
    query: str
    output_schema: str
    dataset_id: UUID | None = None
    notebook_id: UUID
    query_type: str = "sql"
    skill_name: str | None = None
    skill_scope: str | None = None
    api_config: ApiConfigSchema | None = None
    created_at: str
    updated_at: str


class ExecuteSavedQueryResponse(BaseModel):
    success: bool
    message: str | None = None
    data: list[dict[str, Any]] | dict[str, Any] | None = None
    error: str | None = None
    query_name: str | None = None
    query_id: str | UUID | None = None
    cached: bool = False
    stale: bool = False


class DeleteQueryResponse(BaseModel):
    success: bool
    message: str | None = None


class DeleteAllQueriesResponse(BaseModel):
    success: bool
    message: str | None = None
    deleted_count: int | None = None


class UpdateQueryRequest(BaseModel):
    name: str | None = None
    query: str | None = None


class UpdateQueryResponse(BaseModel):
    success: bool
    message: str | None = None
    query_id: str | UUID | None = None


class SavedQueryResult(BaseModel):
    query_id: str | UUID
    query_name: str
    success: bool
    result: list[dict[str, Any]] | dict[str, Any] | None = None
    error: str | None = None
    execution_time_ms: float


class QueryFilter(BaseModel):
    field: str = Field(..., description="Field name to filter on")
    operator: str = Field(..., description="Filter operator: eq, ne, gt, lt, gte, lte, like, in, between, contains")
    value: Any = Field(..., description="Filter value(s)")
    ui_type: str = Field(default="input", description="UI component type: input, select, date, range, multiselect")
    ui_label: str | None = Field(None, description="Display label for the filter")
    ui_options: list[dict[str, Any]] | None = Field(None, description="Options for select/multiselect UI types")


class QueryWithFilters(BaseModel):
    query_id: str | UUID
    filters: list[QueryFilter] = Field(default_factory=list, description="Filters to apply to this specific query")
    filter_values: dict[str, Any] | None = Field(
        None,
        description="Optional UI/filter-intent map keyed by filter id (or id suffixes like _start/_end). Range filters also accept [lower, upper] lists or start/end and min/max bounds objects.",
    )


class BatchExecuteSavedQueriesRequest(BaseModel):
    query_ids: list[str | UUID] | None = Field(None, description="Legacy: List of query IDs without filters")
    queries_with_filters: list[QueryWithFilters] | None = Field(
        None, description="List of queries with their associated filters"
    )
    max_parallel: int = 5


class BatchExecuteSavedQueriesResponse(BaseModel):
    success: bool
    message: str
    data: list[SavedQueryResult]  # Unified with single query response
    # Additional metadata for batch operations
    partial_success: bool | None = None
    total_queries: int | None = None
    successful_queries: int | None = None
    failed_queries: int | None = None
    total_execution_time_ms: float | None = None


class FilterPreflightCompiledFilter(BaseModel):
    field: str
    operator: str
    value: Any
    ui_type: str | None = None
    ui_label: str | None = None


class FilterPreflightResult(BaseModel):
    query_id: str | UUID
    query_name: str = "Unknown"
    success: bool
    compiled_filters: list[FilterPreflightCompiledFilter] = Field(default_factory=list)
    available_filter_ids: list[str] = Field(default_factory=list)
    available_filter_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class BatchFilterPreflightResponse(BaseModel):
    success: bool
    message: str
    data: list[FilterPreflightResult]
    partial_success: bool | None = None
    total_queries: int | None = None
    successful_queries: int | None = None
    failed_queries: int | None = None
