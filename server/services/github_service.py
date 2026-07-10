from __future__ import annotations

import base64
import os
import secrets
from urllib.parse import urlencode
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from server.repositories.skill_credentials import SkillCredentialRepository
from server.utils.config_loader import get_email_config, get_github_oauth_config, is_self_hosted
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

_github_config = get_github_oauth_config()
GITHUB_CLIENT_ID = _github_config.get("client_id") or ""
GITHUB_CLIENT_SECRET = _github_config.get("client_secret") or ""

GITHUB_OAUTH_CLIENT_ID_KEY = "github_oauth_client_id"
GITHUB_OAUTH_CLIENT_SECRET_KEY = "github_oauth_client_secret"
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_SCOPES = "repo read:user"
REQUIRED_SCOPES = {"repo"}

CLASSIC_PAT_PREFIX = "ghp_"
FINE_GRAINED_PAT_PREFIX = "github_pat_"

AUTH_METHOD_OAUTH = "oauth"
AUTH_METHOD_PAT_CLASSIC = "pat_classic"
AUTH_METHOD_PAT_FINE_GRAINED = "pat_fine_grained"

_device_code_store: dict[str, dict] = {}  # device_code → {tenant_id, user_id}


def _get_frontend_url() -> str:
    url = os.getenv("FRONTEND_URL", "").rstrip("/")
    if url:
        return url
    if is_self_hosted():
        return get_email_config().get("frontend_url", "").rstrip("/")
    return ""


def _get_redirect_uri() -> str:
    frontend_url = _get_frontend_url()
    if frontend_url:
        return f"{frontend_url}/api/github/oauth/callback"
    return "byaan://github/callback"


_frontend_url = _get_frontend_url()
GITHUB_REDIRECT_URI = _get_redirect_uri()

_oauth_state_store: dict[str, dict] = {}

GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


async def get_github_oauth_credentials(session: AsyncSession | None = None) -> tuple[str, str]:
    if not is_self_hosted():
        return GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET

    if not session:
        return "", ""

    from server.services.crypto_service import CryptoService
    from server.services.settings import SettingsService

    client_id_setting = await SettingsService.get_setting_by_key(session, GITHUB_OAUTH_CLIENT_ID_KEY)
    if not client_id_setting:
        return "", ""

    client_id = client_id_setting.setting_value
    secret_setting = await SettingsService.get_setting_by_key(session, GITHUB_OAUTH_CLIENT_SECRET_KEY)
    if not secret_setting:
        return client_id, ""

    try:
        decrypted = await CryptoService.decrypt_config(secret_setting.setting_value, session)
        client_secret = decrypted.get("value", "")
    except Exception:
        logger.error("[GITHUB OAUTH] Failed to decrypt client secret")
        return client_id, ""

    return client_id, client_secret


async def create_auth_url(
    redirect_uri: str | None = None,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
    client_id: str | None = None,
) -> tuple[str, str]:
    redirect_uri = redirect_uri or GITHUB_REDIRECT_URI
    resolved_client_id = client_id or GITHUB_CLIENT_ID
    state = secrets.token_urlsafe(32)
    _oauth_state_store[state] = {"redirect_uri": redirect_uri, "tenant_id": tenant_id, "user_id": user_id}
    logger.info(f"[GITHUB OAUTH] Created auth URL with state: {state[:16]}...")

    params = {
        "client_id": resolved_client_id,
        "redirect_uri": redirect_uri,
        "scope": GITHUB_SCOPES,
        "state": state,
    }
    auth_url = f"{GITHUB_AUTH_URL}?{urlencode(params)}"
    return auth_url, state


async def exchange_code(
    code: str, state: str, client_id: str | None = None, client_secret: str | None = None
) -> tuple[dict, dict]:
    stored = _oauth_state_store.pop(state, None)
    if not stored:
        raise ValueError("Invalid or expired state parameter. Please restart the authentication flow.")

    resolved_client_id = client_id or GITHUB_CLIENT_ID
    resolved_client_secret = client_secret or GITHUB_CLIENT_SECRET

    logger.info("[GITHUB OAUTH] Exchanging code for token...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            GITHUB_TOKEN_URL,
            json={
                "client_id": resolved_client_id,
                "client_secret": resolved_client_secret,
                "code": code,
                "redirect_uri": stored.get("redirect_uri", GITHUB_REDIRECT_URI),
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            logger.error(f"[GITHUB OAUTH] Token exchange failed: {response.status_code}")
            raise ValueError(f"Token exchange failed: {response.text}")

        data = response.json()
        if "error" in data:
            raise ValueError(f"GitHub OAuth error: {data.get('error_description', data['error'])}")

        return data, stored


async def save_github_token(tenant_id: UUID, user_id: UUID, token_data: dict, session: AsyncSession) -> None:
    repo = SkillCredentialRepository(session)
    await repo.upsert(
        skill_name="github",
        credentials=token_data,
        tenant_id=tenant_id,
        user_id=user_id,
        scope="user",
        created_by=user_id,
    )


async def get_github_token(tenant_id: UUID, user_id: UUID, session: AsyncSession) -> str | None:
    repo = SkillCredentialRepository(session)
    cred = await repo.get_by_skill("github", tenant_id, user_id, scope="user")
    if not cred:
        return None
    decrypted = await repo.get_decrypted_credentials(cred)
    if not decrypted:
        return None
    return decrypted.get("access_token")


async def get_org_github_token(tenant_id: UUID, session: AsyncSession) -> str | None:
    """Fetch the org-scoped GitHub token, if any. Used by Slack/agent flows without a user."""
    repo = SkillCredentialRepository(session)
    cred = await repo.get_by_skill("github", tenant_id, user_id=None, scope="org")
    if not cred:
        return None
    decrypted = await repo.get_decrypted_credentials(cred)
    if not decrypted:
        return None
    return decrypted.get("access_token")


async def share_github_token_with_org(tenant_id: UUID, user_id: UUID, session: AsyncSession) -> bool:
    """Promote the user's GitHub credentials to org scope so non-user callers (Slack) can use them."""
    repo = SkillCredentialRepository(session)
    cred = await repo.share_to_org("github", tenant_id, user_id)
    return cred is not None


async def unshare_org_github_token(tenant_id: UUID, session: AsyncSession) -> bool:
    """Remove the org-scoped GitHub token. Does not touch user-scoped credentials."""
    repo = SkillCredentialRepository(session)
    return await repo.delete_by_skill("github", tenant_id, user_id=None, scope="org")


async def get_stored_auth_method(tenant_id: UUID, user_id: UUID, session: AsyncSession) -> str | None:
    repo = SkillCredentialRepository(session)
    cred = await repo.get_by_skill("github", tenant_id, user_id, scope="user")
    if not cred:
        return None
    decrypted = await repo.get_decrypted_credentials(cred)
    if not decrypted:
        return None
    token_type = (decrypted.get("token_type") or "").lower()
    if token_type == AUTH_METHOD_PAT_FINE_GRAINED:
        return AUTH_METHOD_PAT_FINE_GRAINED
    if token_type in (AUTH_METHOD_PAT_CLASSIC, "pat"):
        return AUTH_METHOD_PAT_CLASSIC
    return AUTH_METHOD_OAUTH


def detect_pat_type(token: str) -> str:
    if token.startswith(FINE_GRAINED_PAT_PREFIX):
        return AUTH_METHOD_PAT_FINE_GRAINED
    return AUTH_METHOD_PAT_CLASSIC


async def delete_github_token(tenant_id: UUID, user_id: UUID, session: AsyncSession) -> bool:
    repo = SkillCredentialRepository(session)
    return await repo.delete_by_skill("github", tenant_id, user_id, scope="user")


async def _fetch_github_user(token: str) -> tuple[dict, list[str]]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GITHUB_API_BASE}/user",
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        data = response.json()
        user = {"login": data["login"], "avatar_url": data.get("avatar_url"), "name": data.get("name")}
        scopes_header = response.headers.get("X-OAuth-Scopes", "")
        granted_scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
        return user, granted_scopes


async def get_authenticated_user(token: str) -> dict:
    user, _ = await _fetch_github_user(token)
    return user


async def validate_token_scopes(token: str) -> list[str]:
    _, granted_scopes = await _fetch_github_user(token)
    return sorted(REQUIRED_SCOPES - set(granted_scopes))


async def list_user_repos(token: str, page: int = 1, per_page: int = 30, search: str | None = None) -> list[dict]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        if search:
            user = await get_authenticated_user(token)
            response = await client.get(
                f"{GITHUB_API_BASE}/search/repositories",
                params={"q": f"user:{user['login']} {search}", "per_page": per_page, "page": page},
                headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            _check_rate_limit(response)
            return [
                {
                    "full_name": r["full_name"],
                    "private": r["private"],
                    "language": r.get("language"),
                    "description": r.get("description"),
                    "default_branch": r.get("default_branch", "main"),
                }
                for r in response.json().get("items", [])
            ]

        response = await client.get(
            f"{GITHUB_API_BASE}/user/repos",
            params={
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
                "per_page": per_page,
                "page": page,
            },
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        _check_rate_limit(response)
        return [
            {
                "full_name": r["full_name"],
                "private": r["private"],
                "language": r.get("language"),
                "description": r.get("description"),
                "default_branch": r.get("default_branch", "main"),
            }
            for r in response.json()
        ]


async def get_repo_tree(token: str, owner: str, repo: str, branch: str, recursive: bool = True) -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        params = {"recursive": "1"} if recursive else {}
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{branch}",
            params=params,
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        _check_rate_limit(response)
        return response.json().get("tree", [])


async def get_file_content(token: str, owner: str, repo: str, path: str, ref: str | None = None) -> str | None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        params = {"ref": ref} if ref else {}
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
            params=params,
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            return None
        _check_rate_limit(response)
        data = response.json()
        if data.get("size", 0) > 100_000:
            return None
        content = data.get("content", "")
        if not content:
            return None
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return None


async def get_repo_languages(token: str, owner: str, repo: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/languages",
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        _check_rate_limit(response)
        return response.json()


async def get_latest_commit_sha(token: str, owner: str, repo: str, branch: str) -> str:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{branch}",
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()["sha"]


COMPARE_MAX_FILES = 300
COMPARE_MAX_PATCH_BYTES = 4096


async def compare_commits(token: str, repo_full_name: str, base_sha: str, head_sha: str) -> dict | None:
    """Compare two commits via the GitHub compare API.

    Returns a dict of changed files (patches capped at ~4KB each, file list capped at 300
    with a ``truncated`` flag), or ``None`` when the compare cannot be resolved (404, base/head
    not found, or a too-large diff GitHub refuses to serve).
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{repo_full_name}/compare/{base_sha}...{head_sha}",
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        if response.status_code == 404:
            logger.warning("[GITHUB COMPARE] %s %s...%s not found", repo_full_name, base_sha[:8], head_sha[:8])
            return None
        if response.status_code in (413, 422):
            logger.warning("[GITHUB COMPARE] %s diff too large or unprocessable", repo_full_name)
            return None
        response.raise_for_status()
        _check_rate_limit(response)
        data = response.json()

        raw_files = data.get("files") or []
        truncated = len(raw_files) > COMPARE_MAX_FILES
        files = []
        for raw in raw_files[:COMPARE_MAX_FILES]:
            patch = raw.get("patch")
            if patch is not None and len(patch.encode("utf-8")) > COMPARE_MAX_PATCH_BYTES:
                patch = patch.encode("utf-8")[:COMPARE_MAX_PATCH_BYTES].decode("utf-8", errors="ignore")
            files.append(
                {
                    "filename": raw.get("filename"),
                    "status": raw.get("status"),
                    "patch": patch,
                    "additions": raw.get("additions", 0),
                    "deletions": raw.get("deletions", 0),
                }
            )

        return {
            "files": files,
            "total_commits": data.get("total_commits", 0),
            "html_url": data.get("html_url", ""),
            "truncated": truncated,
        }


async def is_oauth_configured(session: AsyncSession | None = None) -> bool:
    client_id, _ = await get_github_oauth_credentials(session)
    return bool(client_id)


async def initiate_device_flow(
    client_id: str,
    tenant_id: UUID | None = None,
    user_id: UUID | None = None,
) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            GITHUB_DEVICE_CODE_URL,
            json={"client_id": client_id, "scope": GITHUB_SCOPES},
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise ValueError(f"Device flow initiation failed: {response.text}")

        data = response.json()
        if "error" in data:
            raise ValueError(f"GitHub Device Flow error: {data.get('error_description', data['error'])}")

        device_code = data["device_code"]
        _device_code_store[device_code] = {"tenant_id": tenant_id, "user_id": user_id}
        logger.info("[GITHUB DEVICE FLOW] Initiated, user_code=%s", data.get("user_code"))
        return data


async def poll_device_token(device_code: str, client_id: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            GITHUB_TOKEN_URL,
            json={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )
        data = response.json()
        error = data.get("error")

        if error in ("authorization_pending",):
            return {"status": "pending"}
        if error == "slow_down":
            return {"status": "slow_down"}
        if error == "access_denied":
            _device_code_store.pop(device_code, None)
            return {"status": "denied"}
        if error == "expired_token":
            _device_code_store.pop(device_code, None)
            return {"status": "expired"}
        if error:
            _device_code_store.pop(device_code, None)
            raise ValueError(f"GitHub Device Flow error: {data.get('error_description', error)}")

        context = _device_code_store.pop(device_code, {})
        return {"status": "success", "token_data": data, "context": context}


async def _validate_fine_grained_pat(token: str) -> dict:
    user_info = await get_authenticated_user(token)
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GITHUB_API_BASE}/user/repos",
            params={"per_page": 1},
            headers={**GITHUB_HEADERS, "Authorization": f"Bearer {token}"},
        )
        if response.status_code == 403:
            raise ValueError(
                "Fine-grained token lacks repository access. Grant 'Contents: Read-only' "
                "and 'Metadata: Read-only' permissions and select at least one repository."
            )
        response.raise_for_status()
        if not response.json():
            raise ValueError(
                "Fine-grained token has no accessible repositories. "
                "Select at least one repository when creating the token."
            )
    return user_info


async def validate_and_save_pat(token: str, tenant_id: UUID, user_id: UUID, session: AsyncSession) -> dict:
    pat_type = detect_pat_type(token)
    if pat_type == AUTH_METHOD_PAT_FINE_GRAINED:
        user_info = await _validate_fine_grained_pat(token)
    else:
        missing = await validate_token_scopes(token)
        if missing:
            raise ValueError(
                f"Token is missing required scope(s): {', '.join(missing)}. "
                "Create a new token with these scopes at github.com/settings/tokens"
            )
        user_info = await get_authenticated_user(token)
    token_data = {"access_token": token, "token_type": pat_type}
    await save_github_token(tenant_id, user_id, token_data, session)
    return user_info


def _check_rate_limit(response: httpx.Response) -> None:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining and int(remaining) < 100:
        logger.warning(f"[GITHUB API] Rate limit low: {remaining} remaining")
