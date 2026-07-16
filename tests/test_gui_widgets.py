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


def test_replacement_editor_has_frame_widget():
    from tkinter import ttk

    from obs_captions.gui.widgets import ReplacementListEditor

    root = _root()
    try:
        editor = ReplacementListEditor(root)
        assert isinstance(editor.widget, ttk.Frame)
    finally:
        root.destroy()
