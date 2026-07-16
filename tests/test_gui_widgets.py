from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")


def _root():
    try:
        return tk.Tk()
    except tk.TclError:
        pytest.skip("no display")


def test_replacement_editor_collects_rows():
    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(root)
        editor.set([{"match": "a", "replace": "b"}, {"match": "c", "replace": "d"}])
        assert editor.get() == [
            {"match": "a", "replace": "b"},
            {"match": "c", "replace": "d"},
        ]
    finally:
        root.destroy()


def test_replacement_editor_skips_empty_match_rows():
    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(root)
        editor.set([{"match": "", "replace": "b"}, {"match": "c", "replace": "d"}])
        assert editor.get() == [{"match": "c", "replace": "d"}]
    finally:
        root.destroy()


def test_replacement_editor_set_get_roundtrip():
    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(root)
        editor.set([{"match": "x", "replace": "y"}])
        assert editor.get() == [{"match": "x", "replace": "y"}]
    finally:
        root.destroy()


def test_replacement_editor_initial_populates_rows():
    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(root, [{"match": "m", "replace": "n"}])
        assert editor.get() == [{"match": "m", "replace": "n"}]
    finally:
        root.destroy()


def test_replacement_editor_set_clears_previous_rows():
    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(root, [{"match": "old", "replace": "1"}])
        editor.set([{"match": "new", "replace": "2"}])
        assert editor.get() == [{"match": "new", "replace": "2"}]
    finally:
        root.destroy()


def test_replacement_editor_preserves_extra_rule_fields():
    """regex/ignore_case/whole_word must survive a set->get round-trip."""
    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(
            root,
            [{"match": "a", "replace": "b", "regex": True,
              "ignore_case": False, "whole_word": True}],
        )
        got = editor.get()
        assert got == [{
            "match": "a", "replace": "b", "regex": True,
            "ignore_case": False, "whole_word": True,
        }]
    finally:
        root.destroy()


def test_replacement_editor_edits_match_but_keeps_flags():
    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(
            root, [{"match": "a", "replace": "b", "regex": True}]
        )
        # Simulate the user editing the match text of the first row.
        editor._rows[0][0].set("z")
        got = editor.get()
        assert got == [{"match": "z", "replace": "b", "regex": True}]
    finally:
        root.destroy()


def test_replacement_editor_has_frame_widget():
    from tkinter import ttk

    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(root)
        assert isinstance(editor.widget, ttk.Frame)
    finally:
        root.destroy()
