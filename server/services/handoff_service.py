from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.github_repository import GitHubRepository
from server.models.handoff import TERMINAL_STATUSES, WORKING_STATUSES, Handoff, HandoffEvent
from server.repositories.handoffs import (
    HandoffEventRepository,
    HandoffMessageRepository,
    HandoffRepository,
    utcnow,
)
from server.schemas.handoff import HandoffBrief, HandoffRepoConfig
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

PROPOSAL_COOLDOWN = timedelta(days=7)
RESTARTABLE_STATUSES = ("done", "ci_failed", "failed", "cancelled")
_FILE_LINE = re.compile(r"[\w./-]+\.\w+:\d+")


class HandoffError(Exception):
    pass


class RepoChoiceNeeded(HandoffError):
    def __init__(self, options: list[str]):
        super().__init__("Several repositories have handoffs turned on and this request doesn't say which one.")
        self.options = options


@dataclass(frozen=True)
class ProposalOutcome:
    handoff: Handoff
    action: str


@dataclass(frozen=True)
class Actor:
    user_id: UUID | None
    label: str | None = None

    @property
    def display(self) -> str:
        return self.label or "Someone"


async def record(
    session: AsyncSession,
    handoff_id: UUID,
    source: str,
    kind: str,
    message: str = "",
    data: dict[str, Any] | None = None,
    *,
    notify: bool = True,
) -> HandoffEvent:
    event = await HandoffEventRepository(session).append(handoff_id, source, kind, message, data)
    if notify:
        from server.services.handoff_notifier import notify_event

        await notify_event(handoff_id, event.seq)
    return event


async def move(
    session: AsyncSession,
    handoff_id: UUID,
    to_status: str,
    *,
    from_statuses: tuple[str, ...] | None = None,
    message: str = "",
    source: str = "system",
    data: dict[str, Any] | None = None,
    notify: bool = True,
    **fields: Any,
) -> bool:
    moved = await HandoffRepository(session).transition(handoff_id, to_status, from_statuses, **fields)
    if moved:
        await record(
            session, handoff_id, source, "status", message, {"status": to_status, **(data or {})}, notify=notify
        )
    return moved


async def get_for_tenant(session: AsyncSession, tenant_id: UUID, handoff_id: UUID) -> Handoff:
    handoff = await HandoffRepository(session).get_unscoped(handoff_id)
    if not handoff or handoff.tenant_id != tenant_id:
        raise HandoffError("Handoff not found")
    return handoff


def repo_config(repo: GitHubRepository | None) -> HandoffRepoConfig:
    return HandoffRepoConfig.from_db(repo.handoff_config if repo else None)


async def enabled_repos(session: AsyncSession, tenant_id: UUID) -> list[GitHubRepository]:
    result = await session.execute(
        select(GitHubRepository).where(
            GitHubRepository.tenant_id == tenant_id,
            GitHubRepository.handoff_enabled.is_(True),
            GitHubRepository.is_active.is_(True),
            GitHubRepository.source == "github",
        )
    )
    return list(result.scalars().all())


async def resolve_repo(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    repo_hint: str | None,
) -> tuple[GitHubRepository | None, list[GitHubRepository]]:
    candidates = await enabled_repos(session, tenant_id)
    if repo_hint:
        hint = repo_hint.strip().lower().removeprefix("https://github.com/").rstrip("/")
        exact = [r for r in candidates if r.repo_full_name.lower() == hint or str(r.id) == hint]
        if not exact and "/" not in hint:
            exact = [r for r in candidates if r.repo_full_name.lower().split("/")[-1] == hint.split("/")[-1]]
        return (exact[0] if len(exact) == 1 else None), candidates
    if len(candidates) == 1:
        return candidates[0], candidates
    return None, candidates


def fingerprint_for(repo_full_name: str, suspected_files: list[str], problem: str) -> str:
    locations = sorted({m.group(0).lower() for f in suspected_files for m in _FILE_LINE.finditer(f)})
    problem_class = " ".join(re.sub(r"\d+", "#", problem.lower()).split()[:12])
    raw = f"{repo_full_name.lower()}|{','.join(locations)}|{problem_class}"
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_proposal_evidence(brief: HandoffBrief) -> None:
    if not any(_FILE_LINE.search(f) for f in brief.suspected_files):
        raise HandoffError("A proposal must cite at least one file:line in suspected_files.")
    if not brief.acceptance_check.strip():
        raise HandoffError("A proposal must name a check that fails today and passes after the fix.")


async def propose(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    requested_by: UUID | None,
    origin: str,
    title: str,
    brief: HandoffBrief,
    repo_hint: str | None = None,
    slack_conversation_id: UUID | None = None,
    notebook_id: UUID | None = None,
    schedule_id: UUID | None = None,
    proposed_by_byaan: bool = False,
    new_task: bool = False,
    actor: Actor | None = None,
    revise_handoff_id: UUID | None = None,
) -> ProposalOutcome:
    actor = actor or Actor(user_id=requested_by)
    if revise_handoff_id is not None:
        if proposed_by_byaan or schedule_id:
            raise HandoffError("Monitors can only propose new handoffs, not change existing ones.")
        if new_task:
            raise HandoffError("A draft can either revise an existing handoff or start a new task, not both.")
        target = await get_for_tenant(session, tenant_id, revise_handoff_id)
        if target.status in ("dismissed", "taken_over"):
            raise HandoffError(f"That handoff is {target.status.replace('_', ' ')}; draft a new task instead.")
        await _require_handoffs_on(session, target)
        outcome = await _revise_task(session, target, title, brief, actor)
        if outcome is not None:
            return outcome
        repo_hint = target.repo_full_name

    repo, candidates = await resolve_repo(session, tenant_id, repo_hint=repo_hint)
    if not candidates:
        raise HandoffError("No repository has handoffs turned on. Enable one in Settings → GitHub → Handoffs.")
    if repo is None:
        raise RepoChoiceNeeded([candidate.repo_full_name for candidate in candidates])

    fingerprint = None
    if proposed_by_byaan or schedule_id:
        validate_proposal_evidence(brief)
    if schedule_id:
        fingerprint = fingerprint_for(repo.repo_full_name, brief.suspected_files, brief.problem)
        repo_handoffs = HandoffRepository(session)
        if await repo_handoffs.find_by_fingerprint(schedule_id, fingerprint, utcnow() - PROPOSAL_COOLDOWN):
            raise HandoffError("This issue already has a handoff proposal or fix in progress.")
        if await repo_handoffs.open_proposal_for_schedule(schedule_id):
            raise HandoffError("This monitor already has an open handoff proposal.")

    if not schedule_id and not new_task:
        existing = await HandoffRepository(session).task_for_thread(
            tenant_id, repo.id, slack_conversation_id=slack_conversation_id, notebook_id=notebook_id
        )
        if existing is not None:
            outcome = await _revise_task(session, existing, title, brief, actor)
            if outcome is not None:
                return outcome

    handoff = Handoff(
        tenant_id=tenant_id,
        github_repository_id=repo.id if repo else None,
        repo_full_name=repo.repo_full_name if repo else "",
        requested_by=requested_by,
        origin=origin,
        slack_conversation_id=slack_conversation_id,
        notebook_id=notebook_id,
        schedule_id=schedule_id,
        fingerprint=fingerprint,
        title=title.strip()[:200],
        brief=brief.model_dump(),
        status="proposed",
        base_branch=repo.effective_branch,
    )
    session.add(handoff)
    await session.commit()
    await session.refresh(handoff)
    await record(
        session,
        handoff.id,
        "system",
        "status",
        "Proposed by Byaan" if proposed_by_byaan or schedule_id else "Handoff drafted",
        {"status": "proposed"},
    )
    return ProposalOutcome(handoff=handoff, action="created")


def revision_text(title: str, brief: HandoffBrief) -> str:
    parts = [f"Revised task: {title.strip()}", brief.problem.strip()]
    if brief.evidence:
        parts.append("Evidence:\n" + "\n".join(f"- {item}" for item in brief.evidence))
    if brief.suspected_files:
        parts.append("Start by looking at:\n" + "\n".join(f"- {item}" for item in brief.suspected_files))
    if brief.acceptance_check.strip():
        parts.append(f"Done when: {brief.acceptance_check.strip()}")
    return "\n\n".join(parts)


async def _revise_task(
    session: AsyncSession, handoff: Handoff, title: str, brief: HandoffBrief, actor: Actor
) -> ProposalOutcome | None:
    """Apply a re-drafted brief to the thread's existing task; None means the task is finished and a new one is due."""
    if handoff.status == "proposed" and await HandoffRepository(session).update_draft(
        handoff.id, title=title.strip()[:200], brief={**handoff.brief, **brief.model_dump()}
    ):
        await record(session, handoff.id, "system", "status", "Brief revised", {"revised": True})
        return ProposalOutcome(handoff=await _reload(session, handoff.id), action="revised_draft")
    handoff = await _reload(session, handoff.id)

    text = revision_text(title, brief)
    if handoff.status in ("queued", *WORKING_STATUSES, "ci_running"):
        outcome = await add_message(session, handoff.tenant_id, handoff.id, text, actor)
        if outcome.startswith("new_handoff:"):
            return ProposalOutcome(handoff=await _reload(session, UUID(outcome.split(":", 1)[1])), action="created")
        return ProposalOutcome(handoff=await _reload(session, handoff.id), action="sent_to_running")

    if handoff.status in RESTARTABLE_STATUSES:
        if handoff.pr_number and await _pr_is_closed(session, handoff):
            return None
        inbox = HandoffMessageRepository(session)
        await inbox.mark_delivered([m.id for m in await inbox.pending(handoff.id, "revision")])
        message = await inbox.add(handoff.id, "revision", text, author_user_id=actor.user_id, author_label=actor.label)
        await record(
            session,
            handoff.id,
            "system",
            "status",
            "Revision drafted; Re-start to apply it on the same branch and pull request",
            {
                "revision": True,
                "message_id": str(message.id),
                "title": title.strip()[:200],
                "brief": brief.model_dump(),
            },
        )
        return ProposalOutcome(handoff=await _reload(session, handoff.id), action="pending_revision")
    return None


async def _require_handoffs_on(session: AsyncSession, handoff: Handoff) -> None:
    if not handoff.github_repository_id:
        raise HandoffError("This repository was disconnected from Byaan.")
    repo = await session.get(GitHubRepository, handoff.github_repository_id)
    if not repo or not repo.handoff_enabled:
        raise HandoffError("Handoffs are turned off for this repository.")


async def start(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, actor: Actor) -> Handoff:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    await _require_handoffs_on(session, handoff)
    fields: dict[str, Any] = {}
    if actor.user_id and (handoff.requested_by is None or handoff.schedule_id):
        fields["requested_by"] = actor.user_id
    if not await move(
        session,
        handoff.id,
        "queued",
        from_statuses=("proposed",),
        message=f"{actor.display} started the handoff",
        source="human",
        **fields,
    ):
        raise HandoffError("This handoff was already started or closed.")
    return await _reload(session, handoff.id)


async def dismiss(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, actor: Actor) -> Handoff:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    if not await move(
        session,
        handoff.id,
        "dismissed",
        from_statuses=("proposed", "queued"),
        message=f"{actor.display} dismissed it",
        source="human",
    ):
        raise HandoffError("Only a handoff that hasn't started can be dismissed.")
    return await _reload(session, handoff.id)


async def cancel(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, actor: Actor, reason: str = "") -> Handoff:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    if handoff.status == "pushing":
        raise HandoffError("Byaan is pushing the branch right now. Stop it once the pull request is open.")
    if handoff.status in TERMINAL_STATUSES or handoff.status == "proposed":
        raise HandoffError(f"This handoff is already {handoff.status.replace('_', ' ')}.")
    message = reason or f"{actor.display} stopped it"
    if handoff.status == "ci_running" and handoff.pr_number:
        message += ". The pull request stays open."
    if not await move(session, handoff.id, "cancelled", message=message, source="human"):
        raise HandoffError("The handoff changed state; try again.")
    return await _reload(session, handoff.id)


async def take_over(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, actor: Actor) -> Handoff:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    if handoff.status == "pushing":
        raise HandoffError("Byaan is pushing the branch right now. Take over once the pull request is open.")
    if not handoff.branch:
        raise HandoffError("There is no branch to take over yet.")
    pushed = bool(handoff.head_sha)
    message = f"{actor.display} took over the branch"
    if not pushed:
        message = f"{actor.display} took over before anything was pushed; start from {handoff.base_branch}"
    data = {"checkout": f"git fetch origin {handoff.branch} && git checkout {handoff.branch}"} if pushed else {}
    if not await move(
        session,
        handoff.id,
        "taken_over",
        message=message,
        source="human",
        data=data,
        next_check_at=None,
    ):
        raise HandoffError("This handoff can't be taken over in its current state.")
    return await _reload(session, handoff.id)


async def pending_revision(session: AsyncSession, handoff_id: UUID) -> dict[str, Any] | None:
    """The handoff's revision waiting for Re-start: its message plus the revised title and brief."""
    pending = await HandoffMessageRepository(session).pending(handoff_id, "revision")
    if not pending:
        return None
    message = pending[-1]
    drafted = await session.scalar(
        select(HandoffEvent)
        .where(HandoffEvent.handoff_id == handoff_id, HandoffEvent.data["message_id"].as_string() == str(message.id))
        .limit(1)
    )
    data = drafted.data if drafted else {}
    return {"message": message, "text": message.body, "title": data.get("title"), "brief": data.get("brief")}


async def restart(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, actor: Actor) -> Handoff:
    handoff, _ = await restart_with_outcome(session, tenant_id, handoff_id, actor)
    return handoff


async def restart_with_outcome(
    session: AsyncSession, tenant_id: UUID, handoff_id: UUID, actor: Actor
) -> tuple[Handoff, str]:
    """Re-run the task on the same handoff, branch, PR, clone and session; also returns what was applied."""
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    if handoff.status not in RESTARTABLE_STATUSES:
        raise HandoffError("Only a finished, failed or stopped handoff can be re-started.")
    await _require_handoffs_on(session, handoff)
    inbox = HandoffMessageRepository(session)
    revision = await pending_revision(session, handoff.id)
    if revision:
        request, applied = revision["text"], "pending_revision"
        await inbox.mark_delivered([m.id for m in await inbox.pending(handoff.id, "revision")])
        if revision["title"] and revision["brief"]:
            await HandoffRepository(session).update_fields(
                handoff.id, title=revision["title"], brief={**handoff.brief, **revision["brief"]}
            )
            handoff = await _reload(session, handoff.id)
            if handoff.pr_number:
                from server.services.handoff_runner import sync_pr_title

                await sync_pr_title(session, handoff)
    else:
        last = await inbox.latest(handoff.id, "followup")
        request = last.body if last and handoff.pr_number else None
        applied = "repeated_last_request" if request else "brief"
    outcome = await _continue_task(session, handoff, request, actor, "re-started the handoff")
    if outcome.startswith("new_handoff:"):
        return await _reload(session, UUID(outcome.split(":", 1)[1])), applied
    return await _reload(session, handoff.id), applied


async def _continue_task(session: AsyncSession, handoff: Handoff, request: str | None, actor: Actor, verb: str) -> str:
    if handoff.pr_number and await _pr_is_closed(session, handoff):
        new = await _followup_handoff(session, handoff, request or handoff.brief.get("problem", ""), actor)
        return f"new_handoff:{new.id}"
    if request:
        await HandoffMessageRepository(session).add(
            handoff.id, "followup", request, author_user_id=actor.user_id, author_label=actor.label
        )
    if not await move(
        session,
        handoff.id,
        "queued",
        from_statuses=RESTARTABLE_STATUSES,
        message=f"{actor.display} {verb}",
        source="human",
        ci_fix_round=0,
        next_check_at=None,
        finished_at=None,
        error=None,
    ):
        raise HandoffError("The handoff changed state; try again.")
    return "requeued"


async def add_message(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, text: str, actor: Actor) -> str:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    inbox = HandoffMessageRepository(session)
    text = text.strip()
    if not text:
        raise HandoffError("The message is empty.")

    if handoff.status == "waiting_on_human":
        question = await HandoffEventRepository(session).latest_of_kind(handoff.id, "question")
        await inbox.add(
            handoff.id,
            "answer",
            text,
            author_user_id=actor.user_id,
            author_label=actor.label,
            question_seq=question.seq if question else None,
        )
        await record(session, handoff.id, "human", "answer", f"{actor.display}: {text}", notify=False)
        return "answered"

    if handoff.status in ("queued", *WORKING_STATUSES):
        await inbox.add(handoff.id, "followup", text, author_user_id=actor.user_id, author_label=actor.label)
        await record(session, handoff.id, "human", "status", f"{actor.display} sent a follow-up", notify=False)
        return "queued_for_running_session"

    if handoff.status == "ci_running":
        if await _pr_is_closed(session, handoff):
            new = await _followup_handoff(session, handoff, text, actor)
            return f"new_handoff:{new.id}"
        await inbox.add(handoff.id, "followup", text, author_user_id=actor.user_id, author_label=actor.label)
        await record(session, handoff.id, "human", "status", f"{actor.display} sent a follow-up", notify=False)
        return "queued_after_ci"

    if handoff.status in RESTARTABLE_STATUSES:
        await _require_handoffs_on(session, handoff)
        pending = await inbox.pending(handoff.id, "revision")
        if pending:
            text = f"{pending[-1].body}\n\nAdditional request: {text}"
            await inbox.mark_delivered([m.id for m in pending])
        return await _continue_task(session, handoff, text, actor, "asked for changes")

    raise HandoffError(f"This handoff is {handoff.status.replace('_', ' ')}; start a new one instead.")


async def _pr_is_closed(session: AsyncSession, handoff: Handoff) -> bool:
    from server.services.github_app_service import resolve_auth
    from server.services.handoff_github import GitHubError, HandoffGitHub

    auth = await resolve_auth(
        session,
        tenant_id=handoff.tenant_id,
        repo_full_name=handoff.repo_full_name,
        requester_id=handoff.requested_by,
        origin=handoff.origin,
    )
    if not auth:
        return False
    try:
        pull = await HandoffGitHub(auth.token).get_pull(handoff.repo_full_name, handoff.pr_number)
    except GitHubError:
        return False
    return pull.get("state") != "open"


async def _followup_handoff(session: AsyncSession, handoff: Handoff, text: str, actor: Actor) -> Handoff:
    brief = HandoffBrief(
        problem=f'{text}\n\nFollow-up to the earlier handoff "{handoff.title}" (PR #{handoff.pr_number}).',
        evidence=handoff.brief.get("evidence", []),
        suspected_files=handoff.brief.get("suspected_files", []),
        acceptance_check=handoff.brief.get("acceptance_check", ""),
    )
    draft = await session.scalar(
        select(Handoff)
        .where(Handoff.status == "proposed", Handoff.brief["followup_of"].as_string() == str(handoff.id))
        .limit(1)
    )
    if draft is not None:
        outcome = await _revise_task(session, draft, draft.title, brief, actor)
        return outcome.handoff
    new = Handoff(
        tenant_id=handoff.tenant_id,
        github_repository_id=handoff.github_repository_id,
        repo_full_name=handoff.repo_full_name,
        requested_by=actor.user_id or handoff.requested_by,
        origin=handoff.origin,
        slack_conversation_id=handoff.slack_conversation_id,
        notebook_id=handoff.notebook_id,
        title=f"Follow-up: {handoff.title}"[:200],
        brief={**brief.model_dump(), "followup_of": str(handoff.id)},
        status="proposed",
        base_branch=handoff.base_branch,
    )
    session.add(new)
    await session.commit()
    await session.refresh(new)
    await record(
        session,
        new.id,
        "human",
        "status",
        "Follow-up drafted; the earlier PR is closed",
        {"status": "proposed", "deliver_card": True},
    )
    return new


async def rename(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, title: str) -> Handoff:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    title = title.strip()[:200]
    await HandoffRepository(session).update_fields(handoff.id, title=title)
    if handoff.pr_number:
        from server.services.handoff_runner import sync_pr_title

        await sync_pr_title(session, await _reload(session, handoff.id))
    await record(session, handoff.id, "human", "status", f'Renamed to "{title}"', {"renamed": True})
    return await _reload(session, handoff.id)


async def edit_brief(
    session: AsyncSession, tenant_id: UUID, handoff_id: UUID, brief: HandoffBrief, title: str | None
) -> Handoff:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    fields: dict[str, Any] = {"brief": {**handoff.brief, **brief.model_dump()}}
    if title:
        fields["title"] = title.strip()[:200]
    if not await HandoffRepository(session).update_draft(handoff.id, **fields):
        raise HandoffError("The brief can only be edited before the handoff starts.")
    await record(session, handoff.id, "human", "status", "Brief edited", {"edited": True})
    return await _reload(session, handoff.id)


async def cancel_for_repo(session: AsyncSession, repo_id: UUID, reason: str) -> int:
    count = 0
    for handoff in await HandoffRepository(session).active_for_repo(repo_id):
        if handoff.status == "proposed":
            moved = await move(session, handoff.id, "dismissed", message=reason)
        elif handoff.status == "pushing":
            moved = False
        else:
            moved = await move(session, handoff.id, "cancelled", message=reason, next_check_at=None)
        count += int(moved)
    return count


async def status_summary(session: AsyncSession, handoff: Handoff) -> dict[str, Any]:
    events = await HandoffEventRepository(session).recent(handoff.id, 40)
    progress = next((e for e in reversed(events) if e.kind == "progress"), None)
    ci = next((e for e in reversed(events) if e.kind == "ci"), None)
    tool = next((e for e in reversed(events) if e.kind == "tool"), None)
    ci_failure = next((e for e in reversed(events) if e.kind == "status" and (e.data or {}).get("log_excerpt")), None)
    if ci and (ci.data or {}).get("state") == "success":
        ci_failure = None
    heartbeat_age = None
    if handoff.heartbeat_at and handoff.status in WORKING_STATUSES:
        heartbeat_age = int((utcnow() - handoff.heartbeat_at).total_seconds())
    summary: dict[str, Any] = {
        "handoff_id": str(handoff.id),
        "title": handoff.title,
        "repo": handoff.repo_full_name,
        "status": handoff.status,
        "branch": handoff.branch,
        "pr_url": handoff.pr_url,
        "untested": handoff.untested,
        "ci_fix_round": handoff.ci_fix_round,
        "error": handoff.error,
        "heartbeat_age_seconds": heartbeat_age,
        "latest_progress": progress.message if progress else None,
        "progress_estimate": (progress.data or {}).get("estimate") if progress else None,
        "current_activity": tool.message if tool and handoff.status in WORKING_STATUSES else None,
        "ci": (ci.data or {}) if ci else None,
        "last_ci_failure": (
            {"checks": ci_failure.data.get("failing"), "log_excerpt": ci_failure.data.get("log_excerpt")}
            if ci_failure
            else None
        ),
        "ci_logs": "Byaan attaches the failing CI output to Claude Code on every fix round and follow-up run.",
        "recent_events": [
            {"kind": e.kind, "message": e.message, "at": e.created_at.isoformat() if e.created_at else None}
            for e in events[-10:]
            if e.kind != "tool"
        ],
    }
    if handoff.status == "queued":
        summary["queue_position"] = await HandoffRepository(session).queue_position(handoff)
    return summary


async def _reload(session: AsyncSession, handoff_id: UUID) -> Handoff:
    handoff = await HandoffRepository(session).get_unscoped(handoff_id)
    await session.refresh(handoff)
    return handoff


async def send_review_feedback(session: AsyncSession, tenant_id: UUID, handoff_id: UUID, actor: Actor) -> str:
    handoff = await get_for_tenant(session, tenant_id, handoff_id)
    event = await HandoffEventRepository(session).latest_of_kind(handoff.id, "review")
    comments = (event.data or {}).get("comments", []) if event else []
    if not comments:
        raise HandoffError("There are no new review comments to send.")
    lines = []
    for comment in comments:
        where = f" ({comment['path']}:{comment.get('line')})" if comment.get("path") else ""
        lines.append(f"- {comment.get('author')}{where}: {str(comment.get('body', ''))[:1500]}")
    return await add_message(
        session,
        tenant_id,
        handoff.id,
        "Address this reviewer feedback on the pull request:\n" + "\n".join(lines),
        actor,
    )


_WORKSPACE_PREFIX = re.compile(r"^.*?/work/")
_READ_TOOLS = {"Read", "NotebookRead"}
_SEARCH_TOOLS = {"Glob", "Grep", "LS"}
_EDIT_TOOLS = {"Edit", "MultiEdit", "NotebookEdit"}
_SHELL_SPLIT = re.compile(r"\n|;|&&|\|\||\|")
_REVIEW_COMMAND = re.compile(r"\bgit\b[^;&|\n]*\b(diff|status|show)\b")
_SHELL_KEYWORDS = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "do",
    "done",
    "while",
    "case",
    "esac",
    "sudo",
    "env",
    "time",
}
_IGNORED_PROGRAMS = {"cd", "echo", "true", "head", "tail", "cat", "E"}


def _preview(items: list[str], limit: int = 6) -> str:
    shown = ", ".join(f"`{item}`" for item in items[:limit])
    return f"{shown} (+{len(items) - limit} more)" if len(items) > limit else shown


_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", re.DOTALL)
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def _programs(command: str) -> list[str]:
    programs = []
    command = _QUOTED.sub("''", _HEREDOC.sub("", command))
    for segment in _SHELL_SPLIT.split(command):
        words = [w for w in segment.strip().split() if not re.match(r"^\w+=", w)]
        while words and words[0] in _SHELL_KEYWORDS:
            words.pop(0)
        if not words:
            continue
        name = words[0].rsplit("/", 1)[-1]
        if name in ("python", "python3") and len(words) > 2 and words[1] == "-m":
            name = words[2]
        elif name in ("pip", "pip3") and len(words) > 1:
            name = f"pip {words[1]}"
        elif name == "git":
            sub = next((w for w in words[1:] if not w.startswith("-") and "=" not in w), "")
            name = f"git {sub}".strip()
        if name and name not in _IGNORED_PROGRAMS and re.match(r"^[\w. -]+$", name):
            programs.append(name)
    return programs


def activity_summary(events: list[HandoffEvent]) -> dict[str, Any]:
    """Factual list of what Claude did, derived from recorded tool calls rather than Claude's own account."""
    read: list[str] = []
    edited: list[str] = []
    written: list[str] = []
    commands: list[str] = []
    searches = 0
    for event in events:
        if event.kind != "tool":
            continue
        data = event.data or {}
        tool = data.get("tool") or event.message.split(" ", 1)[0]
        target = data.get("target") or (event.message.split(" ", 1)[1] if " " in event.message else "")
        target = _WORKSPACE_PREFIX.sub("", target or "").strip()
        if tool in _READ_TOOLS and target and target not in read:
            read.append(target)
        elif tool in _EDIT_TOOLS and target and target not in edited:
            edited.append(target)
        elif tool == "Write" and target and target not in written:
            written.append(target)
        elif tool == "Bash" and target:
            commands.append(target)
        elif tool in _SEARCH_TOOLS:
            searches += 1

    counts: dict[str, int] = {}
    for command in commands:
        for program in _programs(command):
            counts[program] = counts.get(program, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    reviewed = any(_REVIEW_COMMAND.search(command) for command in commands)

    lines = []
    if read:
        lines.append(f"Read {len(read)} file{'s' if len(read) != 1 else ''}: {_preview(read)}")
    if searches:
        lines.append(f"Searched the code {searches} time{'s' if searches != 1 else ''}")
    if edited:
        lines.append(f"Edited {_preview(edited)}")
    if written:
        lines.append(f"Wrote {_preview(written)}")
    if commands:
        used = ", ".join(f"{name} ×{count}" if count > 1 else name for name, count in top[:8])
        more = f" (+{len(top) - 8} more)" if len(top) > 8 else ""
        lines.append(f"Ran {len(commands)} shell command{'s' if len(commands) != 1 else ''}: {used}{more}")
    if reviewed:
        lines.append("Reviewed its changes with git")
    return {
        "read": read,
        "edited": edited,
        "written": written,
        "commands": commands,
        "programs": dict(top),
        "searches": searches,
        "reviewed": reviewed,
        "lines": lines,
    }
