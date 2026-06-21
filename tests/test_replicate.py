from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from obs_captions.stt.base import Transcript
from obs_captions.stt.replicate import ReplicateBackend, _PREDICTIONS_URL

_FAKE_TOKEN = "r8-test-token-xyz"


def _noop(t: Transcript) -> None:
    pass


def _make_mock_client(
    create_response: dict,
    poll_responses: list[dict],
) -> MagicMock:
    create_resp = MagicMock()
    create_resp.raise_for_status = MagicMock()
    create_resp.json = MagicMock(return_value=create_response)

    poll_resps = []
    for pr in poll_responses:
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=pr)
        poll_resps.append(r)

    client = MagicMock()
    client.post = AsyncMock(return_value=create_resp)
    client.get = AsyncMock(side_effect=poll_resps)
    return client


def _make_backend(
    mock_client: MagicMock,
    model: str = "openai/whisper",
    version: str = "abc123",
) -> ReplicateBackend:
    sleep_calls: list[float] = []

    def fake_sleep(secs: float) -> None:
        sleep_calls.append(secs)

    backend = ReplicateBackend(
        model=model,
        version=version,
        api_key=_FAKE_TOKEN,
        http_client=mock_client,
        sleep_fn=fake_sleep,
        language="ko",
        on_partial=_noop,
        on_final=_noop,
    )
    backend._sleep_calls = sleep_calls
    return backend


@pytest.mark.asyncio
async def test_creates_prediction_then_polls_to_success():
    pred_id = "pred-001"
    mock_client = _make_mock_client(
        create_response={"id": pred_id, "status": "starting"},
        poll_responses=[
            {"id": pred_id, "status": "processing"},
            {"id": pred_id, "status": "succeeded", "output": {"transcription": "안녕하세요"}},
        ],
    )
    backend = _make_backend(mock_client)
    result = await backend.transcribe(b"\x00" * 100, "ko")

    assert result == "안녕하세요"
    mock_client.post.assert_called_once()
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_creates_prediction_with_correct_url_and_auth():
    pred_id = "pred-002"
    mock_client = _make_mock_client(
        create_response={"id": pred_id, "status": "starting"},
        poll_responses=[
            {"id": pred_id, "status": "succeeded", "output": {"transcription": "test"}},
        ],
    )
    backend = _make_backend(mock_client, version="ver-xyz")
    await backend.transcribe(b"\x00" * 100, "ko")

    post_call = mock_client.post.call_args
    assert post_call[0][0] == _PREDICTIONS_URL
    headers = post_call.kwargs.get("headers") or post_call[1].get("headers")
    assert headers["Authorization"] == f"Bearer {_FAKE_TOKEN}"
    payload = post_call.kwargs.get("json") or post_call[1].get("json")
    assert payload["version"] == "ver-xyz"
    assert "audio" in payload["input"]


@pytest.mark.asyncio
async def test_poll_url_uses_prediction_id():
    pred_id = "pred-003"
    mock_client = _make_mock_client(
        create_response={"id": pred_id, "status": "starting"},
        poll_responses=[
            {"id": pred_id, "status": "succeeded", "output": {"transcription": "ok"}},
        ],
    )
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")

    get_call = mock_client.get.call_args
    assert f"{_PREDICTIONS_URL}/{pred_id}" in get_call[0][0]


@pytest.mark.asyncio
async def test_failed_prediction_raises():
    pred_id = "pred-fail"
    mock_client = _make_mock_client(
        create_response={"id": pred_id, "status": "starting"},
        poll_responses=[
            {"id": pred_id, "status": "failed", "error": "OOM"},
        ],
    )
    backend = _make_backend(mock_client)
    with pytest.raises(RuntimeError, match="failed"):
        await backend.transcribe(b"\x00" * 100, "ko")


@pytest.mark.asyncio
async def test_sleep_called_between_polls():
    pred_id = "pred-sleep"
    mock_client = _make_mock_client(
        create_response={"id": pred_id, "status": "starting"},
        poll_responses=[
            {"id": pred_id, "status": "processing"},
            {"id": pred_id, "status": "succeeded", "output": {"transcription": "x"}},
        ],
    )
    backend = _make_backend(mock_client)
    await backend.transcribe(b"\x00" * 100, "ko")
    assert len(backend._sleep_calls) == 2  # one sleep per poll iteration


@pytest.mark.asyncio
async def test_missing_api_token_raises():
    import os

    old = os.environ.pop("REPLICATE_API_TOKEN", None)
    try:
        with pytest.raises(ValueError, match="REPLICATE_API_TOKEN"):
            ReplicateBackend(
                language="ko",
                on_partial=_noop,
                on_final=_noop,
            )
    finally:
        if old is not None:
            os.environ["REPLICATE_API_TOKEN"] = old


@pytest.mark.asyncio
async def test_max_polls_exhausted_raises_timeout_error(monkeypatch):
    """When all poll attempts return a non-terminal status, TimeoutError is raised."""
    import obs_captions.stt.replicate as replicate_mod

    pred_id = "pred-timeout"
    # Provide more poll responses than _MAX_POLLS so side_effect never runs short
    poll_responses = [{"id": pred_id, "status": "processing"}] * 5
    mock_client = _make_mock_client(
        create_response={"id": pred_id, "status": "starting"},
        poll_responses=poll_responses,
    )
    backend = _make_backend(mock_client)

    monkeypatch.setattr(replicate_mod, "_MAX_POLLS", 3)

    with pytest.raises(TimeoutError, match=pred_id):
        await backend.transcribe(b"\x00" * 100, "ko")
