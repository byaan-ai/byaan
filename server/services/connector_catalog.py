from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ConnectorAvailability = Literal["beta", "planned"]
ConnectorAuthMode = Literal["oauth", "access_key", "connection_string", "none"]
ConnectorEntryKind = Literal["connector_backed", "embedded_flow", "roadmap"]
ConnectorReadinessGateStatus = Literal["partial", "missing", "not_applicable"]
ConnectorResourcePickerType = Literal[
    "none",
    "file_import",
    "url_import",
    "oauth_drive_picker",
    "object_storage_browser",
    "database_schema_picker",
    "warehouse_catalog_picker",
    "roadmap_only",
]

COMMERCIAL_READINESS_GATES: tuple[tuple[str, str], ...] = (
    ("tenant_isolated_auth", "Tenant-isolated authorization and encrypted credentials"),
    ("resource_picker", "Resource picker or explicit import contract"),
    ("already_added_state", "Already-added state"),
    ("immutable_snapshot", "Immutable snapshot with external revision and content hash"),
    ("raw_artifact_uri", "Raw artifact URI outside the control database"),
    ("parser_warnings", "Parser version and parser warnings"),
    ("context_index_status", "Context indexing status through KnowledgeProvider"),
    ("recoverable_errors", "Permission, reauthorization, source unavailable, and retryable failure states"),
    ("source_detail", "Source detail with snapshots, parsed assets, evidence, lineage, and consumers"),
    ("lifecycle_actions", "Clear delete, revoke, and reindex behavior"),
)


def _readiness_gates(
    *,
    status: ConnectorReadinessGateStatus,
    detail: str,
    overrides: dict[str, tuple[ConnectorReadinessGateStatus, str]] | None = None,
) -> tuple[dict[str, str], ...]:
    overrides = overrides or {}
    gates: list[dict[str, str]] = []
    for key, label in COMMERCIAL_READINESS_GATES:
        gate_status, gate_detail = overrides.get(key, (status, detail))
        gates.append({"key": key, "label": label, "status": gate_status, "detail": gate_detail})
    return tuple(gates)


BETA_GATES = _readiness_gates(
    status="partial",
    detail="Official landing includes the Source control-plane contract. Runtime connector execution, production credentials, and full provenance are not certified in this batch.",
)
WAREHOUSE_BETA_GATES = _readiness_gates(
    status="partial",
    detail="Official landing includes schema/profile Source representation only. Warehouse data remains external and semantic modeling stays beta.",
    overrides={
        "immutable_snapshot": (
            "not_applicable",
            "Warehouse Sources track schema/profile freshness instead of raw data snapshots.",
        ),
        "raw_artifact_uri": (
            "not_applicable",
            "Warehouse Sources do not copy raw table data into Byaan object storage.",
        ),
        "context_index_status": (
            "not_applicable",
            "Warehouse Sources feed future semantic modeling from schema/profile evidence.",
        ),
    },
)
PLANNED_GATES = _readiness_gates(
    status="missing",
    detail="Required before this roadmap entry can become a supported connector.",
)


@dataclass(frozen=True)
class ConnectorDefinition:
    id: str
    category: str
    display_name: str
    icon: str
    auth_mode: ConnectorAuthMode
    capabilities: tuple[str, ...]
    config_schema: dict[str, Any]
    resource_picker_schema: dict[str, Any]
    supported_resource_types: tuple[str, ...]
    availability: ConnectorAvailability
    description: str = ""
    limitations: tuple[str, ...] = ()
    required_scopes: tuple[str, ...] = ()
    resource_picker_type: ConnectorResourcePickerType = "none"
    modeling_modes: tuple[str, ...] = ()
    readiness_gates: tuple[dict[str, str], ...] = ()
    entry_kind: ConnectorEntryKind = "connector_backed"

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.id,
            "category": self.category,
            "family": _family_for_category(self.category),
            "display_name": self.display_name,
            "icon": self.icon,
            "auth_mode": self.auth_mode,
            "capabilities": list(self.capabilities),
            "limitations": list(self.limitations),
            "required_scopes": list(self.required_scopes),
            "config_schema": self.config_schema,
            "resource_picker_schema": self.resource_picker_schema,
            "resource_picker_type": self.resource_picker_type,
            "supported_resource_types": list(self.supported_resource_types),
            "availability": self.availability,
            "status": self.availability,
            "readiness_gates": list(self.readiness_gates),
            "modeling_modes": list(self.modeling_modes),
            "description": self.description,
            "entry_kind": self.entry_kind,
        }


def _field(name: str, field_type: str, *, required: bool = True, secret: bool = False) -> dict[str, Any]:
    return {"name": name, "type": field_type, "required": required, "secret": secret}


def _family_for_category(category: str) -> str:
    if category == "documents":
        return "business_docs"
    if category == "data_lake":
        return "warehouses"
    if category in {"messages", "search"}:
        return "api"
    return category


def _planned(
    *,
    id: str,
    category: str,
    display_name: str,
    icon: str,
    auth_mode: ConnectorAuthMode,
    capabilities: tuple[str, ...] = (),
    planned_adapter: str | None = None,
) -> ConnectorDefinition:
    return ConnectorDefinition(
        id=id,
        category=category,
        display_name=display_name,
        icon=icon,
        auth_mode=auth_mode,
        capabilities=capabilities,
        config_schema={"fields": []},
        resource_picker_schema={"planned_adapter": planned_adapter} if planned_adapter else {},
        supported_resource_types=(),
        availability="planned",
        limitations=("Roadmap entry only; no runtime connector contract has been certified in the official repo.",),
        resource_picker_type="roadmap_only",
        modeling_modes=(),
        readiness_gates=PLANNED_GATES,
        entry_kind="roadmap",
    )


CONNECTOR_CATALOG: tuple[ConnectorDefinition, ...] = (
    ConnectorDefinition(
        id="local_files",
        category="files",
        display_name="Files as Source",
        icon="file",
        auth_mode="none",
        capabilities=("file_import", "snapshot_metadata", "parser_artifact_contract"),
        config_schema={"embedded_flow": "files_source_upload", "fields": []},
        resource_picker_schema={"supported_extensions": ["pdf", "csv", "xlsx", "xlsm", "docx", "pptx"]},
        supported_resource_types=("file", "pdf"),
        availability="beta",
        description="Register uploaded files as governed Sources with immutable snapshot metadata.",
        limitations=(
            "This landing records Source metadata only; parser workers and KnowledgeProvider indexing are partial.",
        ),
        resource_picker_type="file_import",
        modeling_modes=("context_assisted", "projection"),
        readiness_gates=BETA_GATES,
        entry_kind="embedded_flow",
    ),
    ConnectorDefinition(
        id="web",
        category="web",
        display_name="Web page",
        icon="web",
        auth_mode="none",
        capabilities=("url_import", "snapshot_metadata", "crawl_policy_contract"),
        config_schema={"embedded_flow": "web_source_capture", "fields": []},
        resource_picker_schema={"supports_single_url": True, "supports_public_http": True, "ssrf_protection": True},
        supported_resource_types=("web",),
        availability="beta",
        description="Register public web pages as governed Sources.",
        limitations=("No crawler/runtime fetch is included in this official landing batch.",),
        resource_picker_type="url_import",
        modeling_modes=("context_assisted",),
        readiness_gates=BETA_GATES,
        entry_kind="embedded_flow",
    ),
    ConnectorDefinition(
        id="feishu",
        category="documents",
        display_name="飞书文档 / Wiki / Sheets / Base",
        icon="feishu",
        auth_mode="oauth",
        capabilities=("oauth_contract", "resource_browse_contract", "snapshot_metadata"),
        config_schema={"fields": []},
        resource_picker_schema={"scopes": ["recent", "drive", "wiki", "search"], "supports_multi_select": True},
        supported_resource_types=("feishu_doc", "feishu_wiki", "feishu_sheet", "feishu_base"),
        availability="beta",
        description="Control-plane contract for Feishu/Lark Sources.",
        limitations=("OAuth runtime, admin app setup, and live Drive picker are not landed in this batch.",),
        required_scopes=("space:document:retrieve", "docx:document:readonly", "wiki:wiki:readonly"),
        resource_picker_type="oauth_drive_picker",
        modeling_modes=("context_assisted", "projection"),
        readiness_gates=BETA_GATES,
    ),
    ConnectorDefinition(
        id="sql_databases",
        category="databases",
        display_name="SQL databases",
        icon="database",
        auth_mode="connection_string",
        capabilities=("schema_profile_contract", "semantic_model_handoff_contract"),
        config_schema={
            "embedded_flow": "database_connection",
            "providers": ["sqlite", "postgres", "mysql"],
            "fields": [],
        },
        resource_picker_schema={"picker": "database_schema_picker"},
        supported_resource_types=("database_catalog", "database_schema", "database_table"),
        availability="beta",
        description="Represent existing SQL database connections as governed Sources.",
        limitations=("Semantic model generation remains beta and requires reviewed schema/profile evidence.",),
        resource_picker_type="database_schema_picker",
        modeling_modes=("relational",),
        readiness_gates=WAREHOUSE_BETA_GATES,
        entry_kind="embedded_flow",
    ),
    ConnectorDefinition(
        id="volcengine_tos",
        category="object_storage",
        display_name="火山引擎 TOS",
        icon="tos",
        auth_mode="access_key",
        capabilities=("object_storage_contract", "bucket_browse_contract", "snapshot_metadata"),
        config_schema={
            "fields": [
                _field("endpoint", "url"),
                _field("region", "string"),
                _field("access_key_id", "string", secret=True),
                _field("secret_access_key", "string", secret=True),
                _field("session_token", "string", required=False, secret=True),
                _field("default_bucket", "string", required=False),
                _field("default_prefix", "string", required=False),
            ]
        },
        resource_picker_schema={"hierarchy": ["bucket", "prefix", "object"], "supports_multi_select": True},
        supported_resource_types=("tos_bucket", "tos_prefix", "tos_object"),
        availability="beta",
        description="Control-plane contract for TOS object storage Sources.",
        limitations=("No live TOS browse/sync adapter is included in this official landing batch.",),
        required_scopes=("tos:ListBucket", "tos:GetObject"),
        resource_picker_type="object_storage_browser",
        modeling_modes=("projection", "context_assisted"),
        readiness_gates=BETA_GATES,
    ),
    ConnectorDefinition(
        id="databricks",
        category="data_lake",
        display_name="Databricks",
        icon="databricks",
        auth_mode="oauth",
        capabilities=("warehouse_catalog_contract", "schema_profile_contract", "semantic_model_handoff_contract"),
        config_schema={"embedded_flow": "databricks_oauth_catalog", "fields": []},
        resource_picker_schema={"supports_catalogs": True, "supports_schemas": True, "supports_multi_select": True},
        supported_resource_types=("databricks_catalog", "databricks_schema", "databricks_table"),
        availability="beta",
        description="Represent Databricks warehouse metadata as governed Sources.",
        limitations=("Requires existing Databricks OAuth setup; semantic modeling remains beta.",),
        resource_picker_type="warehouse_catalog_picker",
        modeling_modes=("warehouse",),
        readiness_gates=WAREHOUSE_BETA_GATES,
        entry_kind="embedded_flow",
    ),
    _planned(
        id="aliyun_oss",
        category="object_storage",
        display_name="阿里云 OSS",
        icon="oss",
        auth_mode="access_key",
        capabilities=("object_storage_contract",),
        planned_adapter="object_storage",
    ),
    _planned(
        id="tencent_cos",
        category="object_storage",
        display_name="腾讯云 COS",
        icon="cos",
        auth_mode="access_key",
        capabilities=("object_storage_contract",),
        planned_adapter="object_storage",
    ),
    _planned(
        id="huawei_obs",
        category="object_storage",
        display_name="华为云 OBS",
        icon="obs",
        auth_mode="access_key",
        capabilities=("object_storage_contract",),
        planned_adapter="object_storage",
    ),
    _planned(
        id="minio_s3",
        category="object_storage",
        display_name="MinIO / S3 Compatible",
        icon="s3",
        auth_mode="access_key",
        capabilities=("object_storage_contract",),
        planned_adapter="object_storage",
    ),
    _planned(
        id="dingtalk_docs",
        category="documents",
        display_name="钉钉文档 / 表格 / 宜搭",
        icon="dingtalk",
        auth_mode="oauth",
        capabilities=("oauth_picker_contract",),
        planned_adapter="oauth_picker",
    ),
    _planned(
        id="tencent_docs",
        category="documents",
        display_name="腾讯文档",
        icon="tencent-docs",
        auth_mode="oauth",
        capabilities=("oauth_picker_contract",),
        planned_adapter="oauth_picker",
    ),
    _planned(
        id="oceanbase",
        category="databases",
        display_name="OceanBase",
        icon="database",
        auth_mode="connection_string",
        capabilities=("mysql_protocol_planned",),
    ),
    _planned(
        id="clickhouse",
        category="databases",
        display_name="ClickHouse",
        icon="database",
        auth_mode="connection_string",
        capabilities=("sql_dialect_planned",),
    ),
    _planned(
        id="volcengine_las",
        category="data_lake",
        display_name="火山引擎 LAS",
        icon="lake",
        auth_mode="access_key",
    ),
    _planned(id="kafka", category="messages", display_name="Kafka", icon="message", auth_mode="connection_string"),
    _planned(
        id="elasticsearch",
        category="search",
        display_name="Elasticsearch / OpenSearch",
        icon="search",
        auth_mode="connection_string",
    ),
)


def list_connector_definitions() -> list[dict[str, Any]]:
    return [definition.to_payload() for definition in CONNECTOR_CATALOG]


def get_connector_definition(provider: str) -> ConnectorDefinition | None:
    return next((item for item in CONNECTOR_CATALOG if item.id == provider), None)


def connector_catalog_summary() -> dict[str, int | str]:
    counts = {"ready": 0, "beta": 0, "planned": 0, "blocked": 0}
    for definition in CONNECTOR_CATALOG:
        if definition.availability == "beta":
            counts["beta"] += 1
        elif definition.availability == "planned":
            counts["planned"] += 1
    return {**counts, "overall_status": "PARTIAL"}
