"""Slack image-attachment pipeline tests.

Covers:
- SlackService.download_file_bytes (bearer-auth streamed download)
- SlackAgentService._build_attachments_from_slack_files (mime/size/count filtering)
- classify_intent attachment-count hint in the classifier prompt
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from server.services.slack_agent_service import (
    DEFAULT_IMAGE_ONLY_PROMPT,
    MAX_IMAGE_ATTACHMENTS,
    MAX_IMAGE_BYTES,
    SUPPORTED_IMAGE_MIME_TYPES,
    SlackAgentService,
)
from server.services.slack_intent_classifier import (
    INTENT_TRUE_TEXT,
    classify_intent,
)
from server.services.slack_service import SlackService


def _make_file(
    name: str = "pic.png",
    mimetype: str = "image/png",
    url_private: str = "https://files.slack.com/f1",
    size: int | None = 1024,
) -> dict:
    payload = {
        "id": "F123",
        "name": name,
        "mimetype": mimetype,
        "url_private": url_private,
    }
    if size is not None:
        payload["size"] = size
    return payload


def _fake_client(download_return: bytes | None = b"PNGDATA") -> AsyncMock:
    client = AsyncMock()
    client.download_file_bytes = AsyncMock(return_value=download_return)
    return client


class _FakeStreamResponse:
    def __init__(self, status_code: int, headers: dict | None = None, chunks: list[bytes] | None = None):
        self.status_code = status_code
        self.headers = headers or {"content-type": "image/png"}
        self._chunks = chunks or [b"OK"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeHttpClient:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response
        self.stream_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method, url, headers=None):
        self.stream_calls.append({"method": method, "url": url, "headers": headers})
        return self._response


class TestBuildAttachmentsFromSlackFiles:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_files(self):
        client = _fake_client()
        out = await SlackAgentService._build_attachments_from_slack_files(client, None)
        assert out == []
        client.download_file_bytes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_empty_when_empty_list(self):
        client = _fake_client()
        out = await SlackAgentService._build_attachments_from_slack_files(client, [])
        assert out == []
        client.download_file_bytes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_happy_path_single_png(self):
        client = _fake_client(b"PNGDATA")
        out = await SlackAgentService._build_attachments_from_slack_files(client, [_make_file()])
        assert len(out) == 1
        att = out[0]
        assert att["file_name"] == "pic.png"
        assert att["mime_type"] == "image/png"
        assert base64.b64decode(att["file_data"]) == b"PNGDATA"

    @pytest.mark.asyncio
    async def test_accepts_all_supported_mime_types(self):
        files = [
            _make_file(name="a.png", mimetype="image/png", url_private="u1"),
            _make_file(name="b.jpg", mimetype="image/jpeg", url_private="u2"),
            _make_file(name="c.webp", mimetype="image/webp", url_private="u3"),
        ]
        client = _fake_client(b"X")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 3
        assert [a["mime_type"] for a in out] == ["image/png", "image/jpeg", "image/webp"]
        assert {a["file_name"] for a in out} == {"a.png", "b.jpg", "c.webp"}

    @pytest.mark.asyncio
    async def test_skips_unsupported_mime_types(self):
        files = [
            _make_file(name="doc.pdf", mimetype="application/pdf", url_private="u1"),
            _make_file(name="vid.mp4", mimetype="video/mp4", url_private="u2"),
            _make_file(name="img.gif", mimetype="image/gif", url_private="u3"),
            _make_file(name="good.png", mimetype="image/png", url_private="u4"),
        ]
        client = _fake_client(b"OK")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 1
        assert out[0]["file_name"] == "good.png"
        assert client.download_file_bytes.await_count == 1

    @pytest.mark.asyncio
    async def test_skips_oversize_by_declared_size(self):
        files = [
            _make_file(name="big.png", size=MAX_IMAGE_BYTES + 1),
            _make_file(name="small.png", size=1024, url_private="u2"),
        ]
        client = _fake_client(b"DATA")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 1
        assert out[0]["file_name"] == "small.png"

    @pytest.mark.asyncio
    async def test_caps_at_max_attachments(self):
        files = [_make_file(name=f"p{i}.png", url_private=f"u{i}") for i in range(MAX_IMAGE_ATTACHMENTS + 3)]
        client = _fake_client(b"DATA")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == MAX_IMAGE_ATTACHMENTS

    @pytest.mark.asyncio
    async def test_skips_when_download_returns_none(self):
        files = [
            _make_file(name="fail.png", url_private="u1"),
            _make_file(name="ok.png", url_private="u2"),
        ]
        client = AsyncMock()
        client.download_file_bytes = AsyncMock(side_effect=[None, b"OK"])
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 1
        assert out[0]["file_name"] == "ok.png"

    @pytest.mark.asyncio
    async def test_missing_url_private_falls_back_to_download_url(self):
        files = [
            {
                "id": "F1",
                "name": "x.png",
                "mimetype": "image/png",
                "url_private_download": "https://dl.example/a",
                "size": 100,
            }
        ]
        client = _fake_client(b"D")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 1
        client.download_file_bytes.assert_awaited_once()
        called_url = client.download_file_bytes.await_args.args[0]
        assert called_url == "https://dl.example/a"

    @pytest.mark.asyncio
    async def test_skips_when_no_url_at_all(self):
        files = [{"id": "F1", "name": "x.png", "mimetype": "image/png"}]
        client = _fake_client(b"D")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert out == []
        client.download_file_bytes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_case_insensitive_mime(self):
        files = [_make_file(mimetype="IMAGE/PNG")]
        client = _fake_client(b"D")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 1
        assert out[0]["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_non_dict_entries_ignored(self):
        files = [None, "string", 42, _make_file()]
        client = _fake_client(b"D")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 1

    @pytest.mark.asyncio
    async def test_default_filename_when_missing(self):
        files = [
            {
                "id": "F1",
                "mimetype": "image/jpeg",
                "url_private": "u1",
                "size": 10,
            }
        ]
        client = _fake_client(b"D")
        out = await SlackAgentService._build_attachments_from_slack_files(client, files)
        assert len(out) == 1
        assert out[0]["file_name"] == "image.jpeg"


class TestConstants:
    def test_supported_mimes(self):
        assert SUPPORTED_IMAGE_MIME_TYPES == frozenset({"image/png", "image/jpeg", "image/webp"})

    def test_default_prompt_non_empty(self):
        assert DEFAULT_IMAGE_ONLY_PROMPT.strip()


class TestDownloadFileBytes:
    @pytest.mark.asyncio
    async def test_download_success_returns_bytes(self):
        svc = SlackService("xoxb-test")
        resp = _FakeStreamResponse(200, {"content-type": "image/png"}, [b"HEL", b"LO"])
        fake_http = _FakeHttpClient(resp)

        with patch("server.services.slack_service.httpx.AsyncClient", return_value=fake_http):
            out = await svc.download_file_bytes("https://files.slack.com/f1")

        assert out == b"HELLO"
        assert fake_http.stream_calls[0]["headers"] == {"Authorization": "Bearer xoxb-test"}

    @pytest.mark.asyncio
    async def test_download_returns_none_on_non_200(self):
        svc = SlackService("xoxb-test")
        resp = _FakeStreamResponse(403, {"content-type": "application/json"}, [b"{}"])

        with patch("server.services.slack_service.httpx.AsyncClient", return_value=_FakeHttpClient(resp)):
            out = await svc.download_file_bytes("https://files.slack.com/f1")

        assert out is None

    @pytest.mark.asyncio
    async def test_download_returns_none_on_html_response(self):
        svc = SlackService("xoxb-test")
        resp = _FakeStreamResponse(200, {"content-type": "text/html; charset=utf-8"}, [b"<html>"])

        with patch("server.services.slack_service.httpx.AsyncClient", return_value=_FakeHttpClient(resp)):
            out = await svc.download_file_bytes("https://files.slack.com/f1")

        assert out is None

    @pytest.mark.asyncio
    async def test_download_aborts_when_exceeds_max_bytes(self):
        svc = SlackService("xoxb-test")
        big_chunk = b"X" * 100
        resp = _FakeStreamResponse(200, {"content-type": "image/png"}, [big_chunk, big_chunk, big_chunk])

        with patch("server.services.slack_service.httpx.AsyncClient", return_value=_FakeHttpClient(resp)):
            out = await svc.download_file_bytes("https://files.slack.com/f1", max_bytes=150)

        assert out is None

    @pytest.mark.asyncio
    async def test_download_returns_none_on_timeout(self):
        svc = SlackService("xoxb-test")

        class _TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            def stream(self, method, url, headers=None):
                raise httpx.TimeoutException("boom")

        with patch("server.services.slack_service.httpx.AsyncClient", return_value=_TimeoutClient()):
            out = await svc.download_file_bytes("https://files.slack.com/f1")

        assert out is None

    @pytest.mark.asyncio
    async def test_download_returns_none_for_empty_url(self):
        svc = SlackService("xoxb-test")
        out = await svc.download_file_bytes("")
        assert out is None


class TestClassifierAttachmentHint:
    @pytest.mark.asyncio
    async def test_prompt_omits_hint_when_no_attachments(self):
        with patch(
            "server.services.slack_intent_classifier.CompletionService.complete",
            new=AsyncMock(return_value="TRUE_TEXT"),
        ) as mock_complete:
            intent, source = await classify_intent(
                text="show me revenue",
                history=[],
                llm_connection_id=uuid4(),
                session=AsyncMock(),
                attachment_count=0,
            )

        assert intent == INTENT_TRUE_TEXT
        assert source == "llm_true_text"
        sent_prompt = mock_complete.await_args.kwargs["prompt"]
        assert "attached" not in sent_prompt.lower()

    @pytest.mark.asyncio
    async def test_prompt_includes_singular_hint_for_one_file(self):
        with patch(
            "server.services.slack_intent_classifier.CompletionService.complete",
            new=AsyncMock(return_value="TRUE_TEXT"),
        ) as mock_complete:
            await classify_intent(
                text="fix this",
                history=[],
                llm_connection_id=uuid4(),
                session=AsyncMock(),
                attachment_count=1,
            )

        sent_prompt = mock_complete.await_args.kwargs["prompt"]
        assert "attached 1 file" in sent_prompt
        assert "attached 1 files" not in sent_prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_plural_hint_for_many_files(self):
        with patch(
            "server.services.slack_intent_classifier.CompletionService.complete",
            new=AsyncMock(return_value="TRUE_REACT"),
        ) as mock_complete:
            await classify_intent(
                text="look at these",
                history=[],
                llm_connection_id=uuid4(),
                session=AsyncMock(),
                attachment_count=3,
            )

        sent_prompt = mock_complete.await_args.kwargs["prompt"]
        assert "attached 3 files" in sent_prompt

    @pytest.mark.asyncio
    async def test_attachment_count_defaults_to_zero(self):
        with patch(
            "server.services.slack_intent_classifier.CompletionService.complete",
            new=AsyncMock(return_value="SKIP"),
        ) as mock_complete:
            await classify_intent(
                text="noise",
                history=[],
                llm_connection_id=uuid4(),
                session=AsyncMock(),
            )

        sent_prompt = mock_complete.await_args.kwargs["prompt"]
        assert "attached" not in sent_prompt.lower()
