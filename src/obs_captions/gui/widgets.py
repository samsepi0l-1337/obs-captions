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


__all__ = ["LabeledEntry", "SecretEntry", "PathEntry", "ChoiceBox", "BoolCheck"]
