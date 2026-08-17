from __future__ import annotations

from server.services.connector_catalog import get_connector_definition, list_connector_definitions


def test_connector_catalog_is_partial_beta_not_available() -> None:
    payloads = list_connector_definitions()
    by_id = {item["id"]: item for item in payloads}

    for connector_id in ("local_files", "web", "feishu", "sql_databases", "volcengine_tos", "databricks"):
        definition = by_id[connector_id]
        assert definition["availability"] == "beta"
        assert definition["status"] == "beta"
        assert definition["readiness_gates"]
        assert {gate["status"] for gate in definition["readiness_gates"]}.issubset(
            {"partial", "missing", "not_applicable"}
        )

    assert get_connector_definition("feishu") is not None
    assert by_id["aliyun_oss"]["availability"] == "planned"
    assert "available" not in {item["availability"] for item in payloads}
