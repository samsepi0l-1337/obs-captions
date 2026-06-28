"""Tests for Feature 5: per-line character wrap (wrap_text + renderer wiring)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from obs_captions.config import AppConfig, OverlayConfig
from obs_captions.pipeline import CaptionSnapshot, CaptionState
from obs_captions.stt.base import Transcript
from obs_captions.text import wrap_text


# ──────────────────────────────────────────────────────────────────────────────
# wrap_text
# ──────────────────────────────────────────────────────────────────────────────


class TestWrapText:
    def test_disabled_when_zero(self):
        assert wrap_text("hello world", 0) == ["hello world"]

    def test_disabled_when_negative(self):
        assert wrap_text("hello world", -1) == ["hello world"]

    def test_fits_within_limit_unchanged(self):
        assert wrap_text("hi", 10) == ["hi"]

    def test_exact_boundary_not_wrapped(self):
        assert wrap_text("12345", 5) == ["12345"]

    def test_splits_at_boundary(self):
        assert wrap_text("1234567890", 5) == ["12345", "67890"]

    def test_multi_chunk(self):
        assert wrap_text("abcdefghij", 3) == ["abc", "def", "ghi", "j"]

    def test_korean_codepoint_wrap(self):
        # Each Korean syllable-block is one codepoint (len() is correct here).
        text = "안녕하세요여러분"  # 8 codepoints
        assert wrap_text(text, 5) == ["안녕하세요", "여러분"]

    def test_very_long_single_token(self):
        text = "a" * 100
        result = wrap_text(text, 20)
        assert result == ["a" * 20] * 5

    def test_empty_string(self):
        # Empty string: no wrapping needed, one element returned.
        assert wrap_text("", 10) == [""]

    def test_one_char_max(self):
        assert wrap_text("abc", 1) == ["a", "b", "c"]

    def test_normal_ascii_wrap_is_identity_when_disabled(self):
        """With max_chars=0, wrap_text is a transparent passthrough (no behavior change)."""
        long_text = "a" * 200
        result = wrap_text(long_text, 0)
        assert result == [long_text]


# ──────────────────────────────────────────────────────────────────────────────
# Config — wrap fields
# ──────────────────────────────────────────────────────────────────────────────


class TestWrapConfig:
    def test_max_chars_per_line_default_zero(self):
        cfg = OverlayConfig()
        assert cfg.max_chars_per_line == 0

    def test_overlay_max_chars_accepted(self):
        cfg = OverlayConfig(max_chars_per_line=30)
        assert cfg.max_chars_per_line == 30

    def test_overlay_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            OverlayConfig(nonexistent_field="x")  # type: ignore[call-arg]

    def test_appconfig_overlay_max_chars_default(self):
        cfg = AppConfig()
        assert cfg.overlay.max_chars_per_line == 0

    def test_appconfig_no_extra_config_non_blank_caption_unaffected(self):
        """Default AppConfig: non-blank finals are never suppressed, wrap is identity."""
        from obs_captions.text import should_suppress

        cfg = AppConfig()
        # Non-blank text is never suppressed.
        assert should_suppress("hello world", cfg.text) is False
        # Wrap disabled by default: list of one unchanged string.
        assert wrap_text("hello world", cfg.overlay.max_chars_per_line) == ["hello world"]


# ──────────────────────────────────────────────────────────────────────────────
# Path B: obs_sink._build_display_text
# ──────────────────────────────────────────────────────────────────────────────


class TestPathBWiring:
    def test_no_wrap_when_max_chars_zero(self):
        from obs_captions.obs_sink import _build_display_text

        snap = CaptionSnapshot(committed=["hello world"], partial="")
        assert _build_display_text(snap, max_chars=0) == "hello world"

    def test_wraps_committed_line(self):
        from obs_captions.obs_sink import _build_display_text

        snap = CaptionSnapshot(committed=["1234567890"], partial="")
        assert _build_display_text(snap, max_chars=5) == "12345\n67890"

    def test_wraps_partial(self):
        from obs_captions.obs_sink import _build_display_text

        snap = CaptionSnapshot(committed=[], partial="abcdefghij")
        assert _build_display_text(snap, max_chars=5) == "abcde\nfghij"

    def test_wraps_both_committed_and_partial(self):
        from obs_captions.obs_sink import _build_display_text

        snap = CaptionSnapshot(committed=["abcdef"], partial="ghijkl")
        assert _build_display_text(snap, max_chars=3) == "abc\ndef\nghi\njkl"

    def test_default_signature_unchanged(self):
        """Calling with no max_chars arg (backward-compat) uses 0 = no wrap."""
        from obs_captions.obs_sink import _build_display_text

        snap = CaptionSnapshot(committed=["hello world long line"], partial="partial text")
        result = _build_display_text(snap)
        assert result == "hello world long line\npartial text"

    def test_max_lines_x_wrap_all_chunks_displayed(self):
        """max_lines caps committed history; wrapping is display-only.

        2 committed logical lines each wrapping to 2 display lines → 4 display
        lines in the OBS text source.  No logical line is dropped.
        """
        from obs_captions.obs_sink import _build_display_text

        snap = CaptionSnapshot(committed=["aaaaaaaaaa", "bbbbbbbbbb"], partial="")
        result = _build_display_text(snap, max_chars=5)
        assert result == "aaaaa\naaaaa\nbbbbb\nbbbbb"


# ──────────────────────────────────────────────────────────────────────────────
# Path A: server/app.py caption_state_to_message
# ──────────────────────────────────────────────────────────────────────────────


class TestPathAWiring:
    def test_no_wrap_when_max_chars_zero(self):
        from obs_captions.server.app import caption_state_to_message

        snap = CaptionSnapshot(committed=["hello world"], partial="")
        msg = caption_state_to_message(snap, max_chars=0)
        assert msg["committed"] == ["hello world"]
        assert msg["partial"] == ""

    def test_wraps_committed_into_flat_list(self):
        from obs_captions.server.app import caption_state_to_message

        snap = CaptionSnapshot(committed=["1234567890"], partial="")
        msg = caption_state_to_message(snap, max_chars=5)
        assert msg["committed"] == ["12345", "67890"]

    def test_wraps_partial_with_newline(self):
        from obs_captions.server.app import caption_state_to_message

        snap = CaptionSnapshot(committed=[], partial="abcdefghij")
        msg = caption_state_to_message(snap, max_chars=5)
        assert msg["partial"] == "abcde\nfghij"

    def test_message_type_preserved(self):
        from obs_captions.server.app import caption_state_to_message

        snap = CaptionSnapshot(committed=[], partial="")
        msg = caption_state_to_message(snap, max_chars=0)
        assert msg["type"] == "caption"

    def test_default_signature_unchanged(self):
        """No max_chars arg (backward-compat) = no wrap."""
        from obs_captions.server.app import caption_state_to_message

        snap = CaptionSnapshot(committed=["hello world long line"], partial="partial")
        msg = caption_state_to_message(snap)
        assert msg["committed"] == ["hello world long line"]
        assert msg["partial"] == "partial"

    def test_max_lines_x_wrap_no_logical_line_dropped_path_a_equals_path_b(self):
        """Path A and Path B agree: max_lines=2 committed × 2 wrap chunks = 4 display lines.

        The overlay.js no longer re-slices the flat list, so all 4 entries are
        rendered.  Wrapping is display-only — no logical committed line is dropped.
        """
        from obs_captions.obs_sink import _build_display_text
        from obs_captions.server.app import caption_state_to_message

        snap = CaptionSnapshot(committed=["aaaaaaaaaa", "bbbbbbbbbb"], partial="")
        msg = caption_state_to_message(snap, max_chars=5)
        # All 4 display chunks present; none dropped.
        assert msg["committed"] == ["aaaaa", "aaaaa", "bbbbb", "bbbbb"]
        # Path A and Path B agree on display content.
        path_b = _build_display_text(snap, max_chars=5)
        assert "\n".join(msg["committed"]) == path_b

    async def test_wire_caption_state_broadcasts_wrapped(self):
        """Integration: wire_caption_state with max_chars_per_line>0 broadcasts wrapped committed."""
        from obs_captions.server.app import wire_caption_state

        state = CaptionState()
        hub = MagicMock()
        broadcast_calls: list[dict] = []

        async def fake_broadcast(msg: dict) -> None:
            broadcast_calls.append(dict(msg))

        hub.broadcast = AsyncMock(side_effect=fake_broadcast)

        loop = asyncio.get_running_loop()
        wire_caption_state(state, hub, loop=loop, max_chars_per_line=5)

        # Trigger a final that produces a 10-char committed line.
        state.on_final(Transcript(text="1234567890", is_final=True))

        # Drain the event loop so the threadsafe-scheduled coroutine runs.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert broadcast_calls, "hub.broadcast was never called"
        msg = broadcast_calls[-1]
        assert msg["type"] == "caption"
        assert msg["committed"] == ["12345", "67890"], f"Got {msg['committed']!r}"
