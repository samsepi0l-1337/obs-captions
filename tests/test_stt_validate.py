"""Tests for STT engine API-key validation (``stt/validate.py``).

All network access is mocked via the injectable ``http_get`` hook; no real
HTTP call is ever made here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from obs_captions.stt.validate import ValidationResult, validate_engine

_KEY = "secret-key-123"


class _Recorder:
    """Injectable ``http_get`` stub recording url/headers/timeout."""

    def __init__(self, status: int | None = 200, exc: Exception | None = None):
        self._status = status
        self._exc = exc
        self.calls: list[dict] = []

    def __call__(self, url, *, headers, timeout):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(status_code=self._status)


# engine -> (expected url, expected headers) for pure-header network backends.
_NETWORK_CASES = {
    "openai": ("https://api.openai.com/v1/models", {"Authorization": f"Bearer {_KEY}"}),
    "deepgram": ("https://api.deepgram.com/v1/projects", {"Authorization": f"Token {_KEY}"}),
    "elevenlabs": ("https://api.elevenlabs.io/v1/user", {"xi-api-key": _KEY}),
    "groq": ("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {_KEY}"}),
    "openrouter": ("https://openrouter.ai/api/v1/models", {"Authorization": f"Bearer {_KEY}"}),
    "xai": ("https://api.x.ai/v1/models", {"Authorization": f"Bearer {_KEY}"}),
    "replicate": ("https://api.replicate.com/v1/account", {"Authorization": f"Bearer {_KEY}"}),
}


@pytest.mark.parametrize("engine", sorted(_NETWORK_CASES))
def test_network_engine_success_maps_url_and_headers(engine):
    url, headers = _NETWORK_CASES[engine]
    rec = _Recorder(status=200)
    result = validate_engine(engine, _KEY, http_get=rec, timeout=3.0)

    assert isinstance(result, ValidationResult)
    assert result.ok is True
    assert result.mode == "network"
    assert len(rec.calls) == 1
    assert rec.calls[0]["url"] == url
    assert rec.calls[0]["headers"] == headers
    assert rec.calls[0]["timeout"] == 3.0


def test_google_uses_query_param_key_not_header():
    rec = _Recorder(status=200)
    result = validate_engine("google", _KEY, http_get=rec)

    assert result.ok is True
    assert result.mode == "network"
    call = rec.calls[0]
    assert f"key={_KEY}" in call["url"]
    assert call["url"].startswith("https://generativelanguage.googleapis.com/")
    assert call["headers"] == {}


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_status(status):
    rec = _Recorder(status=status)
    result = validate_engine("openai", _KEY, http_get=rec)

    assert result.ok is False
    assert result.mode == "network"
    assert "인증 실패" in result.message


@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_other_error_status_includes_code(status):
    rec = _Recorder(status=status)
    result = validate_engine("openai", _KEY, http_get=rec)

    assert result.ok is False
    assert result.mode == "network"
    assert str(status) in result.message


def test_network_exception_is_friendly_and_leaks_nothing():
    rec = _Recorder(exc=RuntimeError("connect timed out to host"))
    result = validate_engine("openai", _KEY, http_get=rec)

    assert result.ok is False
    assert result.mode == "network"
    assert _KEY not in result.message
    assert "connect timed out to host" not in result.message


@pytest.mark.parametrize("key", ["", "   ", "\t\n"])
def test_blank_key_is_format_failure_without_network(key):
    rec = _Recorder(status=200)
    result = validate_engine("openai", key, http_get=rec)

    assert result.ok is False
    assert result.mode == "format"
    assert rec.calls == []


def test_unsupported_engine_assemblyai():
    rec = _Recorder(status=200)
    result = validate_engine("assemblyai", _KEY, http_get=rec)

    assert result.ok is False
    assert result.mode == "unsupported"
    assert rec.calls == []


def test_azure_without_region_is_format_failure():
    result = validate_engine("azure", _KEY, extra=None)
    assert result.ok is False
    assert result.mode == "format"


def test_azure_with_region_is_unsupported():
    result = validate_engine("azure", _KEY, extra={"region": "eastus"})
    assert result.ok is False
    assert result.mode == "unsupported"


def test_message_never_contains_key_on_success_or_failure():
    for status in (200, 401, 500):
        rec = _Recorder(status=status)
        result = validate_engine("groq", _KEY, http_get=rec)
        assert _KEY not in result.message
