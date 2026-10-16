from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, Text, create_engine, inspect, text

from server.db.base import Base
from server.migrations.versions import add_governed_dashboard_assets, backfill_legacy_dashboard_assets
from server.models.dashboard import Dashboard, DashboardAsset, DashboardAuditEvent, DashboardRun


def _create_minimal_dashboard_legacy_schema(engine) -> None:
    metadata = MetaData()
    Table("users", metadata, Column("id", String(36), primary_key=True))
    Table(
        "tenants",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("owner_id", String(36), ForeignKey("users.id"), nullable=False),
    )
    Table(
        "notebooks",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
        Column("created_by", String(36), ForeignKey("users.id"), nullable=True),
        Column("notebook_name", String(255), nullable=True),
    )
    Table(
        "dashboards",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(36), ForeignKey("tenants.id"), nullable=False),
        Column("notebook_id", String(36), ForeignKey("notebooks.id"), nullable=False),
        Column("version_num", Integer, nullable=False),
        Column("html_content", Text, nullable=False),
    )
    metadata.create_all(engine)


def test_dashboard_persistence_models_are_registered() -> None:
    assert DashboardAsset.__tablename__ in Base.metadata.tables
    assert DashboardRun.__tablename__ in Base.metadata.tables
    assert DashboardAuditEvent.__tablename__ in Base.metadata.tables

    dashboard_columns = {column.name for column in Dashboard.__table__.columns}
    assert {
        "asset_id",
        "manifest_schema_version",
        "manifest_json",
        "content_hash",
        "status",
        "pinned_model_versions_json",
        "pinned_source_snapshots_json",
        "validation_result_json",
        "migration_state",
        "is_published_immutable",
    }.issubset(dashboard_columns)


def test_governed_dashboard_migration_upgrade_and_downgrade_sqlite() -> None:
    engine = create_engine("sqlite:///:memory:")
    _create_minimal_dashboard_legacy_schema(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_op = add_governed_dashboard_assets.op
        add_governed_dashboard_assets.op = operations
        try:
            add_governed_dashboard_assets.upgrade()
            inspector = inspect(connection)
            assert "dashboard_assets" in inspector.get_table_names()
            assert "dashboard_runs" in inspector.get_table_names()
            assert "dashboard_audit_events" in inspector.get_table_names()

            dashboard_columns = {column["name"] for column in inspector.get_columns("dashboards")}
            assert {
                "asset_id",
                "manifest_json",
                "status",
                "migration_state",
                "is_published_immutable",
            }.issubset(dashboard_columns)

            add_governed_dashboard_assets.downgrade()
            inspector = inspect(connection)
            assert "dashboard_assets" not in inspector.get_table_names()
            assert "dashboard_runs" not in inspector.get_table_names()
            assert "dashboard_audit_events" not in inspector.get_table_names()
            dashboard_columns = {column["name"] for column in inspector.get_columns("dashboards")}
            assert "asset_id" not in dashboard_columns
            assert "html_content" in dashboard_columns
        finally:
            add_governed_dashboard_assets.op = original_op


def test_legacy_dashboard_backfill_links_existing_rows_without_html_parsing() -> None:
    engine = create_engine("sqlite:///:memory:")
    _create_minimal_dashboard_legacy_schema(engine)

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id) VALUES ('user-1')"))
        connection.execute(text("INSERT INTO tenants (id, owner_id) VALUES ('tenant-1', 'user-1')"))
        connection.execute(
            text(
                "INSERT INTO notebooks (id, tenant_id, created_by, notebook_name) "
                "VALUES ('notebook-1', 'tenant-1', 'user-1', 'Legacy Revenue')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dashboards (id, tenant_id, notebook_id, version_num, html_content) "
                "VALUES ('dash-1', 'tenant-1', 'notebook-1', 1, '<html>query-a</html>')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dashboards (id, tenant_id, notebook_id, version_num, html_content) "
                "VALUES ('dash-2', 'tenant-1', 'notebook-1', 2, '<html>query-b</html>')"
            )
        )

        context = MigrationContext.configure(connection)
        operations = Operations(context)
        original_asset_op = add_governed_dashboard_assets.op
        original_backfill_op = backfill_legacy_dashboard_assets.op
        add_governed_dashboard_assets.op = operations
        backfill_legacy_dashboard_assets.op = operations
        try:
            add_governed_dashboard_assets.upgrade()
            backfill_legacy_dashboard_assets.upgrade()

            assets = connection.execute(text("SELECT * FROM dashboard_assets")).mappings().all()
            assert len(assets) == 1
            asset = assets[0]
            assert asset["slug"].startswith("legacy-notebook-1")
            assert asset["name"] == "Legacy Revenue"
            assert asset["lifecycle"] == "legacy_unstructured"
            assert asset["published_version_id"] == "dash-2"

            dashboards = (
                connection.execute(
                    text(
                        "SELECT id, asset_id, html_content, status, migration_state, manifest_json "
                        "FROM dashboards ORDER BY version_num"
                    )
                )
                .mappings()
                .all()
            )
            assert {dashboard["asset_id"] for dashboard in dashboards} == {asset["id"]}
            assert [dashboard["html_content"] for dashboard in dashboards] == [
                "<html>query-a</html>",
                "<html>query-b</html>",
            ]
            assert {dashboard["status"] for dashboard in dashboards} == {"legacy_unstructured"}
            assert {dashboard["migration_state"] for dashboard in dashboards} == {"legacy_unstructured"}
            assert all(dashboard["manifest_json"] is None for dashboard in dashboards)

            backfill_legacy_dashboard_assets.upgrade()
            assert connection.execute(text("SELECT COUNT(*) FROM dashboard_assets")).scalar_one() == 1

            backfill_legacy_dashboard_assets.downgrade()
            assert connection.execute(text("SELECT COUNT(*) FROM dashboard_assets")).scalar_one() == 0
            assert (
                connection.execute(
                    text("SELECT COUNT(*) FROM dashboards WHERE html_content LIKE '<html>query-%'")
                ).scalar_one()
                == 2
            )
        finally:
            add_governed_dashboard_assets.op = original_asset_op
            backfill_legacy_dashboard_assets.op = original_backfill_op
