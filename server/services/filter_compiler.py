from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from server.repositories.notebooks import NotebookRepository
from server.repositories.queries import QueryRepository
from server.schemas.query import QueryFilter
from server.services.filter_config_service import build_filter_contract_for_query, normalize_filter_id
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


class FilterCompilationError(ValueError):
    """Raised when filter intent cannot be compiled into valid backend filters."""


class FilterCompilerService:
    """Compile loose UI/user filter intent into validated QueryFilter entries."""

    SUPPORTED_OPERATORS = {"eq", "ne", "gt", "lt", "gte", "lte", "like", "in", "between", "contains"}
    ALLOWED_OPERATORS_BY_FILTER_TYPE = {
        "select": ["eq", "ne", "in"],
        "multiselect": ["in"],
        "date_range": ["between", "gte", "lte", "eq"],
        "number_range": ["between", "gte", "lte", "eq", "gt", "lt"],
        "text": ["contains", "like", "eq"],
    }
    DATA_TYPE_BY_FILTER_TYPE = {
        "select": "string",
        "multiselect": "string",
        "date_range": "date",
        "number_range": "number",
        "text": "string",
    }
    SUFFIX_OPERATOR_MAP = {
        "_start": "gte",
        "_end": "lte",
        "_min": "gte",
        "_max": "lte",
    }
    RANGE_LOWER_BOUND_KEYS = ("min", "minimum", "start", "from", "gte", "low", "lower")
    RANGE_UPPER_BOUND_KEYS = ("max", "maximum", "end", "to", "lte", "high", "upper")
    DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    @staticmethod
    async def compile_for_query(
        session: AsyncSession,
        query_id: str,
        raw_filters: list[dict[str, Any] | QueryFilter] | None = None,
        filter_values: dict[str, Any] | None = None,
    ) -> list[QueryFilter]:
        query_repo = QueryRepository(session)
        saved_query = await query_repo.get(query_id)
        if not saved_query:
            raise FilterCompilationError(f"Query '{query_id}' not found")

        resolved_contract = saved_query.filter_contract
        if filter_values and not resolved_contract:
            resolved_contract = await FilterCompilerService._resolve_contract_from_notebook_config(
                session=session,
                query_id=query_id,
                notebook_id=getattr(saved_query, "notebook_id", None),
            )

        return FilterCompilerService.compile_with_contract(
            query_id=query_id,
            raw_filters=raw_filters,
            filter_values=filter_values,
            filter_contract_json=resolved_contract,
        )

    @staticmethod
    async def _resolve_contract_from_notebook_config(
        session: AsyncSession,
        query_id: str,
        notebook_id: Any,
    ) -> str | None:
        if not notebook_id:
            return None

        notebook_repo = NotebookRepository(session)
        notebook = await notebook_repo.get(notebook_id)
        if not notebook or not notebook.filters_config:
            return None

        try:
            parsed = json.loads(notebook.filters_config)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse notebook.filters_config while compiling filters",
                posthog_context={"query_id": query_id, "notebook_id": str(notebook_id)},
            )
            return None

        raw_filters = parsed.get("filters") if isinstance(parsed, dict) else None
        if not isinstance(raw_filters, list):
            return None

        query_filters = [
            item
            for item in raw_filters
            if isinstance(item, dict) and str(item.get("query_id", "")).strip() == str(query_id)
        ]
        if not query_filters:
            return None

        logger.info(
            "Using notebook filters_config to derive missing filter_contract",
            posthog_context={"query_id": query_id, "notebook_id": str(notebook_id)},
        )
        return build_filter_contract_for_query(
            query_id=query_id,
            query_filters=query_filters,
            existing_contract_json=None,
        )

    @staticmethod
    def compile_with_contract(
        query_id: str,
        raw_filters: list[dict[str, Any] | QueryFilter] | None = None,
        filter_values: dict[str, Any] | None = None,
        filter_contract_json: str | None = None,
    ) -> list[QueryFilter]:
        raw_filters = raw_filters or []
        filter_values = filter_values or {}

        contract = FilterCompilerService._parse_contract(filter_contract_json)
        by_id, by_field, by_alias = FilterCompilerService._build_contract_indexes(contract)

        compiled: list[QueryFilter] = []

        if filter_values:
            if not by_id:
                raise FilterCompilationError(
                    f"Query '{query_id}' has no filter contract metadata. "
                    "Save dashboard filters first before using filter_values."
                )

            for filter_key, value in filter_values.items():
                if FilterCompilerService._is_empty_filter_value(value):
                    continue

                spec, operator_override = FilterCompilerService._resolve_filter_value_spec(
                    by_id=by_id,
                    by_alias=by_alias,
                    filter_key=str(filter_key),
                )
                if not spec:
                    raise FilterCompilationError(
                        f"Unknown filter key '{filter_key}' for query '{query_id}'. "
                        f"Expected one of: {sorted(by_id.keys())}"
                    )

                expanded_entries = FilterCompilerService._expand_filter_value_entries(
                    spec=spec,
                    filter_key=str(filter_key),
                    value=value,
                    operator_override=operator_override,
                )
                for operator, entry_value, context_key in expanded_entries:
                    FilterCompilerService._validate_operator(spec, operator, query_id)
                    mapped_value = FilterCompilerService._map_label_to_value(spec, entry_value)
                    coerced_value = FilterCompilerService._coerce_value(
                        spec, operator, mapped_value, query_id, context_key
                    )

                    compiled.append(
                        QueryFilter(
                            field=spec["field_name"],
                            operator=operator,
                            value=coerced_value,
                            ui_type=spec.get("filter_type", "input"),
                            ui_label=spec.get("display_label"),
                        )
                    )

        for entry in raw_filters:
            query_filter = entry if isinstance(entry, QueryFilter) else QueryFilter(**entry)
            operator = query_filter.operator.lower()
            if operator not in FilterCompilerService.SUPPORTED_OPERATORS:
                raise FilterCompilationError(
                    f"Unsupported operator '{query_filter.operator}' for query '{query_id}'. "
                    f"Supported: {sorted(FilterCompilerService.SUPPORTED_OPERATORS)}"
                )

            spec = None
            if by_id or by_field:
                raw_field_key = str(query_filter.field)
                spec = (
                    by_id.get(raw_field_key)
                    or by_field.get(raw_field_key)
                    or by_field.get(raw_field_key.lower())
                    or by_alias.get(raw_field_key.lower())
                    or by_alias.get(FilterCompilerService._canonicalize_filter_lookup_key(raw_field_key))
                )
                if not spec:
                    raise FilterCompilationError(
                        f"Unknown filter field/id '{query_filter.field}' for query '{query_id}'. "
                        "Use configured filter ids or source field names from the filter contract."
                    )
                FilterCompilerService._validate_operator(spec, operator, query_id)
                mapped_value = FilterCompilerService._map_label_to_value(spec, query_filter.value)
                coerced_value = FilterCompilerService._coerce_value(spec, operator, mapped_value, query_id)
                resolved_field = spec["field_name"]
                ui_type = spec.get("filter_type", query_filter.ui_type)
                ui_label = spec.get("display_label", query_filter.ui_label)
            else:
                coerced_value = FilterCompilerService._coerce_untyped_value(operator, query_filter.value, query_id)
                resolved_field = query_filter.field
                ui_type = query_filter.ui_type
                ui_label = query_filter.ui_label

            compiled.append(
                QueryFilter(
                    field=resolved_field,
                    operator=operator,
                    value=coerced_value,
                    ui_type=ui_type,
                    ui_label=ui_label,
                    ui_options=query_filter.ui_options,
                )
            )

        return compiled

    @staticmethod
    def _parse_contract(filter_contract_json: str | None) -> list[dict[str, Any]]:
        if not filter_contract_json:
            return []
        try:
            parsed = json.loads(filter_contract_json)
            if isinstance(parsed, dict):
                filters = parsed.get("filters", [])
                return filters if isinstance(filters, list) else []
            if isinstance(parsed, list):
                return parsed
            return []
        except json.JSONDecodeError:
            logger.warning("Failed to parse filter_contract JSON")
            return []

    @staticmethod
    def _build_contract_indexes(
        contract: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        by_id: dict[str, dict[str, Any]] = {}
        by_field: dict[str, dict[str, Any]] = {}
        by_alias: dict[str, dict[str, Any]] = {}
        for entry in contract:
            if not isinstance(entry, dict):
                continue
            filter_id = str(entry.get("id", "")).strip()
            field_name = str(entry.get("field_name", "")).strip()
            if not filter_id or not field_name:
                continue

            normalized = dict(entry)
            normalized["id"] = filter_id
            normalized["field_name"] = field_name

            filter_type = str(entry.get("filter_type", "text")).strip().lower()
            normalized["filter_type"] = filter_type

            data_type = str(entry.get("data_type", "")).strip().lower()
            if not data_type:
                data_type = FilterCompilerService.DATA_TYPE_BY_FILTER_TYPE.get(filter_type, "string")
            normalized["data_type"] = data_type

            allowed = entry.get("allowed_operators")
            if isinstance(allowed, list) and allowed:
                allowed_ops = [str(op).lower() for op in allowed if str(op).strip()]
            else:
                allowed_ops = FilterCompilerService.ALLOWED_OPERATORS_BY_FILTER_TYPE.get(
                    filter_type, ["eq", "contains", "in", "between", "gte", "lte"]
                )
            normalized["allowed_operators"] = allowed_ops

            default_operator = str(entry.get("default_operator", entry.get("operator", ""))).strip().lower()
            if not default_operator:
                default_operator = allowed_ops[0] if allowed_ops else "eq"
            normalized["default_operator"] = default_operator

            by_id[filter_id] = normalized
            by_field[field_name] = normalized

            alias_keys = {
                filter_id.lower(),
                field_name.lower(),
                FilterCompilerService._canonicalize_filter_lookup_key(filter_id),
                FilterCompilerService._canonicalize_filter_lookup_key(field_name),
                normalize_filter_id(query_id="", field_name=field_name).lower(),
            }
            for alias in alias_keys:
                if alias:
                    by_alias[alias] = normalized

        return by_id, by_field, by_alias

    @staticmethod
    def _lookup_spec(
        by_id: dict[str, dict[str, Any]],
        by_alias: dict[str, dict[str, Any]],
        key: str,
    ) -> dict[str, Any] | None:
        return (
            by_id.get(key)
            or by_alias.get(key.lower())
            or by_alias.get(FilterCompilerService._canonicalize_filter_lookup_key(key))
        )

    @staticmethod
    def _resolve_filter_value_spec(
        by_id: dict[str, dict[str, Any]],
        by_alias: dict[str, dict[str, Any]],
        filter_key: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """
        Resolve a filter_values key to its contract spec.

        An exactly configured id/alias always wins, so columns named like `period_end` or
        `score_max` keep their identity. Range suffixes are only treated as operator
        shorthand (`filter_date_end` -> lte on `filter_date`) when no exact match exists.
        """
        exact_spec = FilterCompilerService._lookup_spec(by_id, by_alias, filter_key)
        if exact_spec:
            return exact_spec, None

        base_key, operator_override = FilterCompilerService._split_filter_key(filter_key)
        if not operator_override:
            return None, None

        return FilterCompilerService._lookup_spec(by_id, by_alias, base_key), operator_override

    @staticmethod
    def _split_filter_key(filter_key: str) -> tuple[str, str | None]:
        lowered = str(filter_key).lower()
        for suffix, operator in FilterCompilerService.SUFFIX_OPERATOR_MAP.items():
            if lowered.endswith(suffix):
                return filter_key[: -len(suffix)], operator
        return filter_key, None

    @staticmethod
    def _canonicalize_filter_lookup_key(key: str) -> str:
        raw = str(key or "").strip().lower()
        if not raw:
            return raw

        if raw.startswith("auto_"):
            # Legacy IDs looked like auto_<querytoken>_<field_token>
            parts = raw.split("_", 2)
            if len(parts) == 3 and parts[2]:
                return f"filter_{parts[2]}"

        if raw.startswith("filter."):
            raw = raw.replace(".", "_")
        if raw.startswith("filter-"):
            raw = raw.replace("-", "_")

        if "." in raw and not raw.startswith("filter_"):
            field_based = normalize_filter_id(query_id="", field_name=raw)
            return field_based.lower()

        return raw

    @staticmethod
    def _resolve_operator(spec: dict[str, Any], value: Any) -> str:
        filter_type = str(spec.get("filter_type", "")).lower()
        if filter_type == "multiselect" or isinstance(value, list):
            return "in"
        if filter_type in {"date_range", "number_range"}:
            if isinstance(value, list):
                return "between"
            allowed = [str(op).lower() for op in spec.get("allowed_operators", [])]
            for candidate in ("eq", "gte", "lte", "gt", "lt"):
                if candidate in allowed:
                    return candidate
        return str(spec.get("default_operator", "eq")).lower()

    @staticmethod
    def _extract_range_bounds(value: Any) -> tuple[Any, Any]:
        if not isinstance(value, dict):
            return None, None

        normalized: dict[str, Any] = {str(k).strip().lower(): v for k, v in value.items()}

        lower = None
        upper = None
        for key in FilterCompilerService.RANGE_LOWER_BOUND_KEYS:
            if key in normalized:
                lower = normalized[key]
                break
        for key in FilterCompilerService.RANGE_UPPER_BOUND_KEYS:
            if key in normalized:
                upper = normalized[key]
                break
        return lower, upper

    @staticmethod
    def _is_transient_number_input(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        return value.strip() in {"", "+", "-", ".", "+.", "-."}

    @staticmethod
    def _expand_filter_value_entries(
        spec: dict[str, Any],
        filter_key: str,
        value: Any,
        operator_override: str | None,
    ) -> list[tuple[str, Any, str]]:
        filter_type = str(spec.get("filter_type", "")).lower()

        if operator_override:
            if filter_type == "number_range" and FilterCompilerService._is_transient_number_input(value):
                return []
            return [(operator_override, value, filter_key)]

        if filter_type in {"date_range", "number_range"} and isinstance(value, dict):
            lower_value, upper_value = FilterCompilerService._extract_range_bounds(value)
            entries: list[tuple[str, Any, str]] = []

            if not FilterCompilerService._is_empty_filter_value(lower_value):
                if not (
                    filter_type == "number_range" and FilterCompilerService._is_transient_number_input(lower_value)
                ):
                    entries.append(("gte", lower_value, f"{filter_key}_min"))
            if not FilterCompilerService._is_empty_filter_value(upper_value):
                if not (
                    filter_type == "number_range" and FilterCompilerService._is_transient_number_input(upper_value)
                ):
                    entries.append(("lte", upper_value, f"{filter_key}_max"))
            return entries

        if filter_type == "number_range" and FilterCompilerService._is_transient_number_input(value):
            return []

        operator = FilterCompilerService._resolve_operator(spec, value)
        return [(operator, value, filter_key)]

    @staticmethod
    def _validate_operator(spec: dict[str, Any], operator: str, query_id: str) -> None:
        allowed = [str(op).lower() for op in spec.get("allowed_operators", [])]
        if allowed and operator not in allowed:
            raise FilterCompilationError(
                f"Operator '{operator}' is not allowed for filter '{spec.get('id')}' on query '{query_id}'. "
                f"Allowed: {allowed}"
            )

    @staticmethod
    def _map_label_to_value(spec: dict[str, Any], input_value: Any) -> Any:
        """
        If filter options use {label, value} structure, map display label to actual value.
        Returns the actual value if mapping exists, otherwise returns input unchanged.
        """
        options = spec.get("options")
        if not isinstance(options, list) or not options:
            return input_value

        first_option = options[0]
        if not isinstance(first_option, dict) or "label" not in first_option or "value" not in first_option:
            return input_value

        label_to_value_map = {
            str(opt["label"]): opt["value"]
            for opt in options
            if isinstance(opt, dict) and "label" in opt and "value" in opt
        }

        if isinstance(input_value, list):
            return [label_to_value_map.get(str(v), v) for v in input_value]

        return label_to_value_map.get(str(input_value), input_value)

    @staticmethod
    def _coerce_value(
        spec: dict[str, Any],
        operator: str,
        value: Any,
        query_id: str,
        filter_key: str | None = None,
    ) -> Any:
        data_type = str(spec.get("data_type", "string")).lower()
        context_key = filter_key or str(spec.get("id", spec.get("field_name", "filter")))

        if operator in {"in", "between"}:
            if not isinstance(value, list):
                value = [value]
            if operator == "between" and len(value) != 2:
                raise FilterCompilationError(
                    f"Filter '{context_key}' on query '{query_id}' expects exactly 2 values for 'between'."
                )
            coerced_values = [FilterCompilerService._coerce_scalar(data_type, v, query_id, context_key) for v in value]
            return FilterCompilerService._normalize_date_range_end_bound(spec, operator, coerced_values)

        coerced_scalar = FilterCompilerService._coerce_scalar(data_type, value, query_id, context_key)
        return FilterCompilerService._normalize_date_scalar_end_bound(spec, operator, coerced_scalar)

    @staticmethod
    def _coerce_untyped_value(operator: str, value: Any, query_id: str) -> Any:
        if operator in {"in", "between"}:
            if not isinstance(value, list):
                value = [value]
            if operator == "between" and len(value) != 2:
                raise FilterCompilationError(
                    f"Query '{query_id}' received invalid 'between' filter value. Expected list of two values."
                )
            return value
        return value

    @staticmethod
    def _coerce_scalar(data_type: str, value: Any, query_id: str, filter_key: str) -> Any:
        if data_type in {"string", "text"}:
            return str(value)

        if data_type in {"number", "numeric", "int", "integer", "float", "double", "decimal"}:
            if isinstance(value, bool):
                raise FilterCompilationError(
                    f"Filter '{filter_key}' on query '{query_id}' expects a number, got boolean."
                )
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, Decimal):
                return float(value)
            if isinstance(value, str):
                stripped = value.strip()
                normalized_numeric = stripped
                if normalized_numeric.startswith("$"):
                    normalized_numeric = normalized_numeric[1:]
                if normalized_numeric.endswith("%"):
                    normalized_numeric = normalized_numeric[:-1]
                normalized_numeric = normalized_numeric.replace(",", "").replace("_", "").replace(" ", "")
                try:
                    if re.fullmatch(r"[+-]?\d+", normalized_numeric):
                        return int(normalized_numeric)
                    return float(normalized_numeric)
                except ValueError as exc:
                    raise FilterCompilationError(
                        f"Filter '{filter_key}' on query '{query_id}' expects a number, got '{value}'."
                    ) from exc
            raise FilterCompilationError(
                f"Filter '{filter_key}' on query '{query_id}' expects a number, got '{type(value).__name__}'."
            )

        if data_type in {"bool", "boolean"}:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "y", "t"}:
                    return True
                if normalized in {"false", "0", "no", "n", "f"}:
                    return False
            raise FilterCompilationError(
                f"Filter '{filter_key}' on query '{query_id}' expects a boolean, got '{value}'."
            )

        if data_type in {"date", "datetime", "timestamp"}:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, str):
                return value.strip()
            raise FilterCompilationError(
                f"Filter '{filter_key}' on query '{query_id}' expects a date string, got '{type(value).__name__}'."
            )

        return value

    @staticmethod
    def _is_date_like_spec(spec: dict[str, Any]) -> bool:
        filter_type = str(spec.get("filter_type", "")).lower()
        data_type = str(spec.get("data_type", "")).lower()
        return filter_type == "date_range" or data_type in {"date", "datetime", "timestamp"}

    @staticmethod
    def _normalize_date_scalar_end_bound(spec: dict[str, Any], operator: str, value: Any) -> Any:
        if operator != "lte" or not FilterCompilerService._is_date_like_spec(spec):
            return value
        if isinstance(value, str) and FilterCompilerService.DATE_ONLY_PATTERN.fullmatch(value.strip()):
            return f"{value.strip()}T23:59:59.999999"
        return value

    @staticmethod
    def _normalize_date_range_end_bound(spec: dict[str, Any], operator: str, value: Any) -> Any:
        if operator != "between" or not FilterCompilerService._is_date_like_spec(spec):
            return value
        if not isinstance(value, list) or len(value) != 2:
            return value
        end_value = value[1]
        if isinstance(end_value, str) and FilterCompilerService.DATE_ONLY_PATTERN.fullmatch(end_value.strip()):
            normalized = list(value)
            normalized[1] = f"{end_value.strip()}T23:59:59.999999"
            return normalized
        return value

    @staticmethod
    def _is_empty_filter_value(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str) and value.strip() == "":
            return True
        if isinstance(value, dict):
            return len(value) == 0 or all(FilterCompilerService._is_empty_filter_value(v) for v in value.values())
        if isinstance(value, (list, tuple, set)):
            return len(value) == 0 or all(FilterCompilerService._is_empty_filter_value(v) for v in value)
        return False
