from __future__ import annotations

from uuid import uuid4

import pytest

from server.services import github_app_service as app_service

pytestmark = pytest.mark.handoff


def test_state_roundtrip_and_tamper():
    tid, uid = uuid4(), uuid4()
    state = app_service.sign_state(tid, uid)
    assert app_service.verify_state(state) == (tid, uid)
    with pytest.raises(app_service.GitHubAppError):
        app_service.verify_state(state[:-2] + "xx")


def test_manifest_has_no_dangerous_permissions():
    manifest = app_service.manifest_for("byaan-test", "https://byaan.example")
    permissions = manifest["default_permissions"]
    assert permissions["contents"] == "write"
    assert "administration" not in permissions
    assert "workflows" not in permissions
    assert "hook_attributes" not in manifest


def test_app_identity_author():
    identity = app_service.AppIdentity(app_id=1, slug="byaan-acme", bot_user_id=42)
    assert identity.author_name == "byaan-acme[bot]"
    assert identity.author_email == "42+byaan-acme[bot]@users.noreply.github.com"


@pytest.mark.parametrize(
    ("origin", "user_token", "org_token", "expected"),
    [
        ("notebook", "u", "o", ("u", "user")),
        ("slack", "u", "o", ("o", "org")),
        ("slack", "u", None, ("u", "user")),
        ("notebook", None, None, None),
    ],
)
async def test_resolve_auth_fallback_order(monkeypatch, origin, user_token, org_token, expected):
    async def no_app(*_args, **_kwargs):
        return None

    async def user(*_args, **_kwargs):
        return user_token

    async def org(*_args, **_kwargs):
        return org_token

    async def no_connectors(*_args, **_kwargs):
        return []

    monkeypatch.setattr(app_service, "load_app", no_app)
    monkeypatch.setattr(app_service, "repo_connector_ids", no_connectors)
    monkeypatch.setattr(app_service.github_service, "get_github_token", user)
    monkeypatch.setattr(app_service.github_service, "get_org_github_token", org)

    auth = await app_service.resolve_auth(
        None, tenant_id=uuid4(), repo_full_name="acme/api", requester_id=uuid4(), origin=origin
    )
    assert (None if auth is None else (auth.token, auth.kind)) == expected


async def test_resolve_auth_falls_back_to_the_account_that_connected_the_repo(monkeypatch):
    connector = uuid4()

    async def no_app(*_args, **_kwargs):
        return None

    async def no_org(*_args, **_kwargs):
        return None

    async def connectors(*_args, **_kwargs):
        return [connector]

    async def token_for(_tenant_id, user_id, _session):
        return "connector-token" if user_id == connector else None

    monkeypatch.setattr(app_service, "load_app", no_app)
    monkeypatch.setattr(app_service, "repo_connector_ids", connectors)
    monkeypatch.setattr(app_service.github_service, "get_github_token", token_for)
    monkeypatch.setattr(app_service.github_service, "get_org_github_token", no_org)

    auth = await app_service.resolve_auth(
        None, tenant_id=uuid4(), repo_full_name="acme/api", requester_id=None, origin="slack"
    )
    assert (auth.token, auth.kind) == ("connector-token", "user")


async def test_resolve_auth_prefers_app(monkeypatch):
    async def app(*_args, **_kwargs):
        return {"app_id": 9, "slug": "byaan-acme", "private_key": "k"}

    async def token(*_args, **_kwargs):
        return "inst-token", {"contents": "write"}

    async def bot_id(*_args, **_kwargs):
        return 77

    monkeypatch.setattr(app_service, "load_app", app)
    monkeypatch.setattr(app_service, "installation_token", token)
    monkeypatch.setattr(app_service, "_bot_user_id", bot_id)

    auth = await app_service.resolve_auth(
        None, tenant_id=uuid4(), repo_full_name="acme/api", requester_id=None, origin="slack"
    )
    assert auth.kind == "app"
    assert auth.app.author_email.startswith("77+")


async def test_push_check_uses_the_app_installation_permissions():
    write = app_service.HandoffGitHubAuth(token="t", kind="app", app_permissions={"contents": "write"})
    read = app_service.HandoffGitHubAuth(token="t", kind="app", app_permissions={"contents": "read"})
    assert await app_service.can_push(write, None, "acme/api") is True
    assert await app_service.can_push(read, None, "acme/api") is False
