"""Lightweight STT engine API-key validation.

``validate_engine`` performs a cheap, auth-only probe of an STT provider so the
GUI/plugin/CLI can tell a user whether their key actually works before a live
session. Pure logic + a single injectable ``http_get`` hook so it is fully
testable without network access.

Security: the API key and any response body are NEVER placed in a returned
message or logged — only the engine name and HTTP status code are surfaced.

Per-engine network support (see ``_NETWORK`` / ``_UNSUPPORTED`` below):

* REST engines with a confirmed auth header — openai, deepgram, elevenlabs,
  groq, openrouter, xai, replicate — do a real authenticated ``GET`` and map
  the status code. Their auth-header shape was read from the backend modules
  (openai/xai/groq/openrouter/replicate ``Bearer``, deepgram ``Token``,
  elevenlabs ``xi-api-key``).
* ``google`` (Gemini) authenticates via a ``?key=`` query param, so it is also
  network-validated. NOTE: a rejected Gemini key can return HTTP 400 rather
  than 401/403, so the "인증 실패" mapping for google specifically needs one
  manual live observation before it is fully trusted.
* ``assemblyai`` is streaming-only here and its REST auth-failure surface was
  not confirmed, so it is left ``unsupported`` (format check only) rather than
  guessing. ``azure`` uses SDK auth (subscription key + region) that a plain
  ``GET`` cannot verify: missing region is a ``format`` failure, otherwise it
  is ``unsupported``.

MANUAL PROMOTION: every network engine here is exercised only against mocked
responses. Observing a real 401/403 once per engine (and google's 400 case) is
required to fully trust the auth-failure message; until then the 2xx "valid"
path is reliable but the failure classification is best-effort.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

Mode = Literal["network", "format", "unsupported"]

HttpGet = Callable[..., object]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    mode: Mode
    message: str  # 한국어 사용자 친화 메시지 (키/응답 본문 노출 금지)


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# engine -> builder(api_key) -> (url, headers)
_NETWORK: dict[str, Callable[[str], tuple[str, dict[str, str]]]] = {
    "openai": lambda k: ("https://api.openai.com/v1/models", _bearer(k)),
    "deepgram": lambda k: ("https://api.deepgram.com/v1/projects", {"Authorization": f"Token {k}"}),
    "elevenlabs": lambda k: ("https://api.elevenlabs.io/v1/user", {"xi-api-key": k}),
    "groq": lambda k: ("https://api.groq.com/openai/v1/models", _bearer(k)),
    "openrouter": lambda k: ("https://openrouter.ai/api/v1/models", _bearer(k)),
    "xai": lambda k: ("https://api.x.ai/v1/models", _bearer(k)),
    "replicate": lambda k: ("https://api.replicate.com/v1/account", _bearer(k)),
    "google": lambda k: (
        f"https://generativelanguage.googleapis.com/v1beta/models?key={k}",
        {},
    ),
}

# Engines with no confirmed network auth-failure surface -> format check only.
_UNSUPPORTED: frozenset[str] = frozenset({"assemblyai"})


def _default_get(url: str, *, headers: dict[str, str], timeout: float) -> object:
    import httpx

    return httpx.get(url, headers=headers, timeout=timeout)


def _map_status(status: object) -> ValidationResult:
    if not isinstance(status, int):
        return ValidationResult(False, "network", "서버 응답을 해석할 수 없습니다.")
    if 200 <= status < 300:
        return ValidationResult(True, "network", "키가 정상 확인되었습니다.")
    if status in (401, 403):
        return ValidationResult(False, "network", "인증 실패: 키를 확인하세요.")
    return ValidationResult(
        False, "network", f"검증 실패(HTTP {status}). 잠시 후 다시 시도하세요."
    )


def _validate_azure(extra: dict | None) -> ValidationResult:
    region = str((extra or {}).get("region") or "").strip()
    if not region:
        return ValidationResult(
            False, "format", "Azure는 region 설정이 필요합니다. region을 입력하세요."
        )
    # SDK-based auth (subscription key + region) cannot be checked with a GET.
    return ValidationResult(
        False, "unsupported", "Azure 키는 자동 검증을 지원하지 않습니다. 실행 시 확인됩니다."
    )


def validate_engine(
    engine: str,
    api_key: str,
    extra: dict | None = None,
    *,
    http_get: HttpGet | None = None,
    timeout: float = 8.0,
) -> ValidationResult:
    """Validate ``api_key`` for ``engine`` with a cheap auth-only probe."""
    key = (api_key or "").strip()
    if not key:
        return ValidationResult(False, "format", "키가 비어 있습니다.")

    if engine == "azure":
        return _validate_azure(extra)

    if engine in _UNSUPPORTED:
        return ValidationResult(
            False,
            "unsupported",
            f"'{engine}' 엔진은 자동 키 검증을 지원하지 않습니다. 키 형식만 확인했습니다.",
        )

    builder = _NETWORK.get(engine)
    if builder is None:
        return ValidationResult(
            False,
            "unsupported",
            f"'{engine}' 엔진은 자동 키 검증을 지원하지 않습니다. 키 형식만 확인했습니다.",
        )

    url, headers = builder(key)
    getter = http_get or _default_get
    try:
        response = getter(url, headers=headers, timeout=timeout)
    except Exception:
        # Never surface exception text — it can echo the URL (with google's key).
        return ValidationResult(
            False, "network", "네트워크 오류로 키를 확인할 수 없습니다. 연결을 확인하세요."
        )

    return _map_status(getattr(response, "status_code", None))
