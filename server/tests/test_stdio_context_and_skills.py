"""Tests for self-hosted stdio identity resolution, AWS IAM-role skill auth, and skill field parsing."""

import json
import uuid

import pytest

from server.mcp import stdio_server
from server.models.tenant import Tenant
from server.models.tenant_member import TenantMember
from server.models.user import User
from server.services.skill_discovery import SkillDiscovery, _parse_skill_config
from server.tools.skill_executor import _execute_aws_request


async def _create_user(session, email="eng@byaan.ai", is_active=True):
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="fakehash",
        is_active=is_active,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _create_tenant(session, owner, slug):
    tenant = Tenant(id=uuid.uuid4(), name=slug, slug=slug, owner_id=owner.id)
    session.add(tenant)
    await session.flush()
    return tenant


class TestSelfHostedStdioContext:
    async def test_requires_byaan_mcp_user(self, test_session, monkeypatch):
        monkeypatch.delenv("BYAAN_MCP_USER", raising=False)
        with pytest.raises(Exception, match="BYAAN_MCP_USER"):
            await stdio_server._resolve_self_hosted_context(test_session)

    async def test_unknown_email_rejected(self, test_session, monkeypatch):
        monkeypatch.setenv("BYAAN_MCP_USER", "ghost@byaan.ai")
        with pytest.raises(Exception, match="does not match an active user"):
            await stdio_server._resolve_self_hosted_context(test_session)

    async def test_inactive_user_rejected(self, test_session, monkeypatch):
        user = await _create_user(test_session, is_active=False)
        await _create_tenant(test_session, user, "acme")
        monkeypatch.setenv("BYAAN_MCP_USER", user.email)
        with pytest.raises(Exception, match="does not match an active user"):
            await stdio_server._resolve_self_hosted_context(test_session)

    async def test_single_tenant_resolves(self, test_session, monkeypatch):
        user = await _create_user(test_session)
        tenant = await _create_tenant(test_session, user, "acme")
        monkeypatch.setenv("BYAAN_MCP_USER", user.email.upper())
        monkeypatch.delenv("BYAAN_MCP_TENANT", raising=False)

        tenant_id, user_id = await stdio_server._resolve_self_hosted_context(test_session)
        assert tenant_id == tenant.id
        assert user_id == user.id

    async def test_membership_tenant_resolves(self, test_session, monkeypatch):
        owner = await _create_user(test_session, email="owner@byaan.ai")
        tenant = await _create_tenant(test_session, owner, "acme")
        member = await _create_user(test_session, email="member@byaan.ai")
        test_session.add(TenantMember(user_id=member.id, tenant_id=tenant.id, role="member"))
        await test_session.flush()
        monkeypatch.setenv("BYAAN_MCP_USER", member.email)
        monkeypatch.delenv("BYAAN_MCP_TENANT", raising=False)

        tenant_id, user_id = await stdio_server._resolve_self_hosted_context(test_session)
        assert tenant_id == tenant.id
        assert user_id == member.id

    async def test_multi_tenant_requires_slug(self, test_session, monkeypatch):
        user = await _create_user(test_session)
        await _create_tenant(test_session, user, "acme")
        await _create_tenant(test_session, user, "globex")
        monkeypatch.setenv("BYAAN_MCP_USER", user.email)
        monkeypatch.delenv("BYAAN_MCP_TENANT", raising=False)

        with pytest.raises(Exception, match="BYAAN_MCP_TENANT"):
            await stdio_server._resolve_self_hosted_context(test_session)

        monkeypatch.setenv("BYAAN_MCP_TENANT", "globex")
        tenant_id, _ = await stdio_server._resolve_self_hosted_context(test_session)
        result = await test_session.get(Tenant, tenant_id)
        assert result.slug == "globex"

    async def test_wrong_tenant_slug_rejected(self, test_session, monkeypatch):
        user = await _create_user(test_session)
        await _create_tenant(test_session, user, "acme")
        monkeypatch.setenv("BYAAN_MCP_USER", user.email)
        monkeypatch.setenv("BYAAN_MCP_TENANT", "nonexistent")

        with pytest.raises(Exception, match="no access to tenant"):
            await stdio_server._resolve_self_hosted_context(test_session)

    async def test_local_mode_picks_latest_tenant(self, test_session, monkeypatch):
        monkeypatch.setattr(stdio_server, "is_self_hosted", lambda: False)
        user = await _create_user(test_session)
        tenant = await _create_tenant(test_session, user, "local")

        tenant_id, user_id = await stdio_server._resolve_local_context(test_session)
        assert tenant_id == tenant.id
        assert user_id == user.id

    async def test_local_mode_sets_tenant_context(self, test_session, monkeypatch):
        from server.auth.tenant_context import get_tenant_id

        monkeypatch.setattr(stdio_server, "is_self_hosted", lambda: False)
        user = await _create_user(test_session)
        tenant = await _create_tenant(test_session, user, "local")

        await stdio_server._resolve_local_context(test_session)
        assert get_tenant_id() == tenant.id


class TestAwsIamRoleAuth:
    def _cloudwatch_config(self):
        config = SkillDiscovery.get_skill_config("cloudwatch_logs")
        assert config is not None
        return config

    async def test_access_keys_mode_requires_keys(self):
        config = self._cloudwatch_config()
        response, error = await _execute_aws_request(config, {"auth_mode": "access_keys"}, "DescribeLogGroups", {})
        assert response is None
        assert "required" in json.loads(error)["error"]

    async def test_iam_role_mode_uses_default_chain(self, monkeypatch):
        import boto3

        config = self._cloudwatch_config()
        captured = {}

        class FakeClient:
            def describe_log_groups(self, **params):
                return {"logGroups": [], "ResponseMetadata": {}}

        def fake_client(service_name, **kwargs):
            captured["service"] = service_name
            captured["kwargs"] = kwargs
            return FakeClient()

        monkeypatch.setattr(boto3, "client", fake_client)
        credentials = {"auth_mode": "iam_role", "aws_region": "eu-west-1"}
        response, error = await _execute_aws_request(config, credentials, "DescribeLogGroups", {})

        assert error is None
        assert response == {"logGroups": []}
        assert captured["service"] == "logs"
        assert captured["kwargs"] == {"region_name": "eu-west-1"}
        assert "aws_access_key_id" not in captured["kwargs"]

    async def test_iam_role_mode_no_credentials_error(self, monkeypatch):
        import boto3
        from botocore.exceptions import NoCredentialsError

        config = self._cloudwatch_config()

        def fake_client(service_name, **kwargs):
            raise NoCredentialsError()

        monkeypatch.setattr(boto3, "client", fake_client)
        response, error = await _execute_aws_request(config, {"auth_mode": "iam_role"}, "DescribeLogGroups", {})

        assert response is None
        assert "IAM role" in json.loads(error)["error"]


class TestSkillFieldParsing:
    def test_select_field_attributes_parsed(self):
        frontmatter = {
            "name": "demo",
            "credentials": [
                {
                    "key": "auth_mode",
                    "label": "Authentication",
                    "type": "select",
                    "default": "a",
                    "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
                },
                {
                    "key": "secret",
                    "label": "Secret",
                    "depends_on": {"key": "auth_mode", "value": "a"},
                },
            ],
        }
        config = _parse_skill_config(frontmatter, "docs")
        auth_mode, secret = config.credentials
        assert auth_mode.type == "select"
        assert auth_mode.default == "a"
        assert auth_mode.options == [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]
        assert secret.depends_on == {"key": "auth_mode", "value": "a"}
        assert secret.type == "text"

    def test_cloudwatch_skill_has_auth_mode(self):
        config = SkillDiscovery.get_skill_config("cloudwatch_logs")
        fields = {c.key: c for c in config.credentials}
        assert fields["auth_mode"].type == "select"
        assert fields["auth_mode"].default == "access_keys"
        assert {o["value"] for o in fields["auth_mode"].options} == {"access_keys", "iam_role"}
        assert fields["aws_access_key_id"].depends_on == {"key": "auth_mode", "value": "access_keys"}
        assert fields["aws_secret_access_key"].depends_on == {"key": "auth_mode", "value": "access_keys"}

    def test_sentry_skill_discovered(self):
        config = SkillDiscovery.get_skill_config("sentry")
        assert config is not None
        assert config.api.base_url == "https://sentry.io/api/0"
        assert config.api.auth_type == "bearer"
        keys = [c.key for c in config.credentials]
        assert keys == ["api_key", "organization_slug", "project_slugs"]
        assert config.credentials[2].optional is True
        assert "Organization Tokens" in config.docs


class TestMCPRunContextCredentials:
    async def test_create_run_context_exposes_flat_credential_keys(self, monkeypatch):
        from unittest.mock import AsyncMock

        from server.mcp import tool_wrappers

        enabled = {
            "sentry:user": {"skill_name": "sentry", "scope": "user", "credentials": {"api_key": "sntryu_x"}},
            "sentry:org": {"skill_name": "sentry", "scope": "org", "credentials": {"api_key": "sntryu_y"}},
        }
        monkeypatch.setattr(tool_wrappers, "_load_custom_skills_for_mcp", AsyncMock(return_value={}))
        monkeypatch.setattr(
            tool_wrappers, "_load_enabled_skills_for_mcp", AsyncMock(return_value=(enabled, ["sentry"]))
        )

        ctx = await tool_wrappers.create_run_context(uuid.uuid4(), uuid.uuid4(), None)

        assert ctx.context["sentry:user_credentials"] == {"api_key": "sntryu_x"}
        assert ctx.context["sentry:org_credentials"] == {"api_key": "sntryu_y"}

        from server.tools.skill_executor import _get_credentials_for_skill

        assert _get_credentials_for_skill(ctx, "sentry", "user") == {"api_key": "sntryu_x"}
        assert _get_credentials_for_skill(ctx, "sentry", "org") == {"api_key": "sntryu_y"}
