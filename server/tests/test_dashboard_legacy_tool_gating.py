from __future__ import annotations

from uuid import uuid4

import pytest

from server.models.dashboard import Dashboard
from server.tools.agentic import _ensure_legacy_html_dashboard, _load_dashboard_body
from server.utils.dashboard_editing import DashboardEditError


class _DummyCtx:
    def __init__(self, context: dict):
        self.context = context


class _DummyDashboardRepo:
    def __init__(self, dashboard=None):
        self.dashboard = dashboard

    async def get_version(self, notebook_id, version_num):
        return self.dashboard

    async def get_latest_version(self, notebook_id):
        return self.dashboard


def _dashboard(**overrides) -> Dashboard:
    payload = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "notebook_id": uuid4(),
        "version_num": 1,
        "html_content": "<html>legacy</html>",
        "status": "legacy_unstructured",
        "migration_state": "legacy_unstructured",
    }
    payload.update(overrides)
    return Dashboard(**payload)


def test_legacy_html_guard_allows_legacy_unstructured_dashboard() -> None:
    _ensure_legacy_html_dashboard(_dashboard(asset_id=uuid4()))


def test_legacy_html_guard_blocks_structured_manifest_dashboard() -> None:
    dashboard = _dashboard(
        asset_id=uuid4(),
        manifest_schema_version="dashboard.manifest.v1",
        manifest_json={"schema_version": "dashboard.manifest.v1"},
        status="published",
        migration_state="new_structured",
    )

    with pytest.raises(DashboardEditError, match="Legacy HTML tools are deprecated"):
        _ensure_legacy_html_dashboard(dashboard)


@pytest.mark.asyncio
async def test_load_dashboard_body_blocks_structured_latest_dashboard_before_html_edit() -> None:
    dashboard = _dashboard(
        asset_id=uuid4(),
        manifest_schema_version="dashboard.manifest.v1",
        manifest_json={"schema_version": "dashboard.manifest.v1"},
        status="published",
        migration_state="new_structured",
    )

    with pytest.raises(DashboardEditError, match="base_etag JSON Patch"):
        await _load_dashboard_body(
            _DummyCtx({"notebook_id": str(dashboard.notebook_id)}),
            _DummyDashboardRepo(dashboard),
        )


@pytest.mark.asyncio
async def test_load_dashboard_body_still_returns_starter_template_when_no_legacy_dashboard_exists() -> None:
    html, version = await _load_dashboard_body(
        _DummyCtx({"notebook_id": str(uuid4())}),
        _DummyDashboardRepo(None),
    )

    assert version is None
    assert "No content yet" in html
