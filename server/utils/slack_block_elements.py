"""Slack Block Kit builders for rich message formatting."""

from __future__ import annotations

import re
from typing import Any, Literal

AlertLevel = Literal["default", "info", "success", "warning", "error"]
TextAlign = Literal["left", "center", "right"]


class SlackBlockBuilder:
    """Builder for creating Slack Block Kit blocks."""

    @staticmethod
    def alert(
        text: str,
        level: AlertLevel = "default",
        use_markdown: bool = True,
        block_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create an alert block.

        Args:
            text: Alert message content
            level: Severity level (default, info, success, warning, error)
            use_markdown: Use mrkdwn formatting (default True)
            block_id: Optional unique identifier

        Returns:
            Alert block dict
        """
        text_object: dict[str, Any] = {
            "type": "mrkdwn" if use_markdown else "plain_text",
            "text": text,
        }

        if use_markdown:
            text_object["verbatim"] = False

        block: dict[str, Any] = {
            "type": "alert",
            "text": text_object,
            "level": level,
        }
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def card(
        title: str | None = None,
        subtitle: str | None = None,
        body: str | None = None,
        hero_image_url: str | None = None,
        icon_url: str | None = None,
        actions: list[dict[str, Any]] | None = None,
        block_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a native Slack Card block.

        Args:
            title: Card title (max 150 chars)
            subtitle: Card subtitle (max 150 chars)
            body: Card body text (max 200 chars)
            hero_image_url: Top banner image URL
            icon_url: Small icon image URL (displayed next to title)
            actions: List of button elements
            block_id: Optional unique identifier

        Returns:
            Card block dict

        Raises:
            ValueError: If none of the required fields are provided

        Note:
            At least one of hero_image_url, title, actions, or body must be provided.
        """
        if not any([title, subtitle, body, hero_image_url, actions]):
            raise ValueError("Card must have at least one of: title, subtitle, body, hero_image_url, or actions")

        card_block: dict[str, Any] = {"type": "card"}

        if block_id:
            card_block["block_id"] = block_id

        if hero_image_url:
            card_block["hero_image"] = {"type": "image", "image_url": hero_image_url, "alt_text": "Card image"}

        if icon_url:
            card_block["icon"] = {"type": "image", "image_url": icon_url, "alt_text": "Card icon"}

        if title:
            card_block["title"] = {"type": "mrkdwn", "text": title[:150], "verbatim": False}

        if subtitle:
            card_block["subtitle"] = {"type": "mrkdwn", "text": subtitle[:150], "verbatim": False}

        if body:
            card_block["body"] = {"type": "mrkdwn", "text": body[:200], "verbatim": False}

        if actions:
            card_block["actions"] = actions

        return card_block

    @staticmethod
    def image(
        image_url: str,
        alt_text: str,
        title: str | None = None,
        block_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create an image block.

        Args:
            image_url: Public image URL (max 3000 chars)
            alt_text: Alt text for accessibility (max 2000 chars)
            title: Optional image title
            block_id: Optional unique identifier

        Returns:
            Image block dict
        """
        block: dict[str, Any] = {
            "type": "image",
            "image_url": image_url[:3000],
            "alt_text": alt_text[:2000],
        }
        if title:
            block["title"] = {"type": "plain_text", "text": title[:2000]}
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def table(
        rows: list[list[str | dict[str, Any]]],
        column_settings: list[dict[str, Any]] | None = None,
        block_id: str | None = None,
        use_rich_text: bool = False,
    ) -> dict[str, Any]:
        """
        Create a table block.

        Args:
            rows: List of rows, each row is a list of cell values (max 100 rows, 20 columns)
            column_settings: Optional column alignment/wrapping settings
            block_id: Optional unique identifier
            use_rich_text: Use rich_text cells for formatting (bold, links, mentions) instead of raw_text

        Returns:
            Table block dict

        Example:
            rows = [
                ["Name", "Age", "City"],
                ["Alice", "30", "NYC"],
                ["Bob", "25", "LA"]
            ]
        """
        if len(rows) > 100:
            raise ValueError("Table blocks support maximum 100 rows")
        if any(len(row) > 20 for row in rows):
            raise ValueError("Table rows support maximum 20 columns")

        formatted_rows = []
        for row in rows:
            formatted_row = []
            for cell in row:
                if isinstance(cell, dict):
                    formatted_row.append(cell)
                else:
                    if use_rich_text:
                        formatted_row.append(SlackBlockBuilder._create_rich_text_cell(str(cell)))
                    else:
                        formatted_row.append({"type": "raw_text", "text": str(cell)})
            formatted_rows.append(formatted_row)

        block: dict[str, Any] = {"type": "table", "rows": formatted_rows}

        if column_settings:
            block["column_settings"] = column_settings[:20]
        if block_id:
            block["block_id"] = block_id

        return block

    @staticmethod
    def _create_rich_text_cell(text: str) -> dict[str, Any]:
        """
        Create a rich_text cell from plain text, parsing markdown-like formatting.

        Supports:
        - **bold** text
        - [link](url) syntax
        - <@USER_ID> mentions
        - :emoji: codes

        Args:
            text: Text with optional markdown formatting

        Returns:
            Rich text cell dict
        """
        elements = []
        patterns = [
            (r"\*\*([^*\n]+)\*\*", "bold"),
            (r"(?<![\*\w])\*([^*\n]+)\*(?!\*)", "bold"),
            (r"(?<![_\w])_([^_\n]+)_(?!_)", "italic"),
            (r"`([^`\n]+)`", "code"),
            (r"~([^~\n]+)~", "strike"),
            (r"\[([^\]]+)\]\(([^)]+)\)", "link"),
            (r"<@([A-Z0-9]+)>", "user"),
            (r":([a-z0-9_+-]+):", "emoji"),
        ]

        raw_matches = []
        for pattern, match_type in patterns:
            for match in re.finditer(pattern, text):
                raw_matches.append((match.start(), match.end(), match_type, match))

        raw_matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))

        matches = []
        last_taken_end = -1
        for m in raw_matches:
            if m[0] >= last_taken_end:
                matches.append(m)
                last_taken_end = m[1]

        last_end = 0

        for start, end, match_type, match in matches:
            if start > last_end:
                plain_text = text[last_end:start]
                if plain_text:
                    elements.append({"type": "text", "text": plain_text})

            if match_type == "bold":
                elements.append({"type": "text", "text": match.group(1), "style": {"bold": True}})
            elif match_type == "italic":
                elements.append({"type": "text", "text": match.group(1), "style": {"italic": True}})
            elif match_type == "code":
                elements.append({"type": "text", "text": match.group(1), "style": {"code": True}})
            elif match_type == "strike":
                elements.append({"type": "text", "text": match.group(1), "style": {"strike": True}})
            elif match_type == "link":
                elements.append({"type": "link", "text": match.group(1), "url": match.group(2)})
            elif match_type == "user":
                elements.append({"type": "user", "user_id": match.group(1)})
            elif match_type == "emoji":
                elements.append({"type": "emoji", "name": match.group(1)})

            last_end = end

        if last_end < len(text):
            remaining_text = text[last_end:]
            if remaining_text:
                elements.append({"type": "text", "text": remaining_text})

        if not elements:
            elements = [{"type": "text", "text": text}]

        return {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": elements}]}

    @staticmethod
    def carousel(
        cards: list[dict[str, Any]],
        block_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a carousel block.

        Args:
            cards: List of card blocks (1-10 cards)
            block_id: Optional unique identifier

        Returns:
            Carousel block dict

        Raises:
            ValueError: If cards list is invalid or contains non-card blocks
        """
        if not cards or len(cards) < 1:
            raise ValueError("Carousel must contain at least 1 card")
        if len(cards) > 10:
            raise ValueError("Carousel supports maximum 10 cards")

        for idx, card in enumerate(cards):
            if not isinstance(card, dict):
                raise ValueError(f"Card at index {idx} must be a dict, got {type(card).__name__}")
            if card.get("type") != "card":
                raise ValueError(
                    f"Card at index {idx} must have type='card', got type='{card.get('type')}'. "
                    "Carousel elements must be Card blocks."
                )

        block: dict[str, Any] = {"type": "carousel", "elements": cards}
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def button(
        text: str,
        action_id: str,
        value: str | None = None,
        url: str | None = None,
        style: Literal["primary", "danger"] | None = None,
    ) -> dict[str, Any]:
        """
        Create a button element for use in actions or cards.

        Args:
            text: Button text
            action_id: Unique action identifier
            value: Optional value passed when clicked
            url: Optional URL to open
            style: Button style (primary or danger)

        Returns:
            Button element dict
        """
        button: dict[str, Any] = {
            "type": "button",
            "text": {"type": "plain_text", "text": text},
            "action_id": action_id,
        }
        if value:
            button["value"] = value
        if url:
            button["url"] = url
        if style:
            button["style"] = style
        return button

    @staticmethod
    def actions(
        elements: list[dict[str, Any]],
        block_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create an actions block containing interactive elements.

        Args:
            elements: List of interactive elements (buttons, select menus, etc.)
            block_id: Optional unique identifier

        Returns:
            Actions block dict
        """
        block: dict[str, Any] = {"type": "actions", "elements": elements}
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def section(
        text: str,
        use_markdown: bool = True,
        accessory: dict[str, Any] | None = None,
        block_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a section block.

        Args:
            text: Section text content
            use_markdown: Use mrkdwn formatting (default True)
            accessory: Optional accessory element (button, image, etc.)
            block_id: Optional unique identifier

        Returns:
            Section block dict
        """
        block: dict[str, Any] = {
            "type": "section",
            "text": {
                "type": "mrkdwn" if use_markdown else "plain_text",
                "text": text,
            },
        }
        if accessory:
            block["accessory"] = accessory
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def header(text: str, block_id: str | None = None) -> dict[str, Any]:
        """
        Create a header block.

        Args:
            text: Header text (max 150 chars)
            block_id: Optional unique identifier

        Returns:
            Header block dict
        """
        block: dict[str, Any] = {
            "type": "header",
            "text": {"type": "plain_text", "text": text[:150], "emoji": True},
        }
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def divider(block_id: str | None = None) -> dict[str, Any]:
        """
        Create a divider block.

        Args:
            block_id: Optional unique identifier

        Returns:
            Divider block dict
        """
        block: dict[str, Any] = {"type": "divider"}
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def context(
        elements: list[str | dict[str, Any]],
        block_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a context block for supplementary information.

        Args:
            elements: List of text strings or image elements
            block_id: Optional unique identifier

        Returns:
            Context block dict
        """
        formatted_elements = []
        for elem in elements:
            if isinstance(elem, str):
                formatted_elements.append({"type": "mrkdwn", "text": elem})
            else:
                formatted_elements.append(elem)

        block: dict[str, Any] = {"type": "context", "elements": formatted_elements}
        if block_id:
            block["block_id"] = block_id
        return block

    @staticmethod
    def monitor_card(
        monitor_id: str,
        name: str,
        status: str,
        cron_expression: str,
        timezone: str,
        slack_thread_ts: str | None,
        broadcast_to_channel: bool,
        next_run_at: str | None,
        next_fire_times: list[str] | None,
        instruction_preview: str,
        notify_policy: str = "always",
    ) -> list[dict[str, Any]]:
        """Static monitor card. Shows fields read-only + Turn-on/Cancel on draft or
        Delete on active. All field edits go through chat: user asks the agent to
        change time / days / delivery / instruction and the agent calls
        ``update_monitor``. The card is re-posted whenever the row changes.
        """
        from server.utils.cron_builder import humanize_days, time_and_days_from_cron

        b = SlackBlockBuilder

        header_text = {
            "draft": "Monitor draft",
            "active": "Monitor",
            "paused": "Monitor (paused)",
            "auto_paused": "Monitor (auto-paused)",
        }.get(status, "Monitor")

        status_label = {
            "draft": ":pencil2: Draft",
            "active": ":large_green_circle: Active",
            "paused": ":large_yellow_circle: Paused",
            "auto_paused": ":large_orange_circle: Auto-paused",
        }.get(status, status.title())

        parsed = time_and_days_from_cron(cron_expression)
        if parsed is not None:
            hour, minute, days = parsed
            schedule_label = f"{humanize_days(days)} at {hour:02d}:{minute:02d}"
        else:
            schedule_label = f"`{cron_expression}`"

        if slack_thread_ts and broadcast_to_channel:
            delivery_label = "Reply in thread + broadcast to channel"
        elif slack_thread_ts:
            delivery_label = "Reply in this thread"
        else:
            delivery_label = "Post to channel"

        notify_label = {
            "always": "Every run",
            "on_alert": "Only when something's wrong",
            "on_change": "Only when results change",
        }.get(notify_policy, notify_policy)

        body_lines = [
            f"*Status:* {status_label}",
            f"*When:* {schedule_label} · {timezone}",
            f"*Where to post:* {delivery_label}",
            f"*Notify:* {notify_label}",
        ]
        if next_run_at and status != "draft":
            body_lines.append(f"*Next run:* `{next_run_at}`")
        if status == "draft" and next_fire_times:
            fires = ", ".join(next_fire_times[:3])
            body_lines.append(f"*Would fire next:* {fires}")

        blocks: list[dict[str, Any]] = [
            b.header(f"{header_text}: {name[:120]}"),
            b.section("\n".join(body_lines)),
        ]

        if instruction_preview:
            blocks.append(b.section(f"*What it checks*\n{instruction_preview}"))

        if status == "draft":
            blocks.append(
                b.actions(
                    [
                        b.button("Turn on", action_id="monitor_turn_on", value=monitor_id, style="primary"),
                        b.button("Run a test now", action_id="monitor_run_now", value=monitor_id),
                        b.button("Cancel", action_id="monitor_cancel", value=monitor_id, style="danger"),
                    ]
                )
            )
            blocks.append(b.context(["Nothing runs until you turn it on."]))
        else:
            blocks.append(
                b.actions(
                    [
                        b.button("Run a test now", action_id="monitor_run_now", value=monitor_id),
                        b.button("Delete", action_id="monitor_delete", value=monitor_id, style="danger"),
                    ]
                )
            )
            blocks.append(b.context(["Ask Byaan to change the time, days, timezone, delivery, or instruction."]))

        return blocks

    @staticmethod
    def monitor_list_card(
        monitors: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compact list of monitors with inline Open / Pause-Resume / Delete controls."""
        b = SlackBlockBuilder
        blocks: list[dict[str, Any]] = [b.header("Monitors")]
        if not monitors:
            blocks.append(b.section("_No monitors yet._"))
            return blocks
        for m in monitors:
            status = m.get("status") or ("active" if m.get("is_enabled") else "paused")
            value_payload = m["id"]
            body = (
                f"*{m.get('name', 'Untitled')}*\n"
                f"`{m.get('cron_expression', '?')}` · {m.get('timezone', 'UTC')} · {status}\n"
                f"Next: `{m.get('next_run_at') or 'n/a'}`"
            )
            blocks.append(b.section(body))

            if status == "draft":
                buttons = [
                    b.button("Turn on", action_id="monitor_turn_on", value=value_payload, style="primary"),
                    b.button("Run a test now", action_id="monitor_run_now", value=value_payload),
                    b.button("Delete", action_id="monitor_delete", value=value_payload, style="danger"),
                ]
            else:
                toggle_label = "Pause" if status == "active" else "Resume"
                buttons = [
                    b.button(toggle_label, action_id="monitor_toggle", value=value_payload),
                    b.button("Delete", action_id="monitor_delete", value=value_payload, style="danger"),
                ]
            blocks.append(b.actions(buttons))
            blocks.append(b.divider())
        return blocks
