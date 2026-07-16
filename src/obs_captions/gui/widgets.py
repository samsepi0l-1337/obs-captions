"""Small Tk widget wrappers with a uniform ``get()``/``set(v)`` interface.

Each wrapper owns a ``ttk`` widget and a backing Tk variable, so
:mod:`obs_captions.gui.sections` can build/collect a form generically from
:mod:`obs_captions.settings_schema` without widget-specific branching at the
call site.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, ttk
from typing import Any


class LabeledEntry:
    """A plain text entry backed by a ``tk.StringVar``."""

    def __init__(self, parent: tk.Widget, initial: Any = "") -> None:
        self.var = tk.StringVar(value="" if initial is None else str(initial))
        self.widget = ttk.Entry(parent, textvariable=self.var)

    def get(self) -> str:
        return self.var.get()

    def set(self, value: Any) -> None:
        self.var.set("" if value is None else str(value))


class SecretEntry(LabeledEntry):
    """A masked text entry for API keys/passwords."""

    def __init__(self, parent: tk.Widget, initial: Any = "") -> None:
        super().__init__(parent, initial)
        self.widget = ttk.Entry(parent, textvariable=self.var, show="*")


class PathEntry(LabeledEntry):
    """A text entry paired with a "찾아보기" button that opens a file dialog.

    ``dialog`` is injectable so headless tests can drive the browse flow without
    a real Tk file dialog.
    """

    def __init__(
        self,
        parent: tk.Widget,
        initial: Any = "",
        *,
        dialog: Callable[[], str] | None = None,
    ) -> None:
        self.var = tk.StringVar(value="" if initial is None else str(initial))
        self.widget = ttk.Frame(parent)
        self.entry = ttk.Entry(self.widget, textvariable=self.var)
        self.entry.pack(side="left", fill="x", expand=True)
        self._dialog = dialog or filedialog.askopenfilename
        self.button = ttk.Button(self.widget, text="찾아보기", command=self._browse)
        self.button.pack(side="left")

    def _browse(self) -> None:
        chosen = self._dialog()
        if chosen:
            self.var.set(chosen)


class ChoiceBox:
    """A dropdown restricted to a fixed set of ``choices``."""

    def __init__(self, parent: tk.Widget, choices: tuple[str, ...], initial: Any = "") -> None:
        self.var = tk.StringVar(value="" if initial is None else str(initial))
        self.widget = ttk.Combobox(
            parent, textvariable=self.var, values=list(choices), state="readonly"
        )

    def get(self) -> str:
        return self.var.get()

    def set(self, value: Any) -> None:
        self.var.set("" if value is None else str(value))

    def trace(self, callback) -> None:
        """Invoke ``callback(new_value)`` whenever the selection changes."""
        self.var.trace_add("write", lambda *_args: callback(self.var.get()))


class BoolCheck:
    """A checkbutton backed by a ``tk.BooleanVar``."""

    def __init__(self, parent: tk.Widget, initial: Any = False) -> None:
        self.var = tk.BooleanVar(value=bool(initial))
        self.widget = ttk.Checkbutton(parent, variable=self.var)

    def get(self) -> bool:
        return self.var.get()

    def set(self, value: Any) -> None:
        self.var.set(bool(value))


class ReplacementListEditor:
    """A row editor for post-processing text replacements.

    Each row is a "들리는 말" entry paired with a "교정" entry and a delete
    button; a "행 추가" button appends a blank row. :meth:`get` returns a list
    of rule dicts, skipping rows whose ``match`` is blank. Only ``match`` and
    ``replace`` are edited here; any other keys present on the loaded rule
    (``regex``/``ignore_case``/``whole_word``) are preserved verbatim so a
    round-trip never silently drops them.
    """

    def __init__(self, parent: tk.Widget, initial: list[dict] | None = None) -> None:
        self.widget = ttk.Frame(parent)
        self._rows_frame = ttk.Frame(self.widget)
        self._rows_frame.grid(row=0, column=0, sticky="ew")
        self._rows: list[tuple[tk.StringVar, tk.StringVar, ttk.Frame, dict]] = []
        add_button = ttk.Button(self.widget, text="행 추가", command=self._add_row)
        add_button.grid(row=1, column=0, sticky="w")
        self.set(initial or [])

    def _add_row(self, match: str = "", replace: str = "", extra: dict | None = None) -> None:
        row = ttk.Frame(self._rows_frame)
        match_var = tk.StringVar(value=str(match))
        replace_var = tk.StringVar(value=str(replace))
        ttk.Entry(row, textvariable=match_var).pack(side="left", fill="x", expand=True)
        ttk.Entry(row, textvariable=replace_var).pack(side="left", fill="x", expand=True)
        entry = (match_var, replace_var, row, dict(extra or {}))
        ttk.Button(row, text="삭제", command=lambda: self._remove_row(entry)).pack(side="left")
        row.pack(fill="x")
        self._rows.append(entry)

    def _remove_row(self, entry: tuple[tk.StringVar, tk.StringVar, ttk.Frame, dict]) -> None:
        entry[2].destroy()
        self._rows.remove(entry)

    def get(self) -> list[dict]:
        rules: list[dict] = []
        for match_var, replace_var, _row, extra in self._rows:
            match = match_var.get()
            if not match:
                continue
            rules.append({**extra, "match": match, "replace": replace_var.get()})
        return rules

    def set(self, value: list[dict]) -> None:
        for _match_var, _replace_var, row, _extra in self._rows:
            row.destroy()
        self._rows.clear()
        for item in value or []:
            extra = {k: v for k, v in item.items() if k not in ("match", "replace")}
            self._add_row(item.get("match", ""), item.get("replace", ""), extra)


__all__ = [
    "LabeledEntry",
    "SecretEntry",
    "PathEntry",
    "ChoiceBox",
    "BoolCheck",
    "ReplacementListEditor",
]
