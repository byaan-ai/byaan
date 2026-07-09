"""Slack API service for posting messages."""

import re

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


_MENTION_RE = re.compile(r"<@[A-Z0-9]+(?:\|[^>]+)?>")


def strip_bot_mentions(text: str, bot_user_id: str | None = None) -> str:
    """Remove Slack user mentions (defaults to stripping every <@USER> token)."""
    if bot_user_id:
        text = re.sub(rf"<@{re.escape(bot_user_id)}(?:\|[^>]+)?>", "", text)
    text = _MENTION_RE.sub("", text)
    return text.strip()


class SlackService:
    """Service for Slack API interactions."""

    def __init__(self, bot_token: str):
        self.client = AsyncWebClient(token=bot_token)

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list | None = None,
    ) -> dict:
        """Post a message to a Slack channel."""
        try:
            response = await self.client.chat_postMessage(
                channel=channel,
                text=text,
                thread_ts=thread_ts,
                blocks=blocks,
            )
            logger.info(f"Slack API response: {response.data}")
            if "warning" in response.data:
                logger.warning(f"Slack API warning: {response.data['warning']}")
            return response.data
        except SlackApiError as e:
            logger.error(f"Slack API error posting message: {e.response['error']}")
            raise

    async def get_bot_info(self) -> dict:
        """Get bot's own info to extract bot_user_id."""
        try:
            response = await self.client.auth_test()
            return response.data
        except SlackApiError as e:
            logger.error(f"Slack API error in auth test: {e.response['error']}")
            raise

    async def get_user_info(self, user_id: str) -> dict | None:
        """Look up a Slack user via users.info.

        Returns a normalized dict with ``id``, ``name`` (display name falling back
        to real name) and ``email`` (may be None). Returns None on missing scope or
        any API error so callers can treat the user as unidentified.
        """
        try:
            response = await self.client.users_info(user=user_id)
            user = response.get("user", {}) or {}
            profile = user.get("profile", {}) or {}
            name = profile.get("display_name") or profile.get("real_name") or user.get("name") or user_id
            return {
                "id": user.get("id", user_id),
                "name": name,
                "email": profile.get("email"),
            }
        except SlackApiError as e:
            logger.warning(f"Slack API error fetching user info: {e.response.get('error', 'unknown')}")
            return None

    async def list_channels(self, limit: int = 200) -> list[dict]:
        """List public channels the bot has access to."""
        try:
            channels = []
            cursor = None

            while True:
                response = await self.client.conversations_list(
                    types="public_channel",
                    exclude_archived=True,
                    limit=min(limit, 200),
                    cursor=cursor,
                )

                for channel in response.get("channels", []):
                    channels.append(
                        {
                            "id": channel["id"],
                            "name": channel["name"],
                        }
                    )

                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor or len(channels) >= limit:
                    break

            return channels[:limit]
        except SlackApiError as e:
            logger.error(f"Slack API error listing channels: {e.response['error']}")
            raise

    async def upload_file(
        self,
        channel: str,
        file_bytes: bytes,
        filename: str,
        thread_ts: str | None = None,
        initial_comment: str | None = None,
    ) -> dict:
        """
        Upload a file to a Slack channel or thread.

        Requires files:write scope in Slack app configuration.

        Args:
            channel: Channel ID to upload to
            file_bytes: File content as bytes
            filename: Name of the file (e.g., "dashboard.png")
            thread_ts: Optional thread timestamp to upload as thread reply
            initial_comment: Optional comment to post with the file

        Returns:
            Slack API response data

        Raises:
            SlackApiError: If the upload fails
        """
        try:
            response = await self.client.files_upload_v2(
                channel=channel,
                file=file_bytes,
                filename=filename,
                thread_ts=thread_ts,
                initial_comment=initial_comment,
            )
            logger.info(f"File uploaded to Slack channel {channel}: {filename}")
            return response.data
        except SlackApiError as e:
            error_code = e.response.get("error", "unknown_error")
            logger.error(f"Slack API error uploading file: {error_code}")
            raise

    async def fetch_thread_replies(
        self,
        channel: str,
        thread_ts: str,
        limit: int = 20,
    ) -> list[dict]:
        """Fetch replies for a thread via conversations.replies.

        Returns the most recent ``limit`` messages (oldest first). On any Slack
        API error returns an empty list so callers can fall back gracefully.
        """
        try:
            response = await self.client.conversations_replies(
                channel=channel,
                ts=thread_ts,
                limit=min(limit, 100),
            )
            messages = response.get("messages", []) or []
            if len(messages) > limit:
                messages = messages[-limit:]
            return messages
        except SlackApiError as e:
            logger.warning(f"Slack API error fetching thread replies: {e.response.get('error', 'unknown')}")
            return []
