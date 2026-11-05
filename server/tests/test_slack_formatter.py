"""Tests for the Slack Block Kit formatter."""

from server.utils.slack_formatter import (
    MAX_BLOCKS,
    MAX_HEADER_TEXT,
    MAX_SECTION_TEXT,
    SectionType,
    _consolidate_blocks,
    _convert_inline_markdown,
    _make_section_block,
    _parse_markdown_sections,
    markdown_to_slack_blocks,
)


class TestMarkdownToSlackBlocks:
    def test_empty_input(self):
        blocks, fallback = markdown_to_slack_blocks("")
        assert blocks == []
        assert fallback == ""

    def test_whitespace_only(self):
        blocks, fallback = markdown_to_slack_blocks("   \n\n  ")
        assert blocks == []
        assert fallback == ""

    def test_simple_paragraph(self):
        blocks, fallback = markdown_to_slack_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert blocks[0]["text"]["text"] == "Hello world"

    def test_h1_header(self):
        blocks, _ = markdown_to_slack_blocks("# My Report")
        assert blocks[0]["type"] == "header"
        assert blocks[0]["text"]["text"] == "My Report"
        assert blocks[0]["text"]["type"] == "plain_text"

    def test_h2_header(self):
        blocks, _ = markdown_to_slack_blocks("## Section Title")
        assert blocks[0]["type"] == "header"
        assert blocks[0]["text"]["text"] == "Section Title"

    def test_h3_header_as_bold_section(self):
        blocks, _ = markdown_to_slack_blocks("### Sub Section")
        assert blocks[0]["type"] == "section"
        assert "*Sub Section*" in blocks[0]["text"]["text"]

    def test_divider(self):
        blocks, _ = markdown_to_slack_blocks("---")
        assert blocks[0]["type"] == "divider"

    def test_divider_variants(self):
        for divider in ["---", "***", "___"]:
            blocks, _ = markdown_to_slack_blocks(divider)
            assert blocks[0]["type"] == "divider"

    def test_bold_conversion(self):
        blocks, fallback = markdown_to_slack_blocks("This is **bold** text")
        assert blocks[0]["text"]["text"] == "This is *bold* text"
        assert "*bold*" in fallback

    def test_underscore_bold_conversion(self):
        blocks, _ = markdown_to_slack_blocks("This is __bold__ text")
        assert blocks[0]["text"]["text"] == "This is *bold* text"

    def test_strikethrough_conversion(self):
        blocks, _ = markdown_to_slack_blocks("This is ~~deleted~~ text")
        assert blocks[0]["text"]["text"] == "This is ~deleted~ text"

    def test_link_conversion(self):
        blocks, _ = markdown_to_slack_blocks("Click [here](https://example.com)")
        assert blocks[0]["text"]["text"] == "Click <https://example.com|here>"

    def test_emoji_preserved(self):
        blocks, _ = markdown_to_slack_blocks("✅ All good! 🎉")
        assert "✅" in blocks[0]["text"]["text"]
        assert "🎉" in blocks[0]["text"]["text"]

    def test_unordered_list(self):
        text = "- Item one\n- Item two\n- Item three"
        blocks, _ = markdown_to_slack_blocks(text)
        content = blocks[0]["text"]["text"]
        assert "• Item one" in content
        assert "• Item two" in content
        assert "• Item three" in content

    def test_ordered_list(self):
        text = "1. First\n2. Second\n3. Third"
        blocks, _ = markdown_to_slack_blocks(text)
        content = blocks[0]["text"]["text"]
        assert "• First" in content
        assert "• Second" in content

    def test_list_with_inline_formatting(self):
        text = "- **Bold item**\n- [Link](https://example.com)"
        blocks, _ = markdown_to_slack_blocks(text)
        content = blocks[0]["text"]["text"]
        assert "• *Bold item*" in content
        assert "• <https://example.com|Link>" in content

    def test_code_block_preserved(self):
        text = "```python\nprint('hello')\n```"
        blocks, _ = markdown_to_slack_blocks(text)
        assert "```python" in blocks[0]["text"]["text"]
        assert "print('hello')" in blocks[0]["text"]["text"]

    def test_inline_code_preserved(self):
        blocks, _ = markdown_to_slack_blocks("Use `pip install` to install")
        assert "`pip install`" in blocks[0]["text"]["text"]

    def test_full_report_format(self):
        report = """# Weekly Report

## Key Metrics

- **Revenue:** $50,000
- **Users:** 1,234

---

### Details

Revenue increased by **15%** compared to last week.

For more info, see [dashboard](https://example.com/dashboard)."""

        blocks, fallback = markdown_to_slack_blocks(report)

        types = [b["type"] for b in blocks]
        assert "header" in types
        assert "divider" in types
        assert "section" in types

        assert "<https://example.com/dashboard|dashboard>" in fallback


class TestParseMarkdownSections:
    def test_header_levels(self):
        text = "# H1\n## H2\n### H3"
        sections = _parse_markdown_sections(text)
        assert len(sections) == 3
        assert sections[0].level == 1
        assert sections[1].level == 2
        assert sections[2].level == 3

    def test_empty_lines_skipped(self):
        text = "Hello\n\n\nWorld"
        sections = _parse_markdown_sections(text)
        assert len(sections) == 2

    def test_mixed_content(self):
        text = "# Title\n\nSome text\n\n---\n\n- Item 1\n- Item 2"
        sections = _parse_markdown_sections(text)
        types = [s.type for s in sections]
        assert SectionType.HEADER in types
        assert SectionType.PARAGRAPH in types
        assert SectionType.DIVIDER in types
        assert SectionType.LIST in types


class TestConvertInlineMarkdown:
    def test_bold(self):
        assert _convert_inline_markdown("**hello**") == "*hello*"

    def test_link(self):
        assert _convert_inline_markdown("[click](https://url.com)") == "<https://url.com|click>"

    def test_strikethrough(self):
        assert _convert_inline_markdown("~~removed~~") == "~removed~"

    def test_multiple_conversions(self):
        result = _convert_inline_markdown("**bold** and [link](https://x.com) and ~~strike~~")
        assert result == "*bold* and <https://x.com|link> and ~strike~"

    def test_no_change_for_plain_text(self):
        assert _convert_inline_markdown("plain text") == "plain text"


class TestChunkingAndConsolidation:
    def test_section_text_under_limit(self):
        blocks, _ = markdown_to_slack_blocks("Short text")
        assert len(blocks) == 1

    def test_long_text_chunked(self):
        long_text = "A" * (MAX_SECTION_TEXT + 500)
        blocks, _ = markdown_to_slack_blocks(long_text)
        for block in blocks:
            if block["type"] == "section":
                assert len(block["text"]["text"]) <= MAX_SECTION_TEXT

    def test_header_truncation(self):
        long_header = "# " + "A" * 200
        blocks, _ = markdown_to_slack_blocks(long_header)
        assert len(blocks[0]["text"]["text"]) <= MAX_HEADER_TEXT

    def test_consolidation_under_limit(self):
        blocks = [_make_section_block(f"Block {i}") for i in range(30)]
        result = _consolidate_blocks(blocks)
        assert len(result) == 30

    def test_consolidation_over_limit(self):
        blocks = [_make_section_block(f"Block {i}") for i in range(60)]
        result = _consolidate_blocks(blocks)
        assert len(result) <= MAX_BLOCKS

    def test_consolidation_merges_adjacent_sections(self):
        blocks = [_make_section_block("A" * 10) for _ in range(55)]
        result = _consolidate_blocks(blocks)
        assert len(result) <= MAX_BLOCKS


class TestMultipleBlockTypes:
    def test_paragraph_after_header(self):
        text = "# Title\n\nSome paragraph text here."
        blocks, _ = markdown_to_slack_blocks(text)
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"

    def test_list_after_divider(self):
        text = "---\n\n- Item 1\n- Item 2"
        blocks, _ = markdown_to_slack_blocks(text)
        assert blocks[0]["type"] == "divider"
        assert blocks[1]["type"] == "section"

    def test_code_block_standalone(self):
        text = "Before\n\n```\ncode here\n```\n\nAfter"
        blocks, _ = markdown_to_slack_blocks(text)
        assert len(blocks) == 3
        assert "code here" in blocks[1]["text"]["text"]


def test_handoff_markers_are_stripped_from_slack_replies():
    from server.services.slack_agent_service import HANDOFF_MARKER_RE

    text = "Click Start to begin. [[HANDOFF_CARD:c99be27c-90be-4f41-bfe9-383f1d4efbf6]]\nRun [[HANDOFF_RUN:abc|3]] done"
    assert HANDOFF_MARKER_RE.sub("", text) == "Click Start to begin.\nRun done"
