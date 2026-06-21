from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.openrouter import OpenRouterBackend, _ENDPOINT

_FAKE_KEY = "or-test-key-abc"


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
    mock_client=None, model: str = "openai/whisper-large-v3-turbo"
) -> OpenRouterBackend:
    return OpenRouterBackend(
        model=model,
        api_key=_FAKE_KEY,
        http_client=mock_client,
        language="ko",
        on_partial=_noop,
        on_final=_noop,
    )


@pytest.mark.asyncio
async def test_transcribe_posts_to_correct_url():
    mock_client = _make_mock_client({"text": "안녕하세요"})
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert (
        call_kwargs[0][0] == _ENDPOINT
        or call_kwargs.kwargs.get("url") == _ENDPOINT
        or _ENDPOINT in str(call_kwargs)
    )


@pytest.mark.asyncio
async def test_transcribe_sends_model_in_payload():
    mock_client = _make_mock_client({"text": "hello"})
    backend = _make_backend(mock_client, model="openai/whisper-large-v3-turbo")
    await backend.transcribe(b"\x00" * 100, "ko")
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert payload["model"] == "openai/whisper-large-v3-turbo"


@pytest.mark.asyncio
async def test_transcribe_sends_auth_header():
    mock_client = _make_mock_client({"text": "hello"})
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
    assert headers["Authorization"] == f"Bearer {_FAKE_KEY}"


@pytest.mark.asyncio
async def test_transcribe_sends_base64_wav_payload():
    mock_client = _make_mock_client({"text": "hello"})
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert "input_audio" in payload
    assert payload["input_audio"]["format"] == "wav"
    assert isinstance(payload["input_audio"]["data"], str)
    assert len(payload["input_audio"]["data"]) > 0


@pytest.mark.asyncio
async def test_transcribe_returns_text_from_response():
    mock_client = _make_mock_client({"text": "테스트 전사"})
    backend = _make_backend(mock_client)
    result = await backend.transcribe(b"\x00" * 100, "ko")
    assert result == "테스트 전사"


@pytest.mark.asyncio
async def test_missing_api_key_raises():
    import os

    old = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            OpenRouterBackend(
                language="ko",
                on_partial=_noop,
                on_final=_noop,
            )
    finally:
        if old is not None:
            os.environ["OPENROUTER_API_KEY"] = old
