"""Tier tagging (simple/advanced) for the settings schema.

Task 1 of SP1: every :class:`FieldSpec` carries a ``tier`` that defaults to
``"simple"``; a curated set of detail/tuning fields is tagged ``"advanced"``.
The GUI and OBS plugin use this to hide advanced fields behind a toggle.
"""

from __future__ import annotations

from obs_captions import settings_fields as sf
from obs_captions.settings_types import FieldSpec


def test_fieldspec_tier_defaults_to_simple():
    spec = FieldSpec("x", "X", "text", "General", frozenset({"gui"}))
    assert spec.tier == "simple"


def test_every_field_tier_is_one_of_two_values():
    for f in sf.FIELDS:
        assert f.tier in ("simple", "advanced"), f"bad tier for {f.key}: {f.tier!r}"


def test_representative_advanced_keys_are_advanced():
    by_key = {f.key: f for f in sf.FIELDS}
    for key in (
        "local.vad_threshold",
        "audio.samplerate",
        "server.port",
        "obs.hotkey.enabled",
        "overlay.custom_css",
    ):
        assert by_key[key].tier == "advanced", f"{key} should be advanced"


def test_representative_simple_keys_are_simple():
    by_key = {f.key: f for f in sf.FIELDS}
    for key in ("engine", "language", "local.model_size"):
        assert by_key[key].tier == "simple", f"{key} should be simple"


def test_beginner_essentials_stay_simple():
    by_key = {f.key: f for f in sf.FIELDS}
    for key in (
        "overlay.position",
        "overlay.font_size",
        "overlay.color",
        "obs.source_name",
        "export.enabled",
        "export.format",
        "providers.openai",
        "providers.openai.model",
    ):
        assert by_key[key].tier == "simple", f"{key} should be simple"


def test_all_audio_server_text_hotkey_are_advanced():
    # text.replacements is a deliberate exception: the beginner-facing
    # "잘못 들리는 단어 교정" editor stays simple (see test below).
    for f in sf.FIELDS:
        if f.key == "text.replacements":
            continue
        if (
            f.key.startswith("audio.")
            or f.key.startswith("server.")
            or f.key.startswith("text.")
            or f.key.startswith("obs.hotkey.")
        ):
            assert f.tier == "advanced", f"{f.key} should be advanced"


def test_replacements_editor_is_simple_for_beginners():
    by_key = {f.key: f for f in sf.FIELDS}
    assert by_key["text.replacements"].tier == "simple"


def test_overlay_only_three_are_simple():
    simple_overlay = {
        f.key for f in sf.FIELDS if f.key.startswith("overlay.") and f.tier == "simple"
    }
    assert simple_overlay == {"overlay.position", "overlay.font_size", "overlay.color"}


def test_provider_extra_fields_are_advanced():
    by_key = {f.key: f for f in sf.FIELDS}
    for key in (
        "providers.google.mode",
        "providers.google.location",
        "providers.google.project_id",
        "providers.azure.region",
        "providers.openai.delay",
        "providers.openai.target_language",
    ):
        assert by_key[key].tier == "advanced", f"{key} should be advanced"


def test_helpers_partition_all_keys():
    simple = sf.simple_field_keys()
    advanced = sf.advanced_field_keys()
    assert isinstance(simple, set) and isinstance(advanced, set)
    all_keys = {f.key for f in sf.FIELDS}
    assert simple.isdisjoint(advanced)
    assert simple | advanced == all_keys
    assert "engine" in simple
    assert "audio.samplerate" in advanced
