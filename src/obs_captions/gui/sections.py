"""Build schema-driven Notebook tabs from :mod:`obs_captions.settings_schema`.

One tab is created per distinct :class:`~obs_captions.settings_schema.FieldSpec`
``section`` (for fields whose ``applies_to`` includes ``"gui"``). Each tab's
``collect()`` closure reads its widgets back into a flat ``{key: value}`` dict
in the same shape :mod:`obs_captions.gui.config_io` expects.

Tab identity keys stay the English ``section`` value (used by tests and wiring);
only the visible tab text is localized to Korean via :data:`_SECTION_LABELS`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from tkinter import ttk
from typing import Any

from obs_captions.settings_schema import FIELDS, FieldSpec
from obs_captions.gui.widgets import (
    BoolCheck,
    ChoiceBox,
    LabeledEntry,
    PathEntry,
    ReplacementListEditor,
    SecretEntry,
)

_REPLACEMENTS_KEY = "text.replacements"

_CONVERTERS: dict[str, Callable[[str], Any]] = {
    "int": int,
    "float": float,
}

_SECTION_LABELS: dict[str, str] = {
    "General": "일반",
    "Audio": "오디오",
    "Local": "로컬 모델",
    "Output": "출력",
    "Text": "텍스트",
    "Export": "내보내기",
    "OBS": "OBS",
    "API Keys": "API 키",
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
    if field.widget == "path":
        return PathEntry(parent, initial)
    if field.key == _REPLACEMENTS_KEY:
        return ReplacementListEditor(parent, initial if isinstance(initial, list) else None)
    if field.widget == "list":
        return LabeledEntry(parent, json.dumps(initial if initial not in (None, "") else []))
    return LabeledEntry(parent, initial)


def _widget_value(field: FieldSpec, widget: Any) -> Any:
    if field.key == _REPLACEMENTS_KEY:
        return widget.get()
    if field.widget == "list":
        raw = widget.get()
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"'{field.label}' 목록 형식(JSON)이 잘못되었습니다: {exc}") from exc
    convert = _CONVERTERS.get(field.widget)
    raw_value = widget.get()
    if convert is None or raw_value in ("", None):
        return raw_value
    try:
        return convert(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"'{field.label}' 숫자 형식이 잘못되었습니다: {raw_value!r}"
        ) from exc


def _make_visibility_applier(
    notebook: ttk.Notebook,
    engine_widget: ChoiceBox,
    local_frame: ttk.Frame | None,
    visibility_specs: list[tuple[FieldSpec, list[Any]]],
) -> Callable[..., None]:
    """Return an ``apply(engine=, show_advanced=)`` callback and wire it.

    A field's row widgets are shown only when both hold: it is relevant for the
    selected engine (``not field.engines or engine in field.engines``) AND it is
    a beginner-essential ``simple`` field OR advanced fields are being shown.
    The callback keeps the last engine/show_advanced so either can be updated
    independently (engine via the widget trace, show_advanced via the GUI toggle).
    """
    state = {"engine": engine_widget.get(), "show_advanced": False}

    def apply(engine: str | None = None, show_advanced: bool | None = None) -> None:
        if engine is not None:
            state["engine"] = engine
        if show_advanced is not None:
            state["show_advanced"] = show_advanced
        selected = state["engine"]
        advanced = state["show_advanced"]
        if local_frame is not None:
            notebook.tab(local_frame, state="normal" if selected == "local" else "hidden")
        for field, row_widgets in visibility_specs:
            engine_ok = not field.engines or selected in field.engines
            tier_ok = field.tier == "simple" or advanced
            visible = engine_ok and tier_ok
            for row_widget in row_widgets:
                if visible:
                    row_widget.grid()
                else:
                    row_widget.grid_remove()

    engine_widget.trace(lambda new_engine: apply(engine=new_engine))
    apply()
    return apply


def build_sections(
    notebook: ttk.Notebook,
    values: dict[str, Any],
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Callable[[], dict[str, Any]]]:
    """Create one Notebook tab per schema section and return its collectors.

    A field whose ``help`` is non-empty gets a second, smaller gray label below
    it (spanning both columns) carrying that beginner-friendly hint. It is
    grouped with the field's label/widget as one show/hide unit, so engine
    conditional visibility (below) toggles all three together.

    When ``registry`` is provided it is populated with widget handles
    (``"engine_widget"`` and ``"field_widgets"``) so callers/tests can drive and
    inspect the engine-conditional show/hide behaviour without re-deriving them.
    ``field_widgets[key]`` is ``(field, label, widget, help_label_or_None)``.
    """
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
    visibility_specs: list[tuple[FieldSpec, list[Any]]] = []
    field_widgets: dict[str, tuple[FieldSpec, ttk.Label, Any, ttk.Label | None]] = {}

    for section in order:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=_SECTION_LABELS.get(section, section))
        frames[section] = frame

        section_widgets: list[tuple[FieldSpec, Any]] = []
        row = 0
        for field in by_section[section]:
            key = _field_key(field)
            label = ttk.Label(frame, text=field.label)
            label.grid(row=row, column=0, sticky="w")
            widget = _make_widget(frame, field, values.get(key))
            widget.widget.grid(row=row, column=1, sticky="ew")
            row_widgets: list[Any] = [label, widget.widget]

            help_label: ttk.Label | None = None
            if field.help:
                row += 1
                help_label = ttk.Label(
                    frame, text=field.help, foreground="gray", font=("TkDefaultFont", 8)
                )
                help_label.grid(row=row, column=0, columnspan=2, sticky="w")
                row_widgets.append(help_label)
            row += 1

            section_widgets.append((field, widget))
            field_widgets[key] = (field, label, widget, help_label)
            visibility_specs.append((field, row_widgets))
            if field.key == "engine":
                engine_widget = widget

        def collect(widgets: list[tuple[FieldSpec, Any]] = section_widgets) -> dict[str, Any]:
            return {_field_key(field): _widget_value(field, widget) for field, widget in widgets}

        collectors[section] = collect

    apply_visibility: Callable[..., None] | None = None
    if engine_widget is not None:
        apply_visibility = _make_visibility_applier(
            notebook, engine_widget, frames.get("Local"), visibility_specs
        )

    if registry is not None:
        registry["engine_widget"] = engine_widget
        registry["field_widgets"] = field_widgets
        registry["apply_visibility"] = apply_visibility

    return collectors


__all__ = ["build_sections"]
