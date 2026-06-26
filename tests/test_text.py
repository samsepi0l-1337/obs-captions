"""Tests for obs_captions.text — pure text-transform functions."""
from __future__ import annotations

import pytest

from obs_captions.config import ExportConfig, TextConfig
from obs_captions.text import ReplacementRule, apply_filter, apply_replacements, transform_text


# ---------------------------------------------------------------------------
# apply_replacements
# ---------------------------------------------------------------------------


def test_apply_replacements_empty_rules():
    assert apply_replacements("hello world", []) == "hello world"


def test_apply_replacements_literal_basic():
    rules = [ReplacementRule(match="whisper", replace="Whisper")]
    assert apply_replacements("use whisper today", rules) == "use Whisper today"


def test_apply_replacements_literal_case_insensitive_default():
    rules = [ReplacementRule(match="WHISPER", replace="Whisper")]
    assert apply_replacements("use whisper today", rules) == "use Whisper today"


def test_apply_replacements_literal_case_sensitive():
    rules = [ReplacementRule(match="whisper", replace="X", ignore_case=False)]
    # "Whisper" should NOT match; only lowercase "whisper" matches
    assert apply_replacements("Whisper whisper", rules) == "Whisper X"


def test_apply_replacements_whole_word_does_not_match_partial():
    rules = [ReplacementRule(match="key", replace="KEY", whole_word=True)]
    assert apply_replacements("monkey key", rules) == "monkey KEY"


def test_apply_replacements_whole_word_matches_standalone():
    rules = [ReplacementRule(match="key", replace="KEY", whole_word=True)]
    assert apply_replacements("the key here", rules) == "the KEY here"


def test_apply_replacements_regex_basic():
    rules = [ReplacementRule(match=r"o+", replace="0", regex=True)]
    assert apply_replacements("foo boo", rules) == "f0 b0"


def test_apply_replacements_regex_case_insensitive():
    rules = [ReplacementRule(match=r"hello", replace="hi", regex=True, ignore_case=True)]
    assert apply_replacements("Hello HELLO hello", rules) == "hi hi hi"


def test_apply_replacements_regex_whole_word():
    rules = [ReplacementRule(match=r"key\w*", replace="KEY", regex=True, whole_word=True)]
    # "keyword" should match (\bkeyword\b), "monkey" should NOT
    assert apply_replacements("keyword key monkey", rules) == "KEY KEY monkey"


def test_apply_replacements_multiple_rules_in_order():
    rules = [
        ReplacementRule(match="a", replace="b"),
        ReplacementRule(match="b", replace="c"),
    ]
    # "a" -> "b" first, then "b" -> "c"; net result "a" -> "c"
    assert apply_replacements("a", rules) == "c"


def test_apply_replacements_invalid_regex_raises_at_construction():
    with pytest.raises(ValueError, match="[Ii]nvalid regex"):
        ReplacementRule(match="[invalid", replace="x", regex=True)


def test_apply_replacements_non_regex_special_chars_allowed():
    # Special regex chars in a non-regex rule are treated as literals
    rule = ReplacementRule(match="(hello)", replace="hi", regex=False)
    assert apply_replacements("say (hello) now", [rule]) == "say hi now"


def test_apply_replacements_valid_regex_no_construction_error():
    rule = ReplacementRule(match=r"\d+", replace="NUM", regex=True)
    assert apply_replacements("42 items", [rule]) == "NUM items"


def test_apply_replacements_preserves_text_when_no_match():
    rules = [ReplacementRule(match="xyz", replace="ABC")]
    assert apply_replacements("hello world", rules) == "hello world"


# ---------------------------------------------------------------------------
# apply_filter
# ---------------------------------------------------------------------------


def test_apply_filter_empty_words():
    assert apply_filter("hello world", [], "mask", "***") == "hello world"


def test_apply_filter_mask_single_word():
    assert apply_filter("damn this", ["damn"], "mask", "***") == "*** this"


def test_apply_filter_mask_multiple_words():
    result = apply_filter("damn this is bad", ["damn", "bad"], "mask", "***")
    assert result == "*** this is ***"


def test_apply_filter_remove_single_word():
    result = apply_filter("damn this", ["damn"], "remove", "***")
    assert result == "this"


def test_apply_filter_remove_normalizes_whitespace():
    result = apply_filter("a damn b", ["damn"], "remove", "***")
    assert result == "a b"


def test_apply_filter_case_insensitive():
    assert apply_filter("DAMN this is BAD", ["damn", "bad"], "mask", "***") == "*** this is ***"


def test_apply_filter_whole_word_does_not_match_partial():
    # "bad" in "badass" should NOT be masked; standalone "bad" should
    assert apply_filter("badass bad", ["bad"], "mask", "***") == "badass ***"


def test_apply_filter_custom_mask():
    assert apply_filter("hell no", ["hell"], "mask", "#@!") == "#@! no"


def test_apply_filter_remove_strips_edges():
    # Leading/trailing spaces after removal are stripped
    result = apply_filter("damn hello", ["damn"], "remove", "***")
    assert result == "hello"


# ---------------------------------------------------------------------------
# transform_text
# ---------------------------------------------------------------------------


def test_transform_text_identity_with_defaults():
    cfg = TextConfig()
    assert transform_text("hello world", cfg) == "hello world"


def test_transform_text_replacements_applied_before_filter():
    # replace "good"->"badword", then filter "badword"->"***"
    # Verifies order: replacements THEN filter
    cfg = TextConfig(
        replacements=[ReplacementRule(match="good", replace="badword")],
        filter_words=["badword"],
        filter_mode="mask",
        filter_mask="***",
    )
    assert transform_text("good", cfg) == "***"


def test_transform_text_both_features_applied():
    cfg = TextConfig(
        replacements=[ReplacementRule(match="whisper", replace="Whisper")],
        filter_words=["hell"],
        filter_mode="mask",
        filter_mask="[bleep]",
    )
    result = transform_text("use whisper and hell yeah", cfg)
    assert result == "use Whisper and [bleep] yeah"


def test_transform_text_filter_remove_mode():
    cfg = TextConfig(
        filter_words=["um", "uh"],
        filter_mode="remove",
        filter_mask="***",
    )
    result = transform_text("um well uh yes", cfg)
    assert result == "well yes"


def test_transform_text_no_text_section_in_config():
    # ExportConfig default is enabled=False, no text
    export_cfg = ExportConfig()
    assert export_cfg.enabled is False
    # TextConfig default should be identity
    cfg = TextConfig()
    assert transform_text("unchanged", cfg) == "unchanged"


# ---------------------------------------------------------------------------
# Regression: whole_word + regex alternation anchoring (Finding 7)
# ---------------------------------------------------------------------------


def test_apply_replacements_regex_whole_word_alternation():
    """whole_word + regex with top-level alternation must bind boundaries to the full pattern.

    Without a non-capturing group, r'\\bcat|dog\\b' matches 'dog' inside 'underdog'
    and 'cat' inside 'category'.  The correct pattern is r'\\b(?:cat|dog)\\b'.
    """
    rules = [ReplacementRule(match=r"cat|dog", replace="PET", regex=True, whole_word=True)]
    # Standalone words match; substrings inside other words must not.
    assert apply_replacements("cat dog category underdog", rules) == "PET PET category underdog"


# ---------------------------------------------------------------------------
# Regression: backslash in literal replace / filter_mask (Finding 8)
# ---------------------------------------------------------------------------


def test_apply_replacements_literal_backslash_replace_is_safe():
    """A backslash in a literal replace must not be interpreted as a group reference."""
    rule = ReplacementRule(match="bad", replace=r"\n", regex=False)
    # Should produce the literal two-character sequence backslash-n, not a newline.
    result = apply_replacements("bad word", [rule])
    assert result == r"\n word"


def test_apply_replacements_regex_invalid_replace_raises_at_construction():
    """Regex replace template with invalid back-reference raises ValueError at construction."""
    with pytest.raises(ValueError, match="[Ii]nvalid replacement"):
        ReplacementRule(match=r"(\w+)", replace=r"\9", regex=True)


def test_apply_replacements_regex_invalid_named_group_ref_raises_at_construction():
    """Invalid named group reference \\g<name> in replace raises ValueError at construction.

    The group-count check only catches numeric refs; this test exercises the
    sample-substitution fallback (text.py lines 57-59) which catches named-ref
    errors when the pattern actually matches the sample string.
    """
    with pytest.raises(ValueError, match="[Ii]nvalid replacement"):
        # match=r"\w+" matches _VALIDATE_SAMPLE so re.sub will try the template
        # and raise re.error because there is no group named 'nonexistent'.
        ReplacementRule(match=r"\w+", replace=r"\g<nonexistent>", regex=True)


def test_apply_filter_mask_with_backslash_is_safe():
    """filter_mask containing a backslash must not cause a runtime re.error."""
    result = apply_filter("damn it", ["damn"], "mask", r"\1")
    assert result == r"\1 it"


# ---------------------------------------------------------------------------
# Regression: apply_filter remove normalises all whitespace classes (Finding 4)
# ---------------------------------------------------------------------------


def test_apply_filter_remove_normalizes_tab_whitespace():
    """Removing a word adjacent to a tab must collapse the tab, not leave it."""
    result = apply_filter("damn\thello", ["damn"], "remove", "***")
    assert result == "hello"
    assert "\t" not in result


# ---------------------------------------------------------------------------
# Sample-substitution fallback (text.py lines 78-81): malformed template that
# bypasses the numeric-ref and named-ref checks (e.g. \\g without <>) is
# caught by the re.sub sample run when the pattern matches the sample string.
# ---------------------------------------------------------------------------


def test_apply_replacements_malformed_template_no_angle_brackets_raises():
    r"""\\g without <> is a malformed back-ref not matched by \\([1-9]\d*) or
    \\g<([^>]+)> — falls through to the sample-substitution path (lines 78-81)
    which raises re.error, wrapped in ValueError."""
    with pytest.raises(ValueError, match="[Ii]nvalid replacement"):
        # r"\w+" matches _VALIDATE_SAMPLE, so re.sub executes the template
        ReplacementRule(match=r"\w+", replace=r"\g", regex=True)


# ---------------------------------------------------------------------------
# Finding 1 fix: inline flag + whole_word raises at construction
# ---------------------------------------------------------------------------


def test_regex_inline_flag_whole_word_raises_at_construction():
    """(?i) at position 0 of a bare pattern becomes position 5 inside \\b(?:...)\\b.
    Python 3.12 rejects global flags not at the start of the expression — this
    must be caught at construction, not at live-caption runtime."""
    with pytest.raises(ValueError, match="[Ii]nvalid regex"):
        ReplacementRule(match=r"(?i)cat", replace="CAT", regex=True, whole_word=True)


# ---------------------------------------------------------------------------
# Finding 2 fix: blank strings in filter_words are ignored
# ---------------------------------------------------------------------------


def test_apply_filter_blank_words_ignored_no_corruption():
    """Blank strings in the words list are stripped before building patterns.
    Without the guard, '' builds r'\\b\\b' which matches every word boundary."""
    result = apply_filter("hello world", ["", "  "], "mask", "***")
    assert result == "hello world"  # identity: no real words, no corruption


def test_apply_filter_mixed_blank_and_real_words():
    """Blank entries are stripped; real words still apply."""
    result = apply_filter("damn hello", ["", "damn", "  "], "mask", "***")
    assert result == "*** hello"


# ---------------------------------------------------------------------------
# Finding 5 fix: named group ref for non-matching pattern raises at construction
# ---------------------------------------------------------------------------


def test_regex_named_group_ref_non_matching_raises_at_construction():
    """A \\g<name> ref for a non-existent group must fail at construction even
    when the pattern does not match the static validation sample (so the
    sample-substitution fallback would never execute the template)."""
    with pytest.raises(ValueError, match="[Ii]nvalid replacement"):
        ReplacementRule(
            match=r"zzz_nomatch",
            replace=r"\g<bad>",
            regex=True,
        )


# ---------------------------------------------------------------------------
# Finding 6 fix: remove-mode only normalizes whitespace when a word was removed
# ---------------------------------------------------------------------------


def test_apply_filter_remove_no_match_preserves_existing_spaces():
    """When no word matches, pre-existing multiple spaces must not be collapsed."""
    result = apply_filter("hello  world", ["curse"], "remove", "***")
    assert result == "hello  world"  # double space preserved — nothing removed


def test_apply_filter_remove_normalizes_only_after_actual_removal():
    """When a word IS removed, whitespace normalisation runs as expected."""
    result = apply_filter("hello  damn  world", ["damn"], "remove", "***")
    assert result == "hello world"  # trailing/leading double-spaces collapsed


# ---------------------------------------------------------------------------
# Defect 2 fix: \g<N> numeric angle-bracket refs are accepted and validated
# correctly against group COUNT, not groupindex.
# ---------------------------------------------------------------------------


def test_regex_numeric_angle_bracket_ref_single_group_accepted():
    r"""\\g<1> with one capture group must be accepted at construction and apply."""
    rule = ReplacementRule(match=r"(\w+)", replace=r"\g<1>", regex=True)
    # \g<1> substitutes the first capture group — identity transformation
    assert apply_replacements("hello", [rule]) == "hello"


def test_regex_numeric_angle_bracket_ref_with_transform():
    r"""\\g<1> can be embedded in a larger replacement string."""
    rule = ReplacementRule(match=r"(\w+)", replace=r"[\g<1>]", regex=True)
    assert apply_replacements("hi", [rule]) == "[hi]"


def test_regex_numeric_angle_bracket_ref_out_of_range_raises():
    r"""\\g<5> with only 1 capture group must raise ValueError at construction."""
    with pytest.raises(ValueError, match="[Ii]nvalid replacement"):
        ReplacementRule(match=r"(\w+)", replace=r"\g<5>", regex=True)


def test_regex_numeric_angle_bracket_ref_zero_accepted():
    r"""\\g<0> refers to the whole match — must be accepted (0 <= groups)."""
    rule = ReplacementRule(match=r"\w+", replace=r"\g<0>", regex=True)
    assert apply_replacements("test", [rule]) == "test"


def test_regex_named_group_ref_still_rejected_after_numeric_fix():
    r"""Named \\g<name> for a non-existent group must still raise ValueError."""
    with pytest.raises(ValueError, match="[Ii]nvalid replacement"):
        ReplacementRule(match=r"(\w+)", replace=r"\g<nogroup>", regex=True)
