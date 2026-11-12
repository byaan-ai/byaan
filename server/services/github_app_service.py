from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from server.repositories.skill_credentials import SkillCredentialRepository
from server.services import github_service
from server.services.github_service import GITHUB_API_BASE, GITHUB_HEADERS
from server.services.handoff_git import FileChange
from server.utils.config_loader import get_auth_secret
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

APP_SKILL = "github_app"
STATE_AUDIENCE = "byaan-github-app-manifest"
MAX_API_COMMIT_FILES = 300
_token_cache: dict[tuple[int, str], tuple[str, dict[str, str], float]] = {}


class GitHubAppError(Exception):
    pass


@dataclass(frozen=True)
class AppIdentity:
    app_id: int
    slug: str
    bot_user_id: int | None

    @property
    def author_name(self) -> str:
        return f"{self.slug}[bot]"

    @property
    def author_email(self) -> str:
        prefix = f"{self.bot_user_id}+" if self.bot_user_id else ""
        return f"{prefix}{self.slug}[bot]@users.noreply.github.com"


@dataclass(frozen=True)
class HandoffGitHubAuth:
    token: str
    kind: str
    app: AppIdentity | None = None
    app_permissions: dict[str, str] = field(default_factory=dict)


async def can_push(auth: HandoffGitHubAuth, github: Any, repo_full_name: str) -> bool:
    """App tokens carry the installation's permissions; GitHub's per-repo flags only describe user tokens."""
    if auth.kind == "app":
        return auth.app_permissions.get("contents") == "write"
    return await github.can_push(repo_full_name)


def manifest_for(name: str, base_url: str) -> dict[str, Any]:
    return {
        "name": name,
        "url": base_url or "https://byaan.ai",
        "redirect_url": f"{base_url}/api/github/app/callback",
        "public": False,
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
            "issues": "write",
            "checks": "read",
            "statuses": "read",
            "actions": "read",
            "metadata": "read",
        },
        "default_events": [],
    }


def sign_state(tenant_id: UUID, user_id: UUID) -> str:
    payload = {"aud": STATE_AUDIENCE, "tid": str(tenant_id), "uid": str(user_id), "exp": int(time.time()) + 900}
    return jwt.encode(payload, get_auth_secret(), algorithm="HS256")


def verify_state(state: str) -> tuple[UUID, UUID]:
    try:
        payload = jwt.decode(state, get_auth_secret(), algorithms=["HS256"], audience=STATE_AUDIENCE)
    except jwt.PyJWTError as exc:
        raise GitHubAppError("Invalid or expired GitHub App setup state") from exc
    return UUID(payload["tid"]), UUID(payload["uid"])


async def convert_manifest(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{GITHUB_API_BASE}/app-manifests/{code}/conversions", headers=GITHUB_HEADERS)
    if response.status_code != 201:
        raise GitHubAppError(f"GitHub App creation failed: HTTP {response.status_code}")
    return response.json()


async def save_app(tenant_id: UUID, user_id: UUID, data: dict[str, Any], session: AsyncSession) -> None:
    await SkillCredentialRepository(session).upsert(
        skill_name=APP_SKILL,
        credentials={
            "app_id": data["id"],
            "slug": data["slug"],
            "private_key": data["pem"],
            "client_id": data.get("client_id"),
            "html_url": data.get("html_url"),
        },
        tenant_id=tenant_id,
        user_id=None,
        scope="org",
        created_by=user_id,
    )


async def load_app(tenant_id: UUID, session: AsyncSession) -> dict[str, Any] | None:
    repo = SkillCredentialRepository(session)
    credential = await repo.get_by_skill(APP_SKILL, tenant_id, None, scope="org")
    if not credential:
        return None
    return await repo.get_decrypted_credentials(credential)


async def delete_app(tenant_id: UUID, session: AsyncSession) -> bool:
    return await SkillCredentialRepository(session).delete_by_skill(APP_SKILL, tenant_id, user_id=None, scope="org")


def _app_jwt(app: dict[str, Any]) -> str:
    now = int(time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(app["app_id"])}, app["private_key"], "RS256")


async def _app_request(app: dict[str, Any], method: str, path: str, **kwargs: Any) -> httpx.Response:
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {_app_jwt(app)}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.request(method, f"{GITHUB_API_BASE}{path}", headers=headers, **kwargs)


async def installation_token(app: dict[str, Any], repo_full_name: str) -> tuple[str, dict[str, str]] | None:
    """A token scoped to one repo, plus the permissions the installation granted it."""
    response = await _app_request(app, "GET", f"/repos/{repo_full_name}/installation")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise GitHubAppError(f"Installation lookup failed: HTTP {response.status_code}")
    installation_id = response.json()["id"]

    repo_name = repo_full_name.split("/", 1)[1]
    cache_key = (installation_id, repo_name)
    cached = _token_cache.get(cache_key)
    if cached and cached[2] - time.time() > 300:
        return cached[0], cached[1]

    response = await _app_request(
        app, "POST", f"/app/installations/{installation_id}/access_tokens", json={"repositories": [repo_name]}
    )
    if response.status_code != 201:
        raise GitHubAppError(f"Installation token request failed: HTTP {response.status_code}")
    data = response.json()
    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00")).timestamp()
    permissions = data.get("permissions") or {}
    for key in [key for key, entry in _token_cache.items() if entry[2] <= time.time()]:
        del _token_cache[key]
    _token_cache[cache_key] = (data["token"], permissions, expires_at)
    return data["token"], permissions


async def _bot_user_id(slug: str, token: str) -> int | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GITHUB_API_BASE}/users/{slug}[bot]", headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
        )
    return response.json().get("id") if response.status_code == 200 else None


async def app_status(tenant_id: UUID, session: AsyncSession) -> dict[str, Any]:
    app = await load_app(tenant_id, session)
    if not app:
        return {"configured": False}
    return {
        "configured": True,
        "slug": app.get("slug"),
        "install_url": f"{app.get('html_url')}/installations/new" if app.get("html_url") else None,
    }


async def resolve_auth(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    repo_full_name: str,
    requester_id: UUID | None,
    origin: str,
) -> HandoffGitHubAuth | None:
    app = await load_app(tenant_id, session)
    if app:
        try:
            installation = await installation_token(app, repo_full_name)
        except (GitHubAppError, httpx.HTTPError) as exc:
            logger.warning(f"[HANDOFF] GitHub App token unavailable for {repo_full_name}: {exc}")
            installation = None
        if installation:
            token, permissions = installation
            identity = AppIdentity(
                app_id=int(app["app_id"]), slug=app["slug"], bot_user_id=await _bot_user_id(app["slug"], token)
            )
            return HandoffGitHubAuth(token=token, kind="app", app=identity, app_permissions=permissions)

    if origin == "notebook" and requester_id:
        token = await github_service.get_github_token(tenant_id, requester_id, session)
        if token:
            return HandoffGitHubAuth(token=token, kind="user")
    token = await github_service.get_org_github_token(tenant_id, session)
    if token:
        return HandoffGitHubAuth(token=token, kind="org")
    if requester_id:
        token = await github_service.get_github_token(tenant_id, requester_id, session)
        if token:
            return HandoffGitHubAuth(token=token, kind="user")
    for connector_id in await repo_connector_ids(session, tenant_id, repo_full_name):
        if connector_id == requester_id:
            continue
        token = await github_service.get_github_token(tenant_id, connector_id, session)
        if token:
            return HandoffGitHubAuth(token=token, kind="user")
    return None


async def repo_connector_ids(session: AsyncSession, tenant_id: UUID, repo_full_name: str) -> list[UUID]:
    """Accounts that connected this repo; their GitHub access covers handoffs nobody else can authorize."""
    from sqlalchemy import select

    from server.models.github_repository import GitHubRepository

    result = await session.execute(
        select(GitHubRepository.user_id)
        .where(
            GitHubRepository.tenant_id == tenant_id,
            GitHubRepository.repo_full_name == repo_full_name,
            GitHubRepository.is_active.is_(True),
        )
        .order_by(GitHubRepository.created_at)
    )
    return list(dict.fromkeys(result.scalars().all()))


async def create_signed_commit(
    *,
    token: str,
    repo_full_name: str,
    branch: str,
    parent_sha: str,
    worktree: Path,
    changes: list[FileChange],
    message: str,
    branch_exists: bool,
) -> str | None:
    if not branch.startswith("byaan/"):
        raise GitHubAppError(f"Refusing to commit to non-Byaan branch {branch!r}")
    if len(changes) > MAX_API_COMMIT_FILES:
        return None
    headers = {**GITHUB_HEADERS, "Authorization": f"Bearer {token}"}
    base = f"{GITHUB_API_BASE}/repos/{repo_full_name}/git"

    async with httpx.AsyncClient(timeout=60.0) as client:

        async def call(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            response = await client.request(method, f"{base}{path}", headers=headers, json=payload)
            if response.status_code >= 400:
                raise GitHubAppError(f"Git Data API {path} failed: HTTP {response.status_code}")
            return response.json()

        parent = await call("GET", f"/commits/{parent_sha}")
        tree_entries = []
        for change in changes:
            target = worktree / change.path
            if change.status == "D":
                tree_entries.append({"path": change.path, "mode": "100644", "type": "blob", "sha": None})
                continue
            if target.is_symlink():
                content, mode = os.readlink(target).encode(), "120000"
            else:
                content = target.read_bytes()
                mode = "100755" if os.access(target, os.X_OK) else "100644"
            blob = await call("POST", "/blobs", {"content": base64.b64encode(content).decode(), "encoding": "base64"})
            tree_entries.append({"path": change.path, "mode": mode, "type": "blob", "sha": blob["sha"]})

        tree = await call("POST", "/trees", {"base_tree": parent["tree"]["sha"], "tree": tree_entries})
        commit = await call("POST", "/commits", {"message": message, "tree": tree["sha"], "parents": [parent_sha]})
        if branch_exists:
            await call("PATCH", f"/refs/heads/{branch}", {"sha": commit["sha"], "force": False})
        else:
            await call("POST", "/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        return commit["sha"]
