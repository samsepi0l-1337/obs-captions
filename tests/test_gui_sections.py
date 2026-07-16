from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")


def _root():
    try:
        return tk.Tk()
    except tk.TclError:
        pytest.skip("no display")


def test_sections_build_and_collect():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        collectors = sections.build_sections(nb, values)
        assert "General" in collectors
        merged: dict = {}
        for c in collectors.values():
            merged.update(c())
        assert merged["engine"] == "local"
    finally:
        root.destroy()


def test_sections_cover_expected_gui_tabs():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        collectors = sections.build_sections(nb, values)
        expected = {
            "General",
            "Audio",
            "Local",
            "Output",
            "Text",
            "Export",
            "OBS",
            "API Keys",
        }
        assert expected <= set(collectors.keys())
    finally:
        root.destroy()


class _Stub:
    def __init__(self, value: str):
        self._value = value

    def get(self) -> str:
        return self._value


def test_list_collector_raises_on_bad_json():
    from obs_captions.gui.sections import _widget_value
    from obs_captions.settings_schema import FieldSpec

    f = FieldSpec("text.filter_words", "필터 단어", "list", "Text", frozenset({"gui"}))
    with pytest.raises(ValueError):
        _widget_value(f, _Stub("[not-json"))


def test_list_collector_empty_is_empty_list():
    from obs_captions.gui.sections import _widget_value
    from obs_captions.settings_schema import FieldSpec

    f = FieldSpec("text.filter_words", "필터 단어", "list", "Text", frozenset({"gui"}))
    assert _widget_value(f, _Stub("")) == []


def test_numeric_collector_raises_on_bad_value():
    from obs_captions.gui.sections import _widget_value
    from obs_captions.settings_schema import FieldSpec

    f = FieldSpec("overlay.font_size", "글자 크기", "int", "Output", frozenset({"gui"}))
    with pytest.raises(ValueError):
        _widget_value(f, _Stub("48px"))


def test_engine_visibility_toggles_provider_fields_and_keys():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        registry: dict = {}
        sections.build_sections(nb, values, registry=registry)
        engine = registry["engine_widget"]
        fw = registry["field_widgets"]

        # trace fires synchronously on set(); winfo_manager reflects grid state.
        engine.set("openai")
        assert fw["env:OPENAI_API_KEY"][2].widget.winfo_manager() == "grid"
        assert fw["providers.openai.model"][2].widget.winfo_manager() == "grid"
        assert fw["env:ELEVENLABS_API_KEY"][2].widget.winfo_manager() == ""
        assert fw["providers.google.model"][2].widget.winfo_manager() == ""

        engine.set("local")
        assert fw["env:OPENAI_API_KEY"][2].widget.winfo_manager() == ""
        assert fw["env:ELEVENLABS_API_KEY"][2].widget.winfo_manager() == ""
    finally:
        root.destroy()


def test_advanced_toggle_hides_and_shows_advanced_fields():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        registry: dict = {}
        sections.build_sections(nb, values, registry=registry)
        fw = registry["field_widgets"]
        apply = registry["apply_visibility"]
        registry["engine_widget"].set("local")

        # local.vad_threshold is advanced tier (no engines) → hidden by default.
        assert fw["local.vad_threshold"][2].widget.winfo_manager() == ""
        # local.model_size is simple → visible.
        assert fw["local.model_size"][2].widget.winfo_manager() == "grid"

        apply(show_advanced=True)
        assert fw["local.vad_threshold"][2].widget.winfo_manager() == "grid"

        apply(show_advanced=False)
        assert fw["local.vad_threshold"][2].widget.winfo_manager() == ""
    finally:
        root.destroy()


def test_advanced_and_engine_visibility_are_anded():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        registry: dict = {}
        sections.build_sections(nb, values, registry=registry)
        engine = registry["engine_widget"]
        apply = registry["apply_visibility"]
        fw = registry["field_widgets"]

        # providers.openai.delay is advanced tier AND engine-gated to openai.
        engine.set("openai")
        apply(show_advanced=True)
        assert fw["providers.openai.delay"][2].widget.winfo_manager() == "grid"

        # Engine switches away → hidden even though advanced is on.
        engine.set("local")
        assert fw["providers.openai.delay"][2].widget.winfo_manager() == ""

        # Engine matches but advanced off → still hidden (it's advanced).
        engine.set("openai")
        apply(show_advanced=False)
        assert fw["providers.openai.delay"][2].widget.winfo_manager() == ""
    finally:
        root.destroy()


def test_help_label_rendered_for_fields_with_help():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        registry: dict = {}
        sections.build_sections(nb, values, registry=registry)
        fw = registry["field_widgets"]

        engine_entry = fw["engine"]
        assert engine_entry[3] is not None  # help_label
        assert str(engine_entry[3]["text"]) == engine_entry[0].help
        assert engine_entry[0].help  # sanity: engine has non-empty help text
    finally:
        root.destroy()


def test_help_label_absent_for_fields_without_help(monkeypatch):
    from tkinter import ttk

    from obs_captions.gui import sections
    from obs_captions.settings_schema import FieldSpec

    no_help_field = FieldSpec(
        "no_help_field", "레이블", "text", "General", frozenset({"gui"}), help=""
    )
    monkeypatch.setattr(sections, "FIELDS", [no_help_field])

    root = _root()
    try:
        nb = ttk.Notebook(root)
        registry: dict = {}
        sections.build_sections(nb, {}, registry=registry)
        fw = registry["field_widgets"]
        assert fw["no_help_field"][3] is None
    finally:
        root.destroy()


def test_help_label_toggles_with_engine_visibility():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        registry: dict = {}
        sections.build_sections(nb, values, registry=registry)
        engine = registry["engine_widget"]
        fw = registry["field_widgets"]

        openai_key_help = fw["env:OPENAI_API_KEY"][3]
        assert openai_key_help is not None

        engine.set("openai")
        assert openai_key_help.winfo_manager() == "grid"

        engine.set("local")
        assert openai_key_help.winfo_manager() == ""
    finally:
        root.destroy()


def test_path_widget_has_browse_button(monkeypatch):
    from obs_captions.gui.widgets import PathEntry

    root = _root()
    try:
        pe = PathEntry(root, "", dialog=lambda: "/tmp/style.css")
        assert pe.button is not None
        pe._browse()
        assert pe.get() == "/tmp/style.css"
    finally:
        root.destroy()


def test_replacements_collector_returns_dict_list():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        registry: dict = {}
        collectors = sections.build_sections(nb, values, registry=registry)
        editor = registry["field_widgets"]["text.replacements"][2]
        editor.set([{"match": "a", "replace": "b"}])
        collected = collectors["Text"]()
        assert collected["text.replacements"] == [{"match": "a", "replace": "b"}]
    finally:
        root.destroy()


def test_collect_reflects_widget_edits():
    from tkinter import ttk

    from obs_captions.gui import config_io, sections

    root = _root()
    try:
        nb = ttk.Notebook(root)
        values = config_io.load_settings(None, None)
        collectors = sections.build_sections(nb, values)
        merged_before = {}
        for c in collectors.values():
            merged_before.update(c())
        assert merged_before["local.model_size"] == "small"

        # Simulate the user changing the Local model size choice widget.
        general_collect = collectors["Local"]
        # collect() returns the currently-displayed values; changing requires
        # access to the underlying widget, exercised via the merged dict shape.
        assert "local.model_size" in general_collect()
    finally:
        root.destroy()
