from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.groq import GroqBackend, _ENDPOINT

_FAKE_KEY = "gsk-test-key-abc"


def _noop(t: Transcript) -> None:
    pass


def _make_mock_client(response_json: dict) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=response_json)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


def _make_backend(
    mock_client: MagicMock | None = None,
    model: str = "whisper-large-v3-turbo",
) -> GroqBackend:
    return GroqBackend(
        model=model,
        api_key=_FAKE_KEY,
        http_client=mock_client,
        language="ko",
        on_partial=_noop,
        on_final=_noop,
    )


async def test_transcribe_posts_to_correct_endpoint():
    mock_client = _make_mock_client({"text": "안녕하세요"})
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == _ENDPOINT


async def test_transcribe_sends_bearer_auth():
    mock_client = _make_mock_client({"text": "hello"})
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
    assert headers["Authorization"] == f"Bearer {_FAKE_KEY}"


async def test_transcribe_sends_multipart_wav_file():
    mock_client = _make_mock_client({"text": "hello"})
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    call_kwargs = mock_client.post.call_args
    files = call_kwargs.kwargs.get("files") or call_kwargs[1].get("files")
    assert files is not None
    assert "file" in files
    file_entry = files["file"]
    # Entry is a (filename, bytes, content_type) tuple
    wav_bytes = file_entry[1] if isinstance(file_entry, tuple) else file_entry
    assert wav_bytes[:4] == b"RIFF"


async def test_transcribe_sends_model_in_data():
    mock_client = _make_mock_client({"text": "hello"})
    backend = _make_backend(mock_client, model="whisper-large-v3-turbo")
    await backend.transcribe(b"\x00" * 100, "ko")
    call_kwargs = mock_client.post.call_args
    data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
    assert data["model"] == "whisper-large-v3-turbo"


async def test_transcribe_sends_language_in_data():
    mock_client = _make_mock_client({"text": "hello"})
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    call_kwargs = mock_client.post.call_args
    data = call_kwargs.kwargs.get("data") or call_kwargs[1].get("data")
    assert data["language"] == "ko"


async def test_transcribe_returns_text_from_response():
    mock_client = _make_mock_client({"text": "테스트 전사"})
    backend = _make_backend(mock_client)
    result = await backend.transcribe(b"\x00" * 100, "ko")
    assert result == "테스트 전사"


async def test_transcribe_empty_text_returns_empty_string():
    mock_client = _make_mock_client({"text": ""})
    backend = _make_backend(mock_client)
    result = await backend.transcribe(b"\x00" * 100, "ko")
    assert result == ""


async def test_transcribe_whitespace_text_returns_whitespace():
    mock_client = _make_mock_client({"text": "   "})
    backend = _make_backend(mock_client)
    result = await backend.transcribe(b"\x00" * 100, "ko")
    # transcribe() returns raw; flush() strips — keep contract clean
    assert result == "   "


async def test_transcribe_http_error_propagates():
    resp = MagicMock()
    resp.raise_for_status = MagicMock(side_effect=Exception("HTTP 401"))
    resp.json = MagicMock(return_value={})
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=resp)
    backend = _make_backend(mock_client)
    with pytest.raises(Exception, match="HTTP 401"):
        await backend.transcribe(b"\x00" * 100, "ko")


async def test_missing_api_key_raises():
    old = os.environ.pop("GROQ_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="GROQ_API_KEY"):
            GroqBackend(
                language="ko",
                on_partial=_noop,
                on_final=_noop,
            )
    finally:
        if old is not None:
            os.environ["GROQ_API_KEY"] = old
