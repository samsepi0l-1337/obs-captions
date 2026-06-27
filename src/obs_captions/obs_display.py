"""Pure display-text building helpers for OBS captions.

No I/O, no asyncio, no side effects — only stateless transformation of
caption snapshots into display strings.
"""
from __future__ import annotations

from typing import Any


def _build_display_text(snapshot: Any, max_chars: int = 0) -> str:
    """Join committed + partial into display string.

    Wraps each segment at *max_chars* codepoints when > 0 (len() is correct for
    Korean Hangul). max_lines caps transcript history; wrapping is display-only.
    """
    from obs_captions.text import wrap_text

    parts = list(snapshot.committed)
    if snapshot.partial:
        parts.append(snapshot.partial)
    if max_chars > 0:
        wrapped: list[str] = []
        for part in parts:
            wrapped.extend(wrap_text(part, max_chars))
        return "\n".join(wrapped)
    return "\n".join(parts)
