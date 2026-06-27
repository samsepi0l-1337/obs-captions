"""Pure text-transform functions for live captions.

Pipeline:
  apply_replacements() -> apply_filter()   (via transform_text)

Replacements run first so filters can react to substituted terms.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from obs_captions.config import TextConfig

# Sample string used to validate replacement templates at construction time.
# Covers word-chars, digits, spaces, and punctuation so most patterns will match.
_VALIDATE_SAMPLE = "Test text 123 oO!@# "


class ReplacementRule(BaseModel):
    """One text substitution rule (literal or regex)."""

    model_config = ConfigDict(extra="forbid")

    match: str
    replace: str
    regex: bool = False
    ignore_case: bool = True
    whole_word: bool = False

    @model_validator(mode="after")
    def _check_regex_valid(self) -> "ReplacementRule":
        if self.regex:
            # Build the effective runtime pattern before compiling so we validate
            # exactly what will be executed at runtime.  Inline flags (e.g. (?i))
            # are legal at position 0 of the bare pattern but raise re.error at
            # position 5 inside \b(?:...)\b on Python 3.12 — must validate the
            # wrapped form.  Finding 1 fix: compile effective_pattern, not bare
            # self.match.
            flags = re.IGNORECASE if self.ignore_case else 0
            effective_pattern = (
                r"\b(?:" + self.match + r")\b"
            ) if self.whole_word else self.match
            try:
                compiled = re.compile(effective_pattern, flags)
            except re.error as exc:
                raise ValueError(f"Invalid regex pattern {self.match!r}: {exc}") from exc
            # Validate the replacement template at config-load time (fail fast).
            # 1. Check numeric back-references (\1, \2, …) against the compiled
            #    pattern's group count — catches \9 even when the pattern doesn't
            #    match the static sample string.
            n_groups = compiled.groups
            for m in re.finditer(r"\\([1-9]\d*)", self.replace):
                ref = int(m.group(1))
                if ref > n_groups:
                    raise ValueError(
                        f"Invalid replacement template {self.replace!r}: "
                        f"group reference \\{ref} but pattern has {n_groups} group(s)"
                    )
            # 2. Validate \g<...> references against the compiled pattern.
            #    \g<N> where N is all-digits is a NUMERIC backref (0 = whole
            #    match, 1..groups = capture group) — validate against group
            #    count, not groupindex.  Only alphabetic names are validated as
            #    named-group refs via groupindex (Finding 5 fix).
            for ref_m in re.finditer(r"\\g<([^>]+)>", self.replace):
                name = ref_m.group(1)
                if name.isdigit():
                    ref = int(name)
                    if ref > compiled.groups:
                        raise ValueError(
                            f"Invalid replacement template {self.replace!r}: "
                            f"numeric group reference \\g<{ref}> but pattern has "
                            f"{compiled.groups} group(s)"
                        )
                elif name not in compiled.groupindex:
                    raise ValueError(
                        f"Invalid replacement template {self.replace!r}: "
                        f"named group reference \\g<{name}> but pattern has no such group"
                    )
            # 3. Also run a sample substitution to catch any remaining template
            #    errors when the effective pattern happens to match the sample.
            #    re.sub raises re.error for most template errors; IndexError for
            #    unknown named group refs in Python 3.12.
            try:
                re.sub(effective_pattern, self.replace, _VALIDATE_SAMPLE, flags=flags)
            except (re.error, IndexError) as exc:
                raise ValueError(f"Invalid replacement template {self.replace!r}: {exc}") from exc
        return self


def apply_replacements(text: str, rules: list[ReplacementRule]) -> str:
    """Apply each rule to *text* in order; returns *text* unchanged if *rules* is empty."""
    for rule in rules:
        flags = re.IGNORECASE if rule.ignore_case else 0
        if rule.regex:
            # Wrap in a non-capturing group so whole_word boundaries bind to the
            # full alternation (e.g. r"cat|dog" → r"\b(?:cat|dog)\b") instead of
            # only the first/last alternative.
            pattern = (r"\b(?:" + rule.match + r")\b") if rule.whole_word else rule.match
            # Regex mode: replace is a template (group refs supported, validated at construction).
            text = re.sub(pattern, rule.replace, text, flags=flags)
        else:
            escaped = re.escape(rule.match)
            pattern = (r"\b" + escaped + r"\b") if rule.whole_word else escaped
            # Literal mode: treat replace as a plain string — use a lambda to
            # prevent re.sub from interpreting backslash sequences as group refs.
            repl = rule.replace
            text = re.sub(pattern, lambda _: repl, text, flags=flags)
    return text


def apply_filter(
    text: str,
    words: list[str],
    mode: Literal["mask", "remove"],
    mask: str,
) -> str:
    """Mask or remove whole-word occurrences of *words* (case-insensitive).

    mode="mask"   replaces each matched word with *mask*.
    mode="remove" drops matched words and normalises surrounding whitespace, but
                  only when at least one word was actually removed (Finding 6 fix
                  — prevents collapsing pre-existing spaces in non-matching text).
    Empty *words* list (or all-blank entries) returns *text* unchanged.
    Blank strings in *words* are silently ignored (Finding 2 API-level guard —
    prevents r'\\b\\b' zero-width corruption).
    """
    # Strip blank strings before building patterns (Finding 2 fix).
    words = [w for w in words if w.strip()]
    if not words:
        return text
    repl = mask if mode == "mask" else ""
    _removed = False
    for word in words:
        pattern = r"\b" + re.escape(word) + r"\b"
        # Use a lambda so the replacement is always treated as a literal string,
        # preventing backslash sequences in the mask from being interpreted as
        # group references by re.sub.
        new_text = re.sub(pattern, lambda _: repl, text, flags=re.IGNORECASE)
        if mode == "remove" and new_text != text:
            _removed = True
        text = new_text
    if mode == "remove" and _removed:
        # Normalise whitespace classes (spaces, tabs, newlines) only when a word
        # was removed so pre-existing multiple spaces in non-matching captions are
        # left intact (Finding 6 fix).
        text = re.sub(r"\s+", " ", text).strip()
    return text


def transform_text(text: str, text_cfg: TextConfig) -> str:
    """Apply replacements then filter (order matters; see module docstring).

    Returns *text* unchanged when *text_cfg* uses all defaults.
    """
    text = apply_replacements(text, text_cfg.replacements)
    text = apply_filter(text, text_cfg.filter_words, text_cfg.filter_mode, text_cfg.filter_mask)
    return text


def should_suppress(text: str, cfg: TextConfig) -> bool:
    """Return True if *text* should be dropped rather than forwarded to caption state.

    Suppression rules (applied in order; first match wins):
    1. suppress_blank=True (default): blank or whitespace-only text → drop.
    2. suppress_regex: re.fullmatch (case-insensitive) against stripped text → drop.
    3. suppress_exact: case-insensitive whole-string comparison after strip → drop.

    A non-blank, non-matching caption is NEVER suppressed regardless of config.
    """
    stripped = text.strip()
    if cfg.suppress_blank and not stripped:
        return True
    for pattern in cfg._compiled_suppress_regex:
        if pattern.fullmatch(stripped):
            return True
    folded = stripped.casefold()
    for exact in cfg.suppress_exact:
        if folded == exact.strip().casefold():
            return True
    return False


def wrap_text(text: str, max_chars: int) -> list[str]:
    """Split *text* into lines of at most *max_chars* codepoints.

    Uses codepoint count (``len()``), which is correct for Korean Hangul
    (each syllable-block is a single codepoint) without requiring extra
    grapheme-segmentation dependencies.

    ``max_chars <= 0`` disables wrapping and returns ``[text]`` unchanged.

    .. note::
        This function assumes well-formed Unicode input (no explicit lone
        surrogates).  STT backends produce valid UTF-8, so lone surrogates
        are impossible in practice.  Splitting a string that happens to
        contain them at a chunk boundary would yield two invalid surrogate
        strings — an accepted trade-off given the real-world impossibility.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    lines: list[str] = []
    while len(text) > max_chars:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    lines.append(text)
    return lines
