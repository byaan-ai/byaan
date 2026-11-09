"""Slack API service for posting messages."""

import re
import time

import httpx
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

_USER_TZ_CACHE: dict[str, tuple[str, float]] = {}
_USER_TZ_TTL_SECONDS = 3600

# Short-lived cache so time-critical interactivity handlers (e.g. opening a
# modal within Slack's 3-second trigger_id window) can skip the decrypt
# round-trip. Keyed by workspace id, value = bot_token.
_BOT_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_BOT_TOKEN_TTL_SECONDS = 300


def cached_bot_token(workspace_id: str) -> str | None:
    import time as _time

    entry = _BOT_TOKEN_CACHE.get(workspace_id)
    if not entry:
        return None
    token, expires_at = entry
    if expires_at < _time.monotonic():
        _BOT_TOKEN_CACHE.pop(workspace_id, None)
        return None
    return token


def cache_bot_token(workspace_id: str, token: str) -> None:
    import time as _time

    _BOT_TOKEN_CACHE[workspace_id] = (token, _time.monotonic() + _BOT_TOKEN_TTL_SECONDS)


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
        self._bot_token = bot_token

    async def download_file_bytes(self, url_private: str, max_bytes: int = 10 * 1024 * 1024) -> bytes | None:
        """Download a Slack-hosted file via its ``url_private`` using the bot token.

        Requires ``files:read`` scope. Returns bytes on success, ``None`` on any
        failure (missing scope, 404, oversize, timeout). Streams and enforces
        ``max_bytes`` without buffering the full body if the server lies about
        Content-Length.
        """
        if not url_private:
            return None
        headers = {"Authorization": f"Bearer {self._bot_token}"}
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
                async with http.stream("GET", url_private, headers=headers) as response:
                    if response.status_code != 200:
                        logger.warning(
                            f"Slack file download failed: status={response.status_code} url={url_private[:80]}"
                        )
                        return None
                    content_type = (response.headers.get("content-type") or "").lower()
                    if "text/html" in content_type:
                        logger.warning(
                            "Slack file download returned HTML — likely missing files:read scope or auth failure"
                        )
                        return None
                    buf = bytearray()
                    async for chunk in response.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            logger.warning(
                                f"Slack file exceeded max_bytes={max_bytes}; aborting download url={url_private[:80]}"
                            )
                            return None
                    return bytes(buf)
        except httpx.TimeoutException:
            logger.warning(f"Slack file download timed out url={url_private[:80]}")
            return None
        except Exception as e:
            logger.warning(f"Slack file download unexpected error: {e}")
            return None

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: list | None = None,
        reply_broadcast: bool = False,
    ) -> dict:
        """Post a message to a Slack channel."""
        try:
            kwargs = {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
            if thread_ts and reply_broadcast:
                kwargs["reply_broadcast"] = True
            response = await self.client.chat_postMessage(**kwargs)
            logger.info(f"Slack API response: {response.data}")
            if "warning" in response.data:
                logger.warning(f"Slack API warning: {response.data['warning']}")
            return response.data
        except SlackApiError as e:
            logger.error(f"Slack API error posting message: {e.response['error']}")
            raise

    async def set_assistant_status(self, channel: str, thread_ts: str, status: str) -> bool:
        """
        Show a transient 'Byaan is thinking...' style status under the thread.
        Pass an empty status to clear. Returns True on success, False on failure.
        """
        try:
            await self.client.api_call(
                "assistant.threads.setStatus",
                json={"channel_id": channel, "thread_ts": thread_ts, "status": status},
            )
            logger.debug(f"Slack set_assistant_status ok=True status={status!r}")
            return True
        except SlackApiError as e:
            err = e.response.get("error") if hasattr(e, "response") else str(e)
            logger.warning(f"Slack set_assistant_status failed: {err} status={status!r}")
            return False
        except Exception as e:
            logger.warning(f"Slack set_assistant_status unexpected error: {e} status={status!r}")
            return False

    async def add_reaction(self, channel: str, timestamp: str, name: str) -> bool:
        """Add an emoji reaction to a message. Returns True on success, False on failure."""
        try:
            await self.client.reactions_add(channel=channel, timestamp=timestamp, name=name)
            return True
        except SlackApiError as e:
            err = e.response.get("error") if hasattr(e, "response") else str(e)
            if err == "already_reacted":
                return True
            logger.warning(f"Slack reaction add failed: {err}")
            return False

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

    async def get_user_timezone(self, user_id: str, workspace_id: str | None = None) -> str:
        """Return the user's IANA timezone from Slack, cached per workspace+user for 1h.

        Falls back to UTC on any API failure or missing field.
        """
        cache_key = f"{workspace_id or 'default'}:{user_id}"
        now = time.monotonic()
        cached = _USER_TZ_CACHE.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]
        try:
            response = await self.client.users_info(user=user_id)
            user = response.get("user", {}) or {}
            tz = user.get("tz") or "UTC"
            logger.info(f"[SLACK TZ] user_id={user_id} resolved_tz={tz} tz_label={user.get('tz_label')}")
        except SlackApiError as e:
            logger.warning(f"Slack API error fetching user tz: {e.response.get('error', 'unknown')}")
            tz = "UTC"
        _USER_TZ_CACHE[cache_key] = (tz, now + _USER_TZ_TTL_SECONDS)
        return tz

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
