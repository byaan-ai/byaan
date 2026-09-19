import json
from datetime import datetime
from typing import Any

import pytest

from server.schemas.query import QueryFilter, SavedQueryResult
from server.services import database_operations as database_operations_module
from server.services import raw_query as raw_query_module
from server.services.database_operations import DatabaseOperationsService
from server.services.filter_compiler import FilterCompilationError, FilterCompilerService
from server.services.query_service import QueryService
from server.services.raw_query import AsyncRawQueryService


def test_apply_filters_to_sql_handles_cte_query() -> None:
    query = "WITH t AS (SELECT * FROM orders WHERE status = 'open') SELECT * FROM t ORDER BY created_at DESC"
    filters = [QueryFilter(field="region", operator="eq", value="EMEA")]

    filtered_query, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert "WITH t AS" in filtered_query
    assert "SELECT * FROM t WHERE" in filtered_query
    assert '"region" = :p1' in filtered_query
    assert params == {"p1": "EMEA"}


def test_apply_filters_to_sql_rejects_invalid_between_shape() -> None:
    query = "SELECT * FROM orders"
    filters = [QueryFilter(field="created_at", operator="between", value=["2025-01-01"])]

    with pytest.raises(ValueError, match="between"):
        DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")


def test_apply_filters_to_sql_places_where_before_group_by() -> None:
    query = "SELECT region, COUNT(*) AS total FROM orders GROUP BY region ORDER BY total DESC"
    filters = [QueryFilter(field="status", operator="eq", value="open")]

    filtered_query, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert " WHERE " in filtered_query
    assert filtered_query.index(" WHERE ") < filtered_query.index(" GROUP BY ")
    assert params == {"p1": "open"}


def test_apply_filters_to_sql_quotes_table_qualified_field() -> None:
    query = "SELECT * FROM genres g"
    filters = [QueryFilter(field="g.name", operator="eq", value="Rock")]

    filtered_query, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert '"g"."name" = :p1' in filtered_query
    assert params == {"p1": "Rock"}


def test_apply_filters_to_sql_accepts_spaced_column_name() -> None:
    query = "SELECT * FROM orders"
    filters = [QueryFilter(field="Order Date", operator="eq", value="2025-01-01")]

    filtered_query, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert '"Order Date" = :p1' in filtered_query
    assert params == {"p1": "2025-01-01"}


def test_apply_filters_to_sql_accepts_hyphenated_column_name() -> None:
    query = "SELECT * FROM orders"
    filters = [QueryFilter(field="user-id", operator="eq", value="u_1")]

    filtered_query, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert '"user-id" = :p1' in filtered_query
    assert params == {"p1": "u_1"}


def test_apply_filters_to_sql_resolves_select_alias_expression() -> None:
    query = """
    SELECT
      t.id,
      CASE
        WHEN t.subscription_type IS NULL THEN 'Registered'
        ELSE 'Active Customer'
      END AS customer_status
    FROM tenants t
    """
    filters = [QueryFilter(field="customer_status", operator="eq", value="Registered")]

    filtered_query, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert '"customer_status"' not in filtered_query
    assert "CASE" in filtered_query
    assert "WHERE" in filtered_query
    assert params == {"p1": "Registered"}


def test_apply_filters_to_sql_fallback_uses_and_when_where_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_parse_error(*args, **kwargs):
        raise ValueError("forced parse failure")

    monkeypatch.setattr(database_operations_module.sqlglot, "parse_one", _raise_parse_error)

    query = "SELECT * FROM orders WHERE status = 'open'"
    filters = [QueryFilter(field="region", operator="eq", value="EMEA")]

    filtered_query, _ = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert filtered_query.upper().count(" WHERE ") == 1
    assert "AND (" in filtered_query


def test_apply_filters_to_sql_fallback_ignores_nested_where(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_parse_error(*args, **kwargs):
        raise ValueError("forced parse failure")

    monkeypatch.setattr(database_operations_module.sqlglot, "parse_one", _raise_parse_error)

    query = "SELECT * FROM (SELECT * FROM orders WHERE status = 'open') t"
    filters = [QueryFilter(field="region", operator="eq", value="EMEA")]

    filtered_query, _ = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert filtered_query.upper().count(" WHERE ") == 2
    assert " t WHERE (" in filtered_query


def test_apply_filters_to_sql_fallback_uses_context_to_remap_unscoped_qualified_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_parse_one = database_operations_module.sqlglot.parse_one
    call_count = {"count": 0}

    def _fail_once_then_parse(*args, **kwargs):
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise ValueError("forced initial parse failure")
        return real_parse_one(*args, **kwargs)

    monkeypatch.setattr(database_operations_module.sqlglot, "parse_one", _fail_once_then_parse)

    query = (
        "WITH tenant_users AS ("
        "SELECT t.id, t.created_at AS tenant_created_at FROM tenants t"
        ") "
        "SELECT tu.id, tu.tenant_created_at FROM tenant_users tu ORDER BY tu.tenant_created_at DESC"
    )
    filters = [QueryFilter(field="t.created_at", operator="gte", value="2026-02-01", ui_type="date_range")]

    filtered_query, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert call_count["count"] >= 2
    assert "tu.tenant_created_at >= :p1" in filtered_query
    assert "t.created_at >= :p1" not in filtered_query
    assert params["p1"] == datetime(2026, 2, 1, 0, 0, 0)


def test_apply_filters_to_sql_rejects_unscoped_qualified_field_when_mapping_is_ambiguous() -> None:
    query = (
        "SELECT tu.tenant_created_at, uu.user_created_at "
        "FROM tenant_users tu "
        "JOIN user_users uu ON uu.tenant_id = tu.tenant_id"
    )
    filters = [QueryFilter(field="t.created_at", operator="gte", value="2026-02-01", ui_type="date_range")]

    with pytest.raises(ValueError, match="not available in the outer query scope"):
        DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")


def test_apply_filters_to_sql_ast_path_uses_expression_builder_for_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_parse_one = database_operations_module.sqlglot.parse_one
    calls: list[str] = []

    def _tracked_parse_one(*args, **kwargs):
        calls.append(str(args[0]))
        return real_parse_one(*args, **kwargs)

    monkeypatch.setattr(database_operations_module.sqlglot, "parse_one", _tracked_parse_one)

    query = "SELECT * FROM orders"
    filters = [QueryFilter(field="region", operator="eq", value="EMEA")]

    filtered_query, _ = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert len(calls) == 1  # only parse the source query; filter condition is built via sqlglot expressions
    assert '"region" = :p1' in filtered_query


def test_apply_filters_to_sql_coerces_date_range_params_to_datetime() -> None:
    query = "SELECT * FROM orders"
    filters = [
        QueryFilter(field="created_at", operator="between", value=["2025-01-01", "2025-01-01"], ui_type="date_range")
    ]

    _, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert params["p1"] == datetime(2025, 1, 1, 0, 0, 0)
    assert params["p2"] == datetime(2025, 1, 1, 23, 59, 59, 999999)


def test_apply_filters_to_sql_coerces_lte_date_to_end_of_day() -> None:
    query = "SELECT * FROM orders"
    filters = [QueryFilter(field="created_at", operator="lte", value="2025-01-01", ui_type="date_range")]

    _, params = DatabaseOperationsService.apply_filters_to_sql(query, filters, "pg")

    assert params["p1"] == datetime(2025, 1, 1, 23, 59, 59, 999999)


def test_apply_filters_to_mongo_preserves_existing_predicate() -> None:
    query = "db.orders.find({status: 'complete'})"
    filters = [QueryFilter(field="region", operator="eq", value="EMEA")]

    filtered_query = DatabaseOperationsService.apply_filters_to_mongo(query, filters)

    assert "status" in filtered_query
    assert "complete" in filtered_query
    assert "region" in filtered_query
    assert "EMEA" in filtered_query


def test_build_mongo_filter_doc_merges_gte_lte_same_field() -> None:
    filters = [
        QueryFilter(field="created_at", operator="gte", value="2025-01-01"),
        QueryFilter(field="created_at", operator="lte", value="2025-12-31"),
    ]

    doc = DatabaseOperationsService._build_mongo_filter_doc(filters)

    assert "created_at" in doc
    assert isinstance(doc["created_at"], dict)
    assert "$gte" in doc["created_at"]
    assert "$lte" in doc["created_at"]


def test_filter_compiler_compiles_filter_values_using_contract() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_status",
                "field_name": "orders.status",
                "display_label": "Status",
                "filter_type": "select",
                "data_type": "string",
                "allowed_operators": ["eq", "in"],
                "default_operator": "eq",
            },
            {
                "id": "filter_date",
                "field_name": "orders.created_at",
                "display_label": "Created Date",
                "filter_type": "date_range",
                "data_type": "date",
                "allowed_operators": ["between", "gte", "lte"],
                "default_operator": "between",
            },
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_status": "paid", "filter_date_start": "2025-01-01"},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 2
    assert any(f.field == "orders.status" and f.operator == "eq" and f.value == "paid" for f in compiled)
    assert any(
        f.field == "orders.created_at" and f.operator == "gte" and str(f.value).startswith("2025-01-01")
        for f in compiled
    )


def test_filter_compiler_normalizes_date_end_boundary_for_lte_and_between() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_date",
                "field_name": "orders.created_at",
                "display_label": "Created Date",
                "filter_type": "date_range",
                "data_type": "date",
                "allowed_operators": ["between", "gte", "lte"],
                "default_operator": "between",
            }
        ]
    }

    compiled_lte = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_date_end": "2025-01-01"},
        filter_contract_json=json.dumps(contract),
    )
    assert len(compiled_lte) == 1
    assert compiled_lte[0].operator == "lte"
    assert compiled_lte[0].value == "2025-01-01T23:59:59.999999"

    compiled_between = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        raw_filters=[{"field": "filter_date", "operator": "between", "value": ["2025-01-01", "2025-01-01"]}],
        filter_contract_json=json.dumps(contract),
    )
    assert len(compiled_between) == 1
    assert compiled_between[0].operator == "between"
    assert compiled_between[0].value == ["2025-01-01", "2025-01-01T23:59:59.999999"]

    compiled_raw_lte = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        raw_filters=[{"field": "orders.created_at", "operator": "lte", "value": "2025-01-01"}],
        filter_contract_json=json.dumps(contract),
    )
    assert len(compiled_raw_lte) == 1
    assert compiled_raw_lte[0].operator == "lte"
    assert compiled_raw_lte[0].value == "2025-01-01T23:59:59.999999"


def test_filter_compiler_number_range_min_max_compiles_to_bounds() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_amount",
                "field_name": "orders.amount",
                "display_label": "Amount",
                "filter_type": "number_range",
                "data_type": "number",
                "allowed_operators": ["between", "gte", "lte", "eq", "gt", "lt"],
                "default_operator": "between",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_amount_min": "10", "filter_amount_max": "20"},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 2
    assert any(item.operator == "gte" and item.value == 10 for item in compiled)
    assert any(item.operator == "lte" and item.value == 20 for item in compiled)


def test_filter_compiler_number_range_scalar_defaults_to_eq() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_amount",
                "field_name": "orders.amount",
                "display_label": "Amount",
                "filter_type": "number_range",
                "data_type": "number",
                "allowed_operators": ["between", "gte", "lte", "eq", "gt", "lt"],
                "default_operator": "between",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_amount": "10.5"},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 1
    assert compiled[0].operator == "eq"
    assert compiled[0].value == 10.5


def test_filter_compiler_number_range_accepts_dict_and_numeric_formats() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_amount",
                "field_name": "orders.amount",
                "display_label": "Amount",
                "filter_type": "number_range",
                "data_type": "number",
                "allowed_operators": ["between", "gte", "lte", "eq", "gt", "lt"],
                "default_operator": "between",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_amount": {"min": "1,000", "max": "2.5e3"}},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 2
    assert any(item.operator == "gte" and item.value == 1000 for item in compiled)
    assert any(item.operator == "lte" and item.value == 2500.0 for item in compiled)


def test_filter_compiler_number_range_ignores_transient_minus_input() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_amount",
                "field_name": "orders.amount",
                "display_label": "Amount",
                "filter_type": "number_range",
                "data_type": "number",
                "allowed_operators": ["between", "gte", "lte", "eq", "gt", "lt"],
                "default_operator": "between",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_amount_min": "-", "filter_amount_max": "20"},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 1
    assert compiled[0].operator == "lte"
    assert compiled[0].value == 20


DATE_RANGE_CONTRACT = json.dumps(
    {
        "filters": [
            {
                "id": "filter_date",
                "field_name": "orders.created_at",
                "display_label": "Created Date",
                "filter_type": "date_range",
                "data_type": "date",
                "allowed_operators": ["between", "gte", "lte", "eq"],
                "default_operator": "between",
            }
        ]
    }
)

NUMBER_RANGE_CONTRACT = json.dumps(
    {
        "filters": [
            {
                "id": "filter_amount",
                "field_name": "orders.amount",
                "display_label": "Amount",
                "filter_type": "number_range",
                "data_type": "number",
                "allowed_operators": ["between", "gte", "lte", "eq", "gt", "lt"],
                "default_operator": "between",
            }
        ]
    }
)


def test_filter_compiler_date_range_two_value_list_compiles_to_between() -> None:
    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_date": ["2025-01-01", "2025-01-31"]},
        filter_contract_json=DATE_RANGE_CONTRACT,
    )

    assert len(compiled) == 1
    assert compiled[0].field == "orders.created_at"
    assert compiled[0].operator == "between"
    assert compiled[0].value == ["2025-01-01", "2025-01-31T23:59:59.999999"]


def test_filter_compiler_number_range_two_value_list_compiles_to_between() -> None:
    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_amount": ["10", "20.5"]},
        filter_contract_json=NUMBER_RANGE_CONTRACT,
    )

    assert len(compiled) == 1
    assert compiled[0].field == "orders.amount"
    assert compiled[0].operator == "between"
    assert compiled[0].value == [10, 20.5]


def test_filter_compiler_range_list_rejects_single_endpoint() -> None:
    with pytest.raises(FilterCompilationError, match="exactly 2 values"):
        FilterCompilerService.compile_with_contract(
            query_id="query-1",
            filter_values={"filter_amount": ["10"]},
            filter_contract_json=NUMBER_RANGE_CONTRACT,
        )


def test_filter_compiler_range_list_rejects_three_endpoints() -> None:
    with pytest.raises(FilterCompilationError, match="exactly 2 values"):
        FilterCompilerService.compile_with_contract(
            query_id="query-1",
            filter_values={"filter_date": ["2025-01-01", "2025-01-15", "2025-01-31"]},
            filter_contract_json=DATE_RANGE_CONTRACT,
        )


def test_filter_compiler_date_range_dict_bounds_stay_on_gte_lte() -> None:
    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_date": {"start": "2025-01-01", "end": "2025-01-31"}},
        filter_contract_json=DATE_RANGE_CONTRACT,
    )

    assert len(compiled) == 2
    assert any(item.operator == "gte" and item.value == "2025-01-01" for item in compiled)
    assert any(item.operator == "lte" and item.value == "2025-01-31T23:59:59.999999" for item in compiled)


def test_filter_compiler_date_range_open_ended_dict_compiles_single_bound() -> None:
    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_date": {"start": "2025-01-01"}},
        filter_contract_json=DATE_RANGE_CONTRACT,
    )

    assert len(compiled) == 1
    assert compiled[0].operator == "gte"
    assert compiled[0].value == "2025-01-01"


def test_filter_compiler_date_range_scalar_compiles_to_eq() -> None:
    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_date": "2025-01-01"},
        filter_contract_json=DATE_RANGE_CONTRACT,
    )

    assert len(compiled) == 1
    assert compiled[0].operator == "eq"
    assert compiled[0].value == "2025-01-01"


def test_filter_compiler_multiselect_list_still_compiles_to_in() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_status",
                "field_name": "orders.status",
                "display_label": "Status",
                "filter_type": "multiselect",
                "data_type": "string",
                "allowed_operators": ["in"],
                "default_operator": "in",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_status": ["open", "paid"]},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 1
    assert compiled[0].operator == "in"
    assert compiled[0].value == ["open", "paid"]


def test_filter_compiler_select_list_still_compiles_to_in() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_status",
                "field_name": "orders.status",
                "display_label": "Status",
                "filter_type": "select",
                "data_type": "string",
                "allowed_operators": ["eq", "ne", "in"],
                "default_operator": "eq",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_status": ["open", "paid"]},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 1
    assert compiled[0].operator == "in"
    assert compiled[0].value == ["open", "paid"]


def test_filter_compiler_rejects_unknown_filter_key() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_status",
                "field_name": "orders.status",
                "filter_type": "select",
                "allowed_operators": ["eq"],
                "default_operator": "eq",
            }
        ]
    }

    with pytest.raises(FilterCompilationError, match="Unknown filter key"):
        FilterCompilerService.compile_with_contract(
            query_id="query-1",
            filter_values={"filter_not_real": "x"},
            filter_contract_json=json.dumps(contract),
        )


@pytest.mark.asyncio
async def test_filter_compiler_uses_notebook_filters_config_when_contract_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeQuery:
        filter_contract = None
        notebook_id = "n1"

    class _FakeNotebook:
        filters_config = json.dumps(
            {
                "version": 1,
                "filters": [
                    {
                        "id": "filter_t.name",
                        "query_id": "q1",
                        "field_name": "t.name",
                        "display_label": "Customer Name",
                        "filter_type": "select",
                        "operator": "eq",
                        "options": ["Acme"],
                    }
                ],
            }
        )

    class _FakeQueryRepository:
        def __init__(self, session):
            self._session = session

        async def get(self, query_id: str):
            return _FakeQuery() if query_id == "q1" else None

    class _FakeNotebookRepository:
        def __init__(self, session):
            self._session = session

        async def get(self, notebook_id: str):
            return _FakeNotebook() if notebook_id == "n1" else None

    monkeypatch.setattr("server.services.filter_compiler.QueryRepository", _FakeQueryRepository)
    monkeypatch.setattr("server.services.filter_compiler.NotebookRepository", _FakeNotebookRepository)

    compiled = await FilterCompilerService.compile_for_query(
        session=None,
        query_id="q1",
        filter_values={"filter_t.name": "Acme"},
    )

    assert len(compiled) == 1
    assert compiled[0].field == "t.name"
    assert compiled[0].operator == "eq"
    assert compiled[0].value == "Acme"


@pytest.mark.asyncio
async def test_execute_raw_query_duckdb_inlines_named_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_execute_duckdb_query(
        connection_obj: dict[str, Any],
        query: str,
        limit: int = 5,
        timeout: int = 30,
    ) -> dict[str, Any]:
        captured["connection_obj"] = connection_obj
        captured["query"] = query
        captured["limit"] = limit
        return {"success": True, "result": []}

    monkeypatch.setattr(raw_query_module.DataFrameFileService, "execute_duckdb_query", _fake_execute_duckdb_query)

    result = await AsyncRawQueryService.execute_raw_query(
        query="SELECT * FROM orders WHERE region = :p1 AND is_active = :p2",
        db_type="duckdb",
        connection_id="conn-1",
        connection_obj={"dataset_id": "dataset-1"},
        params={"p1": "O'Reilly", "p2": True},
    )

    assert result.get("success") is True
    assert captured["query"] == "SELECT * FROM orders WHERE region = 'O''Reilly' AND is_active = TRUE"
    assert captured["limit"] == 500


@pytest.mark.asyncio
async def test_execute_raw_query_sql_passes_named_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeSQLConnector:
        async def execute_query(
            self, query: str, limit: int = None, timeout: int = 30, params: dict[str, Any] | None = None
        ):
            captured["query"] = query
            captured["limit"] = limit
            captured["params"] = params
            return {"success": True, "result": []}

    async def _fake_get_sql_connector(connection_id: str, connection_obj: dict[str, Any], db_type: str = "pg"):
        captured["connection_id"] = connection_id
        captured["db_type"] = db_type
        return _FakeSQLConnector()

    monkeypatch.setattr(
        raw_query_module.AsyncDatabaseService,
        "get_or_create_sql_connector",
        _fake_get_sql_connector,
    )

    result = await AsyncRawQueryService.execute_raw_query(
        query="SELECT * FROM orders WHERE region = :p1",
        db_type="pg",
        connection_id="conn-1",
        connection_obj={"host": "localhost"},
        limit=25,
        params={"p1": "EMEA"},
    )

    assert result.get("success") is True
    assert captured["db_type"] == "pg"
    assert captured["query"] == "SELECT * FROM orders WHERE region = :p1"
    assert captured["limit"] == 25
    assert captured["params"] == {"p1": "EMEA"}


@pytest.mark.asyncio
async def test_execute_batch_saved_queries_preserves_order_and_compile_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compile_calls: list[tuple[str, dict[str, Any] | None]] = []

    async def _fake_compile_for_query(session, query_id: str, raw_filters=None, filter_values=None):
        compile_calls.append((query_id, filter_values))
        if query_id == "q2":
            raise FilterCompilationError("unknown filter key")
        return [QueryFilter(field="region", operator="eq", value="EMEA")]

    async def _fake_execute_single(query_id: str, session, semaphore, filters=None):
        return SavedQueryResult(
            query_id=query_id,
            query_name=f"name-{query_id}",
            success=True,
            result=[{"query_id": query_id, "filters_count": len(filters or [])}],
            error=None,
            execution_time_ms=1.0,
        )

    monkeypatch.setattr(FilterCompilerService, "compile_for_query", _fake_compile_for_query)
    monkeypatch.setattr(QueryService, "_execute_single_saved_query_async", _fake_execute_single)

    result = await QueryService.execute_batch_saved_queries(
        session=None,
        queries_with_filters=[
            {"query_id": "q1", "filter_values": {"filter_region": "EMEA"}},
            {"query_id": "q2", "filter_values": {"filter_bad": "x"}},
            {"query_id": "q3", "filters": [{"field": "status", "operator": "eq", "value": "open"}]},
        ],
        max_parallel=2,
    )

    assert result["total_queries"] == 3
    assert result["successful_queries"] == 2
    assert result["failed_queries"] == 1
    assert result["partial_success"] is True
    assert result["success"] is False

    ordered = result["data"]
    assert [entry.query_id for entry in ordered] == ["q1", "q2", "q3"]
    assert ordered[0].success is True
    assert ordered[1].success is False
    assert "Invalid filters" in (ordered[1].error or "")
    assert ordered[2].success is True

    assert compile_calls == [
        ("q1", {"filter_region": "EMEA"}),
        ("q2", {"filter_bad": "x"}),
        ("q3", None),
    ]


def test_filter_compiler_accepts_legacy_auto_filter_value_key() -> None:
    contract = {
        "filters": [
            {
                "id": "filter_t_name",
                "field_name": "t.name",
                "display_label": "Customer Name",
                "filter_type": "text",
                "data_type": "string",
                "allowed_operators": ["contains", "eq"],
                "default_operator": "contains",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"auto_895955e0_t_name": "Acme"},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 1
    assert compiled[0].field == "t.name"
    assert compiled[0].operator == "contains"
    assert compiled[0].value == "Acme"


def test_filter_compiler_accepts_new_field_based_key_for_legacy_contract() -> None:
    contract = {
        "filters": [
            {
                "id": "auto_895955e0_t_name",
                "field_name": "t.name",
                "display_label": "Customer Name",
                "filter_type": "text",
                "data_type": "string",
                "allowed_operators": ["contains", "eq"],
                "default_operator": "contains",
            }
        ]
    }

    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_t_name": "Acme"},
        filter_contract_json=json.dumps(contract),
    )

    assert len(compiled) == 1
    assert compiled[0].field == "t.name"
    assert compiled[0].operator == "contains"
    assert compiled[0].value == "Acme"


@pytest.mark.asyncio
async def test_preflight_batch_query_filters_reports_per_query_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeQuery:
        def __init__(self, query_id: str, name: str, filter_contract: str | None):
            self.id = query_id
            self.name = name
            self.filter_contract = filter_contract
            self.notebook_id = "n1"

    contract = json.dumps(
        {
            "filters": [
                {
                    "id": "filter_status",
                    "field_name": "orders.status",
                    "display_label": "Status",
                    "filter_type": "select",
                    "data_type": "string",
                    "allowed_operators": ["eq"],
                    "default_operator": "eq",
                }
            ]
        }
    )
    fake_queries = {
        "q1": _FakeQuery("q1", "Orders", contract),
        "q2": _FakeQuery("q2", "Customers", contract),
    }

    class _FakeQueryRepository:
        def __init__(self, session):
            self._session = session

        async def get(self, query_id: str):
            return fake_queries.get(query_id)

    monkeypatch.setattr("server.services.query_service.QueryRepository", _FakeQueryRepository)

    result = await QueryService.preflight_batch_query_filters(
        session=None,
        queries_with_filters=[
            {"query_id": "q1", "filter_values": {"filter_status": "open"}},
            {"query_id": "q2", "filter_values": {"filter_unknown": "x"}},
        ],
    )

    assert result["total_queries"] == 2
    assert result["successful_queries"] == 1
    assert result["failed_queries"] == 1
    assert result["partial_success"] is True
    assert result["success"] is False

    q1 = result["data"][0]
    assert q1["query_id"] == "q1"
    assert q1["success"] is True
    assert q1["available_filter_ids"] == ["filter_status"]
    assert len(q1["compiled_filters"]) == 1
    assert q1["compiled_filters"][0]["field"] == "orders.status"

    q2 = result["data"][1]
    assert q2["query_id"] == "q2"
    assert q2["success"] is False
    assert "Invalid filters" in (q2["error"] or "")


def test_list_range_reaches_sql_as_between_with_bound_values() -> None:
    compiled = FilterCompilerService.compile_with_contract(
        query_id="query-1",
        filter_values={"filter_amount": ["10", "20.5"]},
        filter_contract_json=NUMBER_RANGE_CONTRACT,
    )
    query, params = DatabaseOperationsService.apply_filters_to_sql("SELECT * FROM orders", compiled, "pg")
    assert '"orders"."amount" BETWEEN :p1 AND :p2' in query
    assert params == {"p1": 10, "p2": 20.5}
