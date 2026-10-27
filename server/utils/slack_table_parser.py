"""Parse markdown tables from agent responses and convert to Slack Table blocks."""

from __future__ import annotations

import re
from typing import Any

from server.utils.slack_block_elements import SlackBlockBuilder


class SlackTableParser:
    """Parser for converting markdown tables to Slack Table blocks."""

    @staticmethod
    def _normalize_table_boundaries(text: str) -> str:
        """Insert newlines between consecutive table rows that got glued together.

        LLM / outbound skill output sometimes collapses the newlines between
        rows so the whole table ends up on one line (``| a | b | |---|---| | 1 | 2 |``).
        We only touch cases where a closing ``|`` is followed by optional
        whitespace and then an opening ``|``; that pattern is exclusive to
        row-to-row junctions, never within a row.
        """
        text = re.sub(r"\|[ \t]+\|", "|\n|", text)
        # Prose + header-row glued case: non-pipe/non-space + optional ws + a full
        # pipe-delimited header row + newline + separator row (---).
        text = re.sub(
            r"([^\s|])\s*(\|(?:[^\n|]*\|){2,}[ \t]*\n\|[-:\s|]+\|)",
            r"\1\n\n\2",
            text,
        )
        return text

    @staticmethod
    def has_markdown_table(text: str) -> bool:
        """
        Check if text contains a markdown table.

        Args:
            text: Text to check

        Returns:
            True if markdown table is found
        """
        text = SlackTableParser._normalize_table_boundaries(text)
        # Require at least 2 cells per line and at least 2 rows for robust detection
        table_pattern = r"\|[^|\n]+\|[^|\n]+\|[\r\n]+\|[^|\n]+\|[^|\n]+\|"
        return bool(re.search(table_pattern, text))

    @staticmethod
    def extract_and_convert_tables(text: str) -> tuple[list[dict[str, Any]], str]:
        """
        Extract markdown tables and convert to Slack blocks.

        Args:
            text: Text containing markdown tables

        Returns:
            Tuple of (blocks list, remaining text without tables)
        """
        text = SlackTableParser._normalize_table_boundaries(text)
        blocks: list[dict[str, Any]] = []
        remaining_text = text

        table_pattern = r"(\|[^\n]+\|(?:\n\|[^\n]+\|)+)"
        matches = list(re.finditer(table_pattern, text))

        for match in reversed(matches):
            table_text = match.group(1)
            table_block = SlackTableParser._parse_markdown_table(table_text)

            if table_block:
                blocks.insert(0, table_block)
                remaining_text = remaining_text[: match.start()] + remaining_text[match.end() :]

        return blocks, remaining_text

    @staticmethod
    def _parse_markdown_table(table_text: str) -> dict[str, Any] | None:
        """
        Parse a single markdown table into Slack Table block.

        Args:
            table_text: Markdown table text

        Returns:
            Table block dict or None if parsing fails
        """
        lines = [line.strip() for line in table_text.strip().split("\n") if line.strip()]

        if len(lines) < 2:
            return None

        rows = []
        for idx, line in enumerate(lines):
            # Skip separator row (contains only dashes, colons, and pipes)
            cells = [cell.strip() for cell in line.split("|")[1:-1]]

            # Check if this is a separator row (all cells are just dashes/colons)
            if all(re.match(r"^[\s:-]+$", cell) for cell in cells):
                continue

            cells = [SlackTableParser._clean_cell_content(cell) for cell in cells]

            if cells:
                rows.append(cells)

        if len(rows) < 2:
            return None

        max_cols = max(len(row) for row in rows)
        min_cols = min(len(row) for row in rows)

        if max_cols != min_cols:
            from server.utils.custom_logger import get_logger

            logger = get_logger(__name__)
            logger.warning(
                f"Uneven table detected: max_cols={max_cols}, min_cols={min_cols}. "
                f"Padding rows to ensure Slack compatibility."
            )

        for row in rows:
            while len(row) < max_cols:
                row.append("-")

        column_settings = []
        if len(rows) > 1:
            for col_idx in range(len(rows[0])):
                is_numeric = True
                for row_idx in range(1, min(len(rows), 10)):
                    if col_idx < len(rows[row_idx]):
                        cell_value = rows[row_idx][col_idx]
                        if not SlackTableParser._is_numeric_cell(cell_value):
                            is_numeric = False
                            break

                setting: dict[str, Any] = {"is_wrapped": False}
                if is_numeric:
                    setting["align"] = "right"
                else:
                    setting["align"] = "left"

                column_settings.append(setting)

        try:
            return SlackBlockBuilder.table(
                rows=rows,
                column_settings=column_settings if column_settings else None,
                use_rich_text=True,
            )
        except Exception:
            return None

    @staticmethod
    def _clean_cell_content(cell: str) -> str:
        """
        Clean cell content by removing emoji markup and excessive whitespace.

        Args:
            cell: Raw cell content

        Returns:
            Cleaned cell content
        """
        # Remove :emoji: format
        cell = re.sub(r":[\w+-]+:", "", cell)
        # Remove Unicode emojis (most common ranges)
        cell = re.sub(r"[\U0001F300-\U0001F9FF]", "", cell)  # Misc symbols & pictographs
        cell = re.sub(r"[\U0001F600-\U0001F64F]", "", cell)  # Emoticons
        cell = re.sub(r"[\U0001F680-\U0001F6FF]", "", cell)  # Transport & map
        cell = re.sub(r"[\U00002600-\U000027BF]", "", cell)  # Misc symbols
        cell = re.sub(r"[\U0001F1E0-\U0001F1FF]", "", cell)  # Flags
        cell = re.sub(r"\s+", " ", cell)
        cleaned = cell.strip()
        # Slack requires non-empty text in table cells
        return cleaned if cleaned else "-"

    @staticmethod
    def _is_numeric_cell(cell: str) -> bool:
        """
        Check if cell content is numeric.

        Args:
            cell: Cell content

        Returns:
            True if numeric
        """
        if not cell or cell == "-":
            return False

        clean_cell = cell.replace(",", "").replace("$", "").replace("%", "").replace("€", "").replace("£", "").strip()

        try:
            float(clean_cell)
            return True
        except ValueError:
            return False
