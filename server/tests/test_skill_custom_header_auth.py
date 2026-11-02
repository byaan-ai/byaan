"""Tests for custom-skill API auth headers, including the custom header name auth type."""

import pytest
from pydantic import ValidationError

from server.schemas.custom_skills import CustomSkillApiConfig
from server.services.skill_discovery import SkillApiConfig, _parse_skill_config
from server.tools.skill_executor import _build_custom_skill_config, _build_request_headers


class TestSkillApiConfigAuthHeaders:
    def test_bearer(self):
        config = SkillApiConfig(base_url="https://api.example.com", auth_type="bearer")
        assert config.auth_headers("k1") == {"Authorization": "Bearer k1"}

    def test_raw_authorization(self):
        config = SkillApiConfig(base_url="https://api.example.com", auth_type="custom")
        assert config.auth_headers("k1") == {"Authorization": "k1"}

    def test_custom_header_name(self):
        config = SkillApiConfig(base_url="https://api.example.com", auth_type="header", auth_header="xi-api-key")
        assert config.auth_headers("k1") == {"xi-api-key": "k1"}

    def test_header_type_without_name_sends_no_auth(self):
        config = SkillApiConfig(base_url="https://api.example.com", auth_type="header")
        assert config.auth_headers("k1") == {}

    def test_no_key_sends_no_auth(self):
        config = SkillApiConfig(base_url="https://api.example.com", auth_type="header", auth_header="xi-api-key")
        assert config.auth_headers("") == {}

    def test_frontmatter_header(self):
        config = _parse_skill_config(
            {
                "name": "voice",
                "api": {"base_url": "https://api.example.com", "auth": {"type": "header", "header": "x-api-key"}},
            },
            "",
        )
        assert config.api.auth_type == "header"
        assert config.api.auth_header == "x-api-key"


class TestCustomSkillRequestHeaders:
    def _skill_data(self, **overrides):
        data = {
            "name": "elevenlabs",
            "description": "",
            "api_base_url": "https://api.elevenlabs.io",
            "api_auth_type": "header",
            "api_auth_header": "xi-api-key",
        }
        data.update(overrides)
        return data

    def test_custom_header_skill_sends_key_under_header(self):
        config = _build_custom_skill_config(self._skill_data())
        headers = _build_request_headers(config, {"api_key": "sk_123"})
        assert headers["xi-api-key"] == "sk_123"
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_existing_bearer_skill_unchanged(self):
        config = _build_custom_skill_config(self._skill_data(api_auth_type="bearer", api_auth_header=None))
        headers = _build_request_headers(config, {"api_key": "sk_123"})
        assert headers["Authorization"] == "Bearer sk_123"

    def test_existing_raw_skill_unchanged(self):
        config = _build_custom_skill_config(self._skill_data(api_auth_type="custom", api_auth_header=None))
        headers = _build_request_headers(config, {"api_key": "sk_123"})
        assert headers["Authorization"] == "sk_123"


class TestCustomSkillApiConfigValidation:
    def test_header_type_requires_header_name(self):
        with pytest.raises(ValidationError):
            CustomSkillApiConfig(api_base_url="https://api.example.com", api_auth_type="header")

    @pytest.mark.parametrize("name", ["xi api key", "x-key:", "x-api\nkey"])
    def test_rejects_invalid_header_names(self, name):
        with pytest.raises(ValidationError):
            CustomSkillApiConfig(api_base_url="https://api.example.com", api_auth_type="header", api_auth_header=name)

    @pytest.mark.parametrize("name", ["Host", "content-type", "Content-Length"])
    def test_rejects_reserved_headers(self, name):
        with pytest.raises(ValidationError):
            CustomSkillApiConfig(api_base_url="https://api.example.com", api_auth_type="header", api_auth_header=name)

    def test_strips_header_name(self):
        config = CustomSkillApiConfig(
            api_base_url="https://api.example.com", api_auth_type="header", api_auth_header="  xi-api-key "
        )
        assert config.api_auth_header == "xi-api-key"

    def test_header_name_cleared_for_other_auth_types(self):
        config = CustomSkillApiConfig(
            api_base_url="https://api.example.com", api_auth_type="bearer", api_auth_header="xi-api-key"
        )
        assert config.api_auth_header == ""


@pytest.mark.asyncio
async def test_custom_skill_api_roundtrip_keeps_header_and_hides_key(test_client):
    resp = await test_client.post(
        "/api/custom-skills",
        json={
            "name": "elevenlabs",
            "description": "Read ElevenLabs agents and conversations",
            "instructions": "Use GET endpoints only.",
            "api_config": {
                "api_base_url": "https://api.elevenlabs.io",
                "api_auth_type": "header",
                "api_auth_header": "xi-api-key",
                "api_key": "sk_secret_value",
            },
        },
    )
    assert resp.status_code == 200, resp.text
    skill = resp.json()["data"]
    assert skill["api_auth_type"] == "header"
    assert skill["api_auth_header"] == "xi-api-key"
    assert skill["has_credentials"] is True
    assert "sk_secret_value" not in resp.text

    resp = await test_client.put(
        f"/api/custom-skills/{skill['id']}",
        json={"remove_api_config": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["api_auth_header"] is None


@pytest.mark.asyncio
async def test_custom_skill_api_rejects_missing_header_name(test_client):
    resp = await test_client.post(
        "/api/custom-skills",
        json={
            "name": "broken",
            "description": "Missing header name",
            "instructions": "x",
            "api_config": {"api_base_url": "https://api.example.com", "api_auth_type": "header", "api_key": "k"},
        },
    )
    assert resp.status_code == 422
