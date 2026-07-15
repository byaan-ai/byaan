"""Slack surface for the skill learning loop review workflow.

Posts review cards for skill suggestions to a workspace reviewers channel (or
the originating thread) and handles the approve / reject / discuss button
interactions coming back from Slack.
"""

from __future__ import annotations

import json
import os
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.skill_suggestion import SkillSuggestion
from server.models.slack_workspace import SlackWorkspace
from server.models.tenant_member import TenantMember
from server.models.user import User
from server.repositories.custom_skill import CustomSkillRepository
from server.repositories.skill_suggestion import SkillSuggestionRepository
from server.repositories.slack_workspace import SlackWorkspaceRepository
from server.services.crypto_service import CryptoService
from server.services.skill_suggestion_service import SkillSuggestionService
from server.services.slack_service import SlackService
from server.utils.custom_logger import get_logger
from server.utils.slack_block_elements import SlackBlockBuilder

logger = get_logger(__name__)

ACTION_APPROVE = "skill_suggestion_approve"
ACTION_REJECT = "skill_suggestion_reject"
ACTION_DISCUSS = "skill_suggestion_discuss"
ACTION_ACK = "skill_suggestion_ack"


def _frontend_url() -> str:
    url = os.getenv("FRONTEND_URL", "").rstrip("/")
    if url:
        return url
    try:
        from server.utils.config_loader import get_email_config

        return (get_email_config().get("frontend_url") or "").rstrip("/")
    except Exception:
        return ""


async def _bot_token(workspace: SlackWorkspace, session: AsyncSession) -> str:
    decrypted = await CryptoService.decrypt_config(workspace.bot_token_encrypted, session)
    return decrypted.get("bot_token", decrypted) if isinstance(decrypted, dict) else decrypted


async def _skill_name(session: AsyncSession, suggestion: SkillSuggestion) -> str:
    if suggestion.skill_id:
        skill = await CustomSkillRepository(session).get(suggestion.skill_id, suggestion.tenant_id)
        if skill:
            return skill.name
    return suggestion.title


async def _match_member(session: AsyncSession, tenant_id: UUID, email: str | None) -> User | None:
    """Resolve a Byaan user from a Slack profile email for reviewer attribution."""
    if not email:
        return None
    stmt = (
        select(User)
        .join(TenantMember, TenantMember.user_id == User.id)
        .where(TenantMember.tenant_id == tenant_id)
        .where(func.lower(User.email) == email.lower())
    )
    result = await session.execute(stmt)
    return result.scalars().first()


def _patch_diff_block(patch: dict | None) -> str:
    before = (patch or {}).get("before") or ""
    after = (patch or {}).get("after") or ""
    lines: list[str] = []
    for line in before.splitlines()[:12]:
        lines.append(f"- {line}")
    for line in after.splitlines()[:12]:
        lines.append(f"+ {line}")
    body = "\n".join(lines)[:2500]
    if not body:
        return ""
    return f"```\n{body}\n```"


def _context_line(suggestion: SkillSuggestion) -> str:
    source = suggestion.source or {}
    parts = []
    if suggestion.confidence:
        parts.append(f"Confidence: {suggestion.confidence}")
    if source.get("origin"):
        parts.append(f"Origin: {source.get('origin')}")
    if source.get("verdict"):
        parts.append(f"Verdict: {source.get('verdict')}")
    return " · ".join(parts) or "New skill learning-loop suggestion"


def _evidence_summary(suggestion: SkillSuggestion) -> str | None:
    evidence = suggestion.evidence
    if not isinstance(evidence, dict) or not evidence:
        return None
    summary = evidence.get("summary") or evidence.get("evidence")
    if not summary:
        summary = ", ".join(f"{k}: {v}" for k, v in list(evidence.items())[:3])
    if not summary:
        return None
    return f"Evidence: {str(summary)[:300]}"


def _build_card_blocks(suggestion: SkillSuggestion, skill_name: str) -> list[dict]:
    is_clarification = suggestion.suggestion_type == "clarification"
    value = json.dumps({"suggestion_id": str(suggestion.id)})

    if is_clarification:
        header = "Byaan needs a judgment call"
    else:
        header = f"Skill edit suggested — {skill_name}"

    blocks: list[dict] = [
        SlackBlockBuilder.header(header),
        SlackBlockBuilder.context([_context_line(suggestion)]),
    ]

    if suggestion.rationale:
        blocks.append(SlackBlockBuilder.section(suggestion.rationale[:2900]))

    diff = _patch_diff_block(suggestion.patch)
    if diff:
        blocks.append(SlackBlockBuilder.section(diff))

    evidence = _evidence_summary(suggestion)
    if evidence:
        blocks.append(SlackBlockBuilder.context([evidence]))

    if is_clarification:
        review_url = f"{_frontend_url()}/skill-review" if _frontend_url() else None
        ack_button = SlackBlockBuilder.button(
            text="Answer in Byaan",
            action_id=ACTION_ACK,
            value=value,
            url=review_url,
        )
        blocks.append(SlackBlockBuilder.actions([ack_button]))
        if review_url:
            blocks.append(SlackBlockBuilder.context([f"Review in Byaan: {review_url}"]))
    else:
        blocks.append(
            SlackBlockBuilder.actions(
                [
                    SlackBlockBuilder.button(text="Approve", action_id=ACTION_APPROVE, value=value, style="primary"),
                    SlackBlockBuilder.button(text="Reject", action_id=ACTION_REJECT, value=value, style="danger"),
                    SlackBlockBuilder.button(text="Discuss", action_id=ACTION_DISCUSS, value=value),
                ]
            )
        )

    return blocks


def _resolve_target(workspace: SlackWorkspace, suggestion: SkillSuggestion) -> tuple[str | None, str | None]:
    """Pick the channel + optional thread to post the review card to."""
    if workspace.reviewers_channel_id:
        return workspace.reviewers_channel_id, None
    source = suggestion.source or {}
    if source.get("origin") == "slack":
        channel = source.get("channel") or source.get("channel_id") or source.get("slack_channel_id")
        if channel:
            return channel, source.get("thread_ts") or source.get("slack_thread_ts")
    return None, None


async def notify_suggestion_created(session: AsyncSession, suggestion: SkillSuggestion) -> None:
    """Post a review card for a freshly created skill suggestion to Slack.

    Silently no-ops when the tenant has no active Slack workspace or when there
    is no channel to route the card to.
    """
    try:
        workspace = await SlackWorkspaceRepository(session).get_by_tenant(suggestion.tenant_id)
        if not workspace or not workspace.is_active:
            return

        channel, thread_ts = _resolve_target(workspace, suggestion)
        if not channel:
            logger.info(f"No Slack review target for suggestion {suggestion.id}; skipping notification")
            return

        skill_name = await _skill_name(session, suggestion)
        blocks = _build_card_blocks(suggestion, skill_name)

        bot_token = await _bot_token(workspace, session)
        slack = SlackService(bot_token)
        response = await slack.post_message(
            channel=channel,
            text=f"Skill review needed: {skill_name}",
            thread_ts=thread_ts,
            blocks=blocks,
        )

        message_ts = response.get("ts") if isinstance(response, dict) else None
        posted_channel = response.get("channel") if isinstance(response, dict) else channel
        suggestion.slack_channel_id = posted_channel or channel
        suggestion.slack_message_ts = message_ts
        await SkillSuggestionRepository(session).save(suggestion)
    except Exception as e:
        logger.error(f"Failed to notify Slack about suggestion {getattr(suggestion, 'id', None)}: {e}", exc_info=True)


def _points_at_review_card(suggestion: SkillSuggestion) -> bool:
    """True only when slack_channel_id/slack_message_ts were overwritten with a posted card's location.

    When card delivery failed they still hold the originating conversation's coordinates
    (copied from source at creation) — replying there would leak loop internals to the user.
    """
    if not suggestion.slack_channel_id or not suggestion.slack_message_ts:
        return False
    source = suggestion.source or {}
    source_ts = source.get("slack_thread_ts") or source.get("thread_ts")
    source_channel = source.get("slack_channel_id") or source.get("channel_id") or source.get("channel")
    return not (suggestion.slack_channel_id == source_channel and suggestion.slack_message_ts == source_ts)


async def notify_clarification_resolved(session: AsyncSession, suggestion: SkillSuggestion, note: str) -> None:
    """Reply under the original review card when a clarification is auto-resolved by re-evaluation."""
    try:
        if not _points_at_review_card(suggestion):
            return
        workspace = await SlackWorkspaceRepository(session).get_by_tenant(suggestion.tenant_id)
        if not workspace or not workspace.is_active:
            return

        bot_token = await _bot_token(workspace, session)
        slack = SlackService(bot_token)
        await slack.post_message(
            channel=suggestion.slack_channel_id,
            text=f"✅ {note}",
            thread_ts=suggestion.slack_message_ts,
        )
    except Exception as e:
        logger.error(f"Failed to post resolution note for suggestion {getattr(suggestion, 'id', None)}: {e}")


async def _post_ephemeral(response_url: str, text: str) -> None:
    if not response_url:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                response_url,
                json={"response_type": "ephemeral", "replace_original": False, "text": text},
                timeout=15.0,
            )
    except Exception as e:
        logger.warning(f"Failed to post ephemeral Slack response: {e}")


async def _replace_original(response_url: str, blocks: list[dict], text: str) -> None:
    if not response_url:
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                response_url,
                json={"replace_original": True, "text": text, "blocks": blocks},
                timeout=15.0,
            )
    except Exception as e:
        logger.warning(f"Failed to replace original Slack message: {e}")


async def handle_suggestion_action(
    *,
    action_id: str,
    suggestion_id: str,
    slack_user_id: str,
    response_url: str,
    team_id: str,
) -> None:
    """Handle an approve / reject / discuss button from a Slack review card."""
    from server.db.session import AsyncSessionFactory

    try:
        async with AsyncSessionFactory() as session:
            workspace = await SlackWorkspaceRepository(session).get_by_team_id(team_id)
            if not workspace:
                logger.warning(f"No Slack workspace for team {team_id}")
                return

            tenant_id = workspace.tenant_id
            try:
                suggestion = await SkillSuggestionRepository(session).get(UUID(suggestion_id), tenant_id)
            except (ValueError, TypeError):
                suggestion = None
            if not suggestion:
                await _post_ephemeral(response_url, "This suggestion no longer exists.")
                return

            bot_token = await _bot_token(workspace, session)
            slack = SlackService(bot_token)

            user_info = await slack.get_user_info(slack_user_id)
            email = user_info.get("email") if user_info else None
            reviewer_user = await _match_member(session, tenant_id, email)

            reviewer_name = (user_info or {}).get("name") or slack_user_id
            service = SkillSuggestionService(session)

            if action_id == ACTION_DISCUSS:
                await slack.post_message(
                    channel=suggestion.slack_channel_id,
                    text="Discuss here — replies will be attached to this suggestion.",
                    thread_ts=suggestion.slack_message_ts,
                )
                return

            if suggestion.status != "pending":
                handled_by = suggestion.reviewer_display_name or "another reviewer"
                await _post_ephemeral(response_url, f"Already handled by {handled_by}.")
                return

            skill_name = await _skill_name(session, suggestion)

            if action_id == ACTION_APPROVE:
                _, new_version = await service.approve(
                    suggestion.id,
                    tenant_id,
                    reviewed_by=reviewer_user.id if reviewer_user else None,
                    reviewed_via="slack",
                    reviewer_slack_user_id=slack_user_id,
                    reviewer_display_name=reviewer_name,
                )
                version_text = f"{skill_name} updated to v{new_version}" if new_version else f"{skill_name} applied"
                blocks = [
                    SlackBlockBuilder.section(f"✅ Approved by {reviewer_name} — {version_text}"),
                    SlackBlockBuilder.context([_context_line(suggestion)]),
                ]
                await _replace_original(response_url, blocks, f"Approved — {version_text}")

            elif action_id == ACTION_REJECT:
                await service.reject(
                    suggestion.id,
                    tenant_id,
                    reason="rejected via Slack",
                    reviewed_by=reviewer_user.id if reviewer_user else None,
                    reviewed_via="slack",
                    reviewer_slack_user_id=slack_user_id,
                    reviewer_display_name=reviewer_name,
                )
                blocks = [
                    SlackBlockBuilder.section(f"❌ Rejected by {reviewer_name} — {skill_name}"),
                    SlackBlockBuilder.context([_context_line(suggestion)]),
                ]
                await _replace_original(response_url, blocks, f"Rejected — {skill_name}")
                await slack.post_message(
                    channel=suggestion.slack_channel_id,
                    text="What's the one-line reason for rejecting? Reply here and it'll be attached to this suggestion.",
                    thread_ts=suggestion.slack_message_ts,
                )
            else:
                logger.warning(f"Unhandled skill suggestion action: {action_id}")
    except ValueError as e:
        logger.warning(f"Skill suggestion action rejected: {e}")
        await _post_ephemeral(response_url, "Already handled by another reviewer.")
    except Exception as e:
        logger.error(f"Error handling skill suggestion action {action_id}: {e}", exc_info=True)
        await _post_ephemeral(response_url, "Something went wrong handling that action. Please try again.")
