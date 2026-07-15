"""Build schema-driven Notebook tabs from :mod:`obs_captions.settings_schema`.

One tab is created per distinct :class:`~obs_captions.settings_schema.FieldSpec`
``section`` (for fields whose ``applies_to`` includes ``"gui"``). Each tab's
``collect()`` closure reads its widgets back into a flat ``{key: value}`` dict
in the same shape :mod:`obs_captions.gui.config_io` expects.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from obs_captions.settings_schema import FIELDS, FieldSpec
from obs_captions.gui.widgets import BoolCheck, ChoiceBox, LabeledEntry, SecretEntry

_CONVERTERS: dict[str, Callable[[str], Any]] = {
    "int": int,
    "float": float,
}


def _field_key(field: FieldSpec) -> str:
    return f"env:{field.env_var}" if field.widget == "secret" and field.env_var else field.key


def _make_widget(parent: ttk.Frame, field: FieldSpec, initial: Any):
    if field.widget == "choice":
        return ChoiceBox(parent, field.choices, initial)
    if field.widget == "bool":
        return BoolCheck(parent, initial)
    if field.widget == "secret":
        return SecretEntry(parent, initial)
    if field.widget == "list":
        return LabeledEntry(parent, json.dumps(initial if initial not in (None, "") else []))
    return LabeledEntry(parent, initial)


def _widget_value(field: FieldSpec, widget: Any) -> Any:
    if field.widget == "list":
        raw = widget.get()
        try:
            return json.loads(raw) if raw else []
        except (json.JSONDecodeError, ValueError):
            return []
    convert = _CONVERTERS.get(field.widget)
    raw_value = widget.get()
    if convert is None:
        return raw_value
    if raw_value in ("", None):
        return raw_value
    try:
        return convert(raw_value)
    except (TypeError, ValueError):
        return raw_value


def _wire_engine_visibility(
    notebook: ttk.Notebook, engine_widget: ChoiceBox, local_frame: ttk.Frame
) -> None:
    def on_change(new_value: str) -> None:
        notebook.tab(local_frame, state="normal" if new_value == "local" else "hidden")

    engine_widget.trace(on_change)
    on_change(engine_widget.get())


def build_sections(
    notebook: ttk.Notebook, values: dict[str, Any]
) -> dict[str, Callable[[], dict[str, Any]]]:
    """Create one Notebook tab per schema section and return its collectors."""
    gui_fields = [f for f in FIELDS if "gui" in f.applies_to]

    order: list[str] = []
    by_section: dict[str, list[FieldSpec]] = {}
    for field in gui_fields:
        if field.section not in by_section:
            order.append(field.section)
            by_section[field.section] = []
        by_section[field.section].append(field)

    collectors: dict[str, Callable[[], dict[str, Any]]] = {}
    frames: dict[str, ttk.Frame] = {}
    engine_widget: ChoiceBox | None = None

    for section in order:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=section)
        frames[section] = frame

        section_widgets: list[tuple[FieldSpec, Any]] = []
        for row, field in enumerate(by_section[section]):
            key = _field_key(field)
            ttk.Label(frame, text=field.label).grid(row=row, column=0, sticky="w")
            widget = _make_widget(frame, field, values.get(key))
            widget.widget.grid(row=row, column=1, sticky="ew")
            section_widgets.append((field, widget))
            if field.key == "engine":
                engine_widget = widget

        def collect(widgets: list[tuple[FieldSpec, Any]] = section_widgets) -> dict[str, Any]:
            return {_field_key(field): _widget_value(field, widget) for field, widget in widgets}

        collectors[section] = collect

    if engine_widget is not None and "Local" in frames:
        _wire_engine_visibility(notebook, engine_widget, frames["Local"])

    return collectors


__all__ = ["build_sections"]
