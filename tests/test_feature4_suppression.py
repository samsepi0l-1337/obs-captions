"""Tests for Feature 4: hallucination suppression (should_suppress + wiring)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from obs_captions.config import AppConfig, ReplacementRule, TextConfig
from obs_captions.pipeline import CaptionSnapshot, CaptionState
from obs_captions.stt.base import Transcript
from obs_captions.text import should_suppress


# ──────────────────────────────────────────────────────────────────────────────
# should_suppress
# ──────────────────────────────────────────────────────────────────────────────


class TestShouldSuppress:
    def test_blank_suppressed_by_default(self):
        cfg = TextConfig()
        assert should_suppress("", cfg) is True

    def test_whitespace_only_suppressed_by_default(self):
        cfg = TextConfig()
        assert should_suppress("   ", cfg) is True

    def test_blank_not_suppressed_when_disabled(self):
        cfg = TextConfig(suppress_blank=False)
        assert should_suppress("", cfg) is False

    def test_whitespace_not_suppressed_when_disabled(self):
        cfg = TextConfig(suppress_blank=False)
        assert should_suppress("   ", cfg) is False

    def test_normal_text_not_suppressed_defaults(self):
        """Core invariant: non-blank text is NEVER suppressed under default config."""
        cfg = TextConfig()
        assert should_suppress("hello world", cfg) is False

    def test_normal_text_not_suppressed_with_rules(self):
        """Non-matching text passes through even with active suppress rules."""
        cfg = TextConfig(suppress_regex=[r"^bad phrase$"], suppress_exact=["also bad"])
        assert should_suppress("hello world", cfg) is False

    def test_regex_fullmatch_suppresses(self):
        cfg = TextConfig(suppress_regex=[r"thank you.*"])
        assert should_suppress("thank you for watching", cfg) is True

    def test_regex_partial_does_not_suppress(self):
        """fullmatch: pattern must match the ENTIRE stripped text, not a substring."""
        cfg = TextConfig(suppress_regex=[r"thank you"])
        assert should_suppress("thank you for watching", cfg) is False

    def test_regex_case_insensitive(self):
        cfg = TextConfig(suppress_regex=[r"thank you"])
        assert should_suppress("THANK YOU", cfg) is True

    def test_regex_strips_before_match(self):
        cfg = TextConfig(suppress_regex=[r"hello"])
        assert should_suppress("  hello  ", cfg) is True

    def test_exact_suppresses_case_insensitive(self):
        cfg = TextConfig(suppress_exact=["Thank You For Watching"])
        assert should_suppress("thank you for watching", cfg) is True

    def test_exact_strips_before_match(self):
        cfg = TextConfig(suppress_exact=["hello"])
        assert should_suppress("  hello  ", cfg) is True

    def test_exact_no_match_passes_through(self):
        cfg = TextConfig(suppress_exact=["goodbye"])
        assert should_suppress("hello", cfg) is False

    def test_multiple_sources_any_triggers(self):
        cfg = TextConfig(
            suppress_regex=[r"pattern\d+"],
            suppress_exact=["exact phrase"],
        )
        assert should_suppress("pattern123", cfg) is True
        assert should_suppress("exact phrase", cfg) is True
        assert should_suppress("unrelated", cfg) is False

    def test_default_config_korean_text_not_suppressed(self):
        """Korean captions must never be suppressed under default config."""
        cfg = TextConfig()
        assert should_suppress("안녕하세요 여러분", cfg) is False

    def test_exact_unicode_casefold(self):
        """suppress_exact uses casefold() so German ß↔ss equivalence is honored.

        str.lower() fails: 'straße'.lower() == 'straße' != 'strasse' == 'STRASSE'.lower().
        str.casefold() expands ß→ss, making the comparison correct.
        """
        cfg = TextConfig(suppress_exact=["STRASSE"])
        assert should_suppress("straße", cfg) is True

    def test_exact_casefold_normal_ascii(self):
        """casefold() is a superset of lower() for ASCII — normal captions unaffected."""
        cfg = TextConfig(suppress_exact=["Hello"])
        assert should_suppress("hello", cfg) is True
        assert should_suppress("HELLO", cfg) is True


# ──────────────────────────────────────────────────────────────────────────────
# Config — suppression fields
# ──────────────────────────────────────────────────────────────────────────────


class TestSuppressConfig:
    def test_suppress_blank_defaults_true(self):
        cfg = TextConfig()
        assert cfg.suppress_blank is True

    def test_suppress_regex_defaults_empty(self):
        cfg = TextConfig()
        assert cfg.suppress_regex == []

    def test_suppress_exact_defaults_empty(self):
        cfg = TextConfig()
        assert cfg.suppress_exact == []

    def test_suppress_regex_invalid_raises_value_error(self):
        with pytest.raises(ValidationError, match="suppress_regex"):
            TextConfig(suppress_regex=["[not-valid-regex"])

    def test_suppress_regex_valid_accepted(self):
        cfg = TextConfig(suppress_regex=[r"thank you.*", r"^\s*$"])
        assert len(cfg.suppress_regex) == 2

    def test_text_config_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            TextConfig(nonexistent_field="x")  # type: ignore[call-arg]

    def test_appconfig_text_suppress_blank_default(self):
        cfg = AppConfig()
        assert cfg.text.suppress_blank is True

    def test_suppress_blank_false_accepted(self):
        cfg = TextConfig(suppress_blank=False)
        assert cfg.suppress_blank is False

    def test_suppress_exact_list_accepted(self):
        cfg = TextConfig(suppress_exact=["phrase one", "phrase two"])
        assert cfg.suppress_exact == ["phrase one", "phrase two"]


# ──────────────────────────────────────────────────────────────────────────────
# Suppression wiring: _build_caption_callbacks
# ──────────────────────────────────────────────────────────────────────────────


class TestSuppressionWiring:
    def _make_cfg(self, **text_kwargs: object) -> AppConfig:
        return AppConfig(text=TextConfig(**text_kwargs))  # type: ignore[arg-type]

    def test_blank_final_does_not_reach_state(self):
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="", is_final=True))
        assert changes == []

    def test_non_blank_final_reaches_state(self):
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="hello world", is_final=True))
        assert len(changes) == 1
        assert changes[0].committed == ["hello world"]

    def test_blank_partial_does_not_reach_state(self):
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        on_partial, _ = _build_caption_callbacks(cfg, state)
        on_partial(Transcript(text="  ", is_final=False))
        assert changes == []

    def test_non_blank_partial_reaches_state(self):
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        on_partial, _ = _build_caption_callbacks(cfg, state)
        on_partial(Transcript(text="안녕", is_final=False))
        assert len(changes) == 1
        assert changes[0].partial == "안녕"

    def test_regex_suppressed_final_dropped(self):
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_regex=[r"thank you.*"])
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="thank you for watching", is_final=True))
        assert changes == []

    def test_exact_suppressed_final_dropped(self):
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_exact=["thank you for watching"])
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="thank you for watching", is_final=True))
        assert changes == []

    def test_suppress_blank_false_blank_final_reaches_state(self):
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=False)
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="", is_final=True))
        assert len(changes) == 1

    def test_export_sink_not_called_for_suppressed_final(self):
        """Suppressed finals must not be forwarded to the export sink."""
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        export_calls: list[Transcript] = []

        class FakeExportSink:
            def on_final(self, tr: Transcript) -> None:
                export_calls.append(tr)

        _, on_final = _build_caption_callbacks(cfg, state, FakeExportSink())
        on_final(Transcript(text="", is_final=True))
        assert export_calls == []

    def test_export_sink_called_for_non_suppressed_final(self):
        """Non-suppressed finals must still reach the export sink."""
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        export_calls: list[Transcript] = []

        class FakeExportSink:
            def on_final(self, tr: Transcript) -> None:
                export_calls.append(tr)

        _, on_final = _build_caption_callbacks(cfg, state, FakeExportSink())
        on_final(Transcript(text="hello", is_final=True))
        assert len(export_calls) == 1
        assert export_calls[0].text == "hello"

    def test_transform_to_blank_causes_suppression(self):
        """Replacement converts non-blank input to blank → suppressed (transform before suppress)."""
        from obs_captions.cli import _build_caption_callbacks

        cfg = AppConfig(text=TextConfig(
            replacements=[ReplacementRule(match="BLANK", replace="")],
            suppress_blank=True,
        ))
        state = CaptionState()
        changes: list = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="BLANK", is_final=True))
        assert changes == []

    def test_transform_to_suppress_exact_causes_suppression(self):
        """Replacement converts input to a suppress_exact phrase → suppressed."""
        from obs_captions.cli import _build_caption_callbacks

        cfg = AppConfig(text=TextConfig(
            replacements=[ReplacementRule(match="noise input", replace="thank you for watching")],
            suppress_exact=["thank you for watching"],
        ))
        state = CaptionState()
        changes: list = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="noise input", is_final=True))
        assert changes == []

    def test_transform_to_blank_partial_suppressed(self):
        """Transform-to-blank suppression works for partials too."""
        from obs_captions.cli import _build_caption_callbacks

        cfg = AppConfig(text=TextConfig(
            replacements=[ReplacementRule(match="hm", replace="")],
            suppress_blank=True,
        ))
        state = CaptionState()
        changes: list = []
        state.subscribe(changes.append)
        on_partial, _ = _build_caption_callbacks(cfg, state)
        on_partial(Transcript(text="hm", is_final=False))
        assert changes == []

    def test_transform_suppressed_final_not_forwarded_to_export_sink(self):
        """Finals suppressed after transform must not reach export_sink."""
        from obs_captions.cli import _build_caption_callbacks

        cfg = AppConfig(text=TextConfig(
            replacements=[ReplacementRule(match="noise", replace="")],
            suppress_blank=True,
        ))
        state = CaptionState()
        export_calls: list = []

        class FakeExportSink:
            def on_final(self, tr: Transcript) -> None:
                export_calls.append(tr)

        _, on_final = _build_caption_callbacks(cfg, state, FakeExportSink())
        on_final(Transcript(text="noise", is_final=True))
        assert export_calls == []

    def test_blank_partial_clears_stale_partial(self):
        """After a non-blank partial, a blank partial must clear CaptionState.partial.

        Previously the blank partial was silently dropped, leaving the prior
        non-blank partial visible on screen (stale display). Correct semantics:
        emit an empty partial so the partial display is wiped.
        """
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        on_partial, _ = _build_caption_callbacks(cfg, state)
        on_partial(Transcript(text="hello", is_final=False))
        assert state._partial == "hello"
        on_partial(Transcript(text="   ", is_final=False))
        assert state._partial == ""

    def test_blank_final_still_dropped_not_committed(self):
        """Blank finals are still dropped (not committed) — only partials are cleared."""
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_blank=True)
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        _, on_final = _build_caption_callbacks(cfg, state)
        on_final(Transcript(text="   ", is_final=True))
        assert state._committed == []
        assert changes == []

    def test_blocklisted_nonblank_partial_is_dropped(self):
        """Non-blank partial matching suppress_regex/suppress_exact must be dropped.

        When a non-blank partial's transformed text matches the suppression
        blocklist (should_suppress=True), state.on_partial must NOT be called.
        This tests line 202 (the return in on_partial when should_suppress is True).
        """
        from obs_captions.cli import _build_caption_callbacks

        cfg = self._make_cfg(suppress_regex=[r"광고문구"])
        state = CaptionState()
        changes: list[CaptionSnapshot] = []
        state.subscribe(changes.append)
        on_partial, _ = _build_caption_callbacks(cfg, state)
        on_partial(Transcript(text="광고문구", is_final=False))
        assert changes == []
