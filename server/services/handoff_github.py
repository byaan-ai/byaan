from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from typing import Any

import httpx

from server.services.github_service import GITHUB_API_BASE, GITHUB_HEADERS, _check_rate_limit
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

_ETAG_CACHE_SIZE = 512
_etag_cache: OrderedDict[str, tuple[str, Any]] = OrderedDict()

FAILING_CONCLUSIONS = {"failure", "timed_out", "action_required", "startup_failure", "cancelled"}
PASSING_CONCLUSIONS = {"success", "neutral", "skipped"}


class GitHubError(Exception):
    def __init__(self, status: int, message: str, rate_limited: bool = False):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status
        self.rate_limited = rate_limited


class HandoffGitHub:
    def __init__(self, token: str, timeout: float = 30.0):
        self._token = token
        self._timeout = timeout
        self._token_key = hashlib.sha256(token.encode()).hexdigest()[:16]

    def _headers(self) -> dict[str, str]:
        return {**GITHUB_HEADERS, "Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, path: str, *, cached: bool = False, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{GITHUB_API_BASE}{path}"
        headers = self._headers()
        cache_key = f"{self._token_key}:{url}:{kwargs.get('params')}"
        cached_entry = _etag_cache.get(cache_key) if cached and method == "GET" else None
        if cached_entry:
            headers["If-None-Match"] = cached_entry[0]

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(method, url, headers=headers, **kwargs)

        if response.status_code == 304 and cached_entry:
            _etag_cache.move_to_end(cache_key)
            return cached_entry[1]
        if response.status_code >= 400:
            try:
                message = response.json().get("message", response.text)
            except ValueError:
                message = response.text
            rate_limited = response.status_code == 429 or response.headers.get("X-RateLimit-Remaining") == "0"
            raise GitHubError(response.status_code, str(message)[:300], rate_limited=rate_limited)
        _check_rate_limit(response)
        data = response.json() if response.content else None
        etag = response.headers.get("ETag")
        if cached and method == "GET" and etag:
            _etag_cache[cache_key] = (etag, data)
            _etag_cache.move_to_end(cache_key)
            while len(_etag_cache) > _ETAG_CACHE_SIZE:
                _etag_cache.popitem(last=False)
        return data

    async def get_repo(self, repo: str) -> dict[str, Any]:
        return await self._request("GET", f"/repos/{repo}")

    async def can_push(self, repo: str) -> bool:
        data = await self.get_repo(repo)
        permissions = data.get("permissions")
        return True if permissions is None else bool(permissions.get("push"))

    async def create_pull(self, repo: str, *, head: str, base: str, title: str, body: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/repos/{repo}/pulls",
            json={"head": head, "base": base, "title": title, "body": body, "draft": False},
        )

    async def find_open_pull(self, repo: str, head: str) -> dict[str, Any] | None:
        owner = repo.split("/", 1)[0]
        pulls = await self._request("GET", f"/repos/{repo}/pulls", params={"head": f"{owner}:{head}", "state": "open"})
        return pulls[0] if pulls else None

    async def update_pull(self, repo: str, number: int, **fields: Any) -> dict[str, Any]:
        return await self._request("PATCH", f"/repos/{repo}/pulls/{number}", json=fields)

    async def get_pull(self, repo: str, number: int) -> dict[str, Any]:
        return await self._request("GET", f"/repos/{repo}/pulls/{number}", cached=True)

    async def compare_status(self, repo: str, base: str, head: str) -> str:
        data = await self._request("GET", f"/repos/{repo}/compare/{base}...{head}")
        return data.get("status", "")

    async def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        await self._request("POST", f"/repos/{repo}/issues/{number}/labels", json={"labels": labels})

    async def check_runs(self, repo: str, sha: str) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self._request(
                "GET",
                f"/repos/{repo}/commits/{sha}/check-runs",
                cached=True,
                params={"per_page": 100, "page": page},
            )
            batch = data.get("check_runs", [])
            runs.extend(batch)
            if len(batch) < 100:
                return runs
            page += 1

    async def combined_status(self, repo: str, sha: str) -> dict[str, Any]:
        """Commit statuses are optional; a token without access to them still has check runs to go on."""
        try:
            return await self._request("GET", f"/repos/{repo}/commits/{sha}/status", cached=True)
        except GitHubError as exc:
            if exc.status != 403 or exc.rate_limited:
                raise
            logger.warning(f"[HANDOFF] Commit statuses not readable for {repo}; using check runs only")
            return {"statuses": []}

    async def has_ci(self, repo: str, sha: str) -> bool:
        if await self.check_runs(repo, sha):
            return True
        status = await self.combined_status(repo, sha)
        return bool(status.get("statuses"))

    async def job_log_failure(self, repo: str, job_id: int, limit: int = 12_000) -> str | None:
        url = f"{GITHUB_API_BASE}/repos/{repo}/actions/jobs/{job_id}/logs"
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=self._headers())
        if response.status_code != 200:
            logger.info(f"[HANDOFF] Job log {job_id} unavailable: HTTP {response.status_code}")
            return None
        return failing_step_output(response.text, limit)

    async def review_feedback(self, repo: str, number: int) -> list[dict[str, Any]]:
        comments = await self._request("GET", f"/repos/{repo}/pulls/{number}/comments", params={"per_page": 100})
        reviews = await self._request("GET", f"/repos/{repo}/pulls/{number}/reviews", params={"per_page": 100})
        issue_comments = await self._request("GET", f"/repos/{repo}/issues/{number}/comments", params={"per_page": 100})
        feedback = []
        for item in comments or []:
            feedback.append(
                {
                    "id": f"c{item['id']}",
                    "author": item.get("user", {}).get("login"),
                    "is_bot": item.get("user", {}).get("type") == "Bot",
                    "body": item.get("body", ""),
                    "path": item.get("path"),
                    "line": item.get("line") or item.get("original_line"),
                    "created_at": item.get("created_at"),
                }
            )
        for item in reviews or []:
            if not (item.get("body") or "").strip():
                continue
            feedback.append(
                {
                    "id": f"r{item['id']}",
                    "author": item.get("user", {}).get("login"),
                    "is_bot": item.get("user", {}).get("type") == "Bot",
                    "body": item.get("body", ""),
                    "state": item.get("state"),
                    "created_at": item.get("submitted_at"),
                }
            )
        for item in issue_comments or []:
            feedback.append(
                {
                    "id": f"i{item['id']}",
                    "author": item.get("user", {}).get("login"),
                    "is_bot": item.get("user", {}).get("type") == "Bot",
                    "body": item.get("body", ""),
                    "created_at": item.get("created_at"),
                }
            )
        return sorted(feedback, key=lambda f: f.get("created_at") or "")

    async def requires_signed_commits(self, repo: str, branch: str) -> bool | None:
        try:
            data = await self._request("GET", f"/repos/{repo}/branches/{branch}/protection/required_signatures")
        except GitHubError as exc:
            if exc.status in (403, 404):
                return None
            raise
        return bool(data.get("enabled"))


def summarize_checks(check_runs: list[dict[str, Any]], combined: dict[str, Any]) -> dict[str, Any]:
    pending, failing, passing = [], [], []
    for run in check_runs:
        name = run.get("name", "check")
        if run.get("status") != "completed":
            pending.append(name)
        elif run.get("conclusion") in FAILING_CONCLUSIONS:
            failing.append(
                {
                    "name": name,
                    "kind": "check_run",
                    "id": run.get("id"),
                    "details_url": run.get("details_url") or run.get("html_url"),
                    "is_actions": (run.get("app") or {}).get("slug") == "github-actions",
                    "conclusion": run.get("conclusion"),
                    "output": run.get("output") or {},
                }
            )
        else:
            passing.append(name)
    for status in combined.get("statuses", []) or []:
        name = status.get("context", "status")
        state = status.get("state")
        if state == "pending":
            pending.append(name)
        elif state in ("failure", "error"):
            failing.append(
                {
                    "name": name,
                    "kind": "status",
                    "details_url": status.get("target_url"),
                    "output": {"summary": status.get("description") or ""},
                }
            )
        else:
            passing.append(name)
    total = len(pending) + len(failing) + len(passing)
    if total == 0:
        state = "none"
    elif pending:
        state = "pending"
    elif failing:
        state = "failure"
    else:
        state = "success"
    return {"state": state, "pending": pending, "failing": failing, "passing": passing}


_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z ?", re.MULTILINE)


def failing_step_output(log: str, limit: int) -> str:
    """Output of the step that failed, from its start, so the first errors are never cut off."""
    log = _TIMESTAMP.sub("", log)
    error_at = log.find("##[error]")
    if error_at == -1:
        return log[-limit:]
    step_start = log.rfind("##[group]Run ", 0, error_at)
    section = log[step_start if step_start != -1 else 0 : error_at + 300]
    return section if len(section) <= limit else section[:limit] + "\n… (truncated)"
