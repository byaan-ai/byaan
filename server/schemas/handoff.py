from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALWAYS_DENIED_PATHS = (
    ".github/**",
    ".gitlab-ci.yml",
    ".circleci/**",
    "azure-pipelines.yml",
    "Jenkinsfile",
    ".drone.yml",
    "bitbucket-pipelines.yml",
    ".pre-commit-config.yaml",
    "CODEOWNERS",
)
DEFAULT_MINUTES = 30
MAX_MINUTES = 240
CI_FIX_ROUND_CHOICES = (1, 3, 5)
DEFAULT_CI_FIX_ROUNDS = 3


class HandoffBrief(BaseModel):
    problem: str = Field(..., min_length=1, max_length=4000)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    suspected_files: list[str] = Field(default_factory=list, max_length=20)
    acceptance_check: str = Field(default="", max_length=2000)

    @field_validator("evidence", "suspected_files")
    @classmethod
    def _trim_items(cls, value: list[str]) -> list[str]:
        return [item.strip()[:500] for item in value if item and item.strip()]


class HandoffPaths(BaseModel):
    allow: list[str] = Field(default_factory=list, max_length=50)
    deny: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allow", "deny")
    @classmethod
    def _validate_globs(cls, value: list[str]) -> list[str]:
        cleaned = []
        for pattern in value:
            pattern = pattern.strip().lstrip("/")
            if not pattern or ".." in pattern.split("/"):
                raise ValueError(f"Invalid path pattern: {pattern!r}")
            cleaned.append(pattern)
        return cleaned

    def effective_deny(self) -> list[str]:
        return list(dict.fromkeys([*ALWAYS_DENIED_PATHS, *self.deny]))


class HandoffRepoConfig(BaseModel):
    paths: HandoffPaths = Field(default_factory=HandoffPaths)
    minutes: int = Field(default=DEFAULT_MINUTES, ge=5, le=MAX_MINUTES)

    @classmethod
    def from_db(cls, raw: dict[str, Any] | None) -> HandoffRepoConfig:
        return cls.model_validate(raw or {})


class HandoffTeamSettings(BaseModel):
    instructions: str = Field(default="", max_length=4000)
    ci_fix_rounds: int | None = DEFAULT_CI_FIX_ROUNDS

    @field_validator("instructions")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("ci_fix_rounds")
    @classmethod
    def _known_choice(cls, value: int | None) -> int | None:
        if value is not None and value not in CI_FIX_ROUND_CHOICES:
            raise ValueError(f"CI fix attempts must be one of {CI_FIX_ROUND_CHOICES} or unlimited")
        return value


class HandoffRepoSettingsUpdate(BaseModel):
    enabled: bool
    config: HandoffRepoConfig | None = None


class GitHubAppManifestRequest(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=34, pattern=r"^[A-Za-z0-9][A-Za-z0-9 -]*$")
    organization: str | None = Field(default=None, max_length=39, pattern=r"^[A-Za-z0-9-]+$")


class HandoffBriefUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    brief: HandoffBrief


class HandoffRename(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class HandoffMessageCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


class HandoffEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    source: str
    kind: str
    message: str
    data: dict[str, Any] | None = None
    created_at: datetime


class HandoffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    github_repository_id: UUID | None
    repo_full_name: str
    requested_by: UUID | None
    origin: str
    notebook_id: UUID | None
    schedule_id: UUID | None
    title: str
    brief: dict[str, Any]
    status: str
    base_branch: str
    branch: str | None
    pr_number: int | None
    pr_url: str | None
    untested: bool
    ci_fix_round: int
    cost_usd: float
    error: str | None
    heartbeat_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class HandoffDetail(HandoffRead):
    events: list[HandoffEventRead] = Field(default_factory=list)
    has_transcript: bool = False
    steps: list[str] = Field(default_factory=list)
    activity: list[str] = Field(default_factory=list)
    pending_revision: str | None = None
    pending_revision_title: str | None = None
    pending_revision_brief: dict[str, Any] | None = None
    has_run: bool = False
