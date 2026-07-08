from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GitHubOAuthStartResponse(BaseModel):
    auth_url: str
    state: str


class GitHubOAuthCallbackRequest(BaseModel):
    code: str
    state: str


class GitHubOAuthStatusResponse(BaseModel):
    connected: bool
    username: str | None = None
    scopes: list[str] | None = None
    auth_method: str | None = None  # "oauth" | "pat_classic" | "pat_fine_grained"


class GitHubRepoConnect(BaseModel):
    repo_full_name: str = Field(..., min_length=3, max_length=255)
    default_branch: str = Field(default="main", max_length=100)


class RepoSkillSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    github_analysis_type: str | None


class GitHubRepoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    source: str = "github"
    repo_full_name: str
    default_branch: str
    local_path: str | None = None
    last_analyzed_sha: str | None
    analysis_status: str
    analysis_error: str | None
    language_breakdown: str | None
    is_active: bool
    scope: str = "user"
    created_at: datetime
    updated_at: datetime
    skills: list[RepoSkillSummary] = []


class LocalRepoConnect(BaseModel):
    path: str = Field(..., min_length=1)
    name: str | None = None


class GitHubRepoListItem(BaseModel):
    full_name: str
    private: bool
    language: str | None = None
    description: str | None = None
    default_branch: str = "main"


class GitHubAuthConfigResponse(BaseModel):
    oauth_available: bool
    can_configure_oauth: bool = False


class GitHubOAuthSettingsRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)


class GitHubOAuthSettingsResponse(BaseModel):
    client_id: str
    client_secret_configured: bool


class GitHubDeviceFlowStartResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class GitHubDeviceFlowPollRequest(BaseModel):
    device_code: str


class GitHubDeviceFlowPollResponse(BaseModel):
    status: str  # "success" | "pending" | "slow_down" | "denied" | "expired"
    connected: bool = False
    username: str | None = None


class GitHubPATRequest(BaseModel):
    token: str = Field(..., min_length=1)


class AnalysisRequest(BaseModel):
    llm_connection_id: str


class AnalysisStatusResponse(BaseModel):
    status: str
    progress_message: str | None = None
    files_analyzed: int | None = None
    total_files: int | None = None
    error: str | None = None
