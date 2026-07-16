"""Main Tkinter window: schema-driven settings tabs + Start/Stop/Save controls.

:func:`build_app` constructs the window without entering the Tk mainloop, so
it is directly testable. :func:`main` is the real entry point used by the
CLI's no-args dispatch (see ``obs_captions.cli``).
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from pydantic import ValidationError

from obs_captions.gui import config_io, sections
from obs_captions.gui.runner import CaptionRunner
from obs_captions.gui.widgets import ChoiceBox

DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_ENV_PATH = Path(".env")

_SINK_CHOICES = ("browser", "obs", "both")


@dataclass
class AppWindow:
    """Handles returned by :func:`build_app`, exposed for tests and wiring."""

    root: tk.Misc
    notebook: ttk.Notebook
    sink_choice: ChoiceBox
    start_button: ttk.Button
    stop_button: ttk.Button
    save_button: ttk.Button
    status_label: ttk.Label
    log_widget: ScrolledText
    advanced_check: ttk.Checkbutton | None = None
    collectors: dict[str, Any] = field(default_factory=dict)


def _collect_all(collectors: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for collect in collectors.values():
        merged.update(collect())
    return merged


def build_app(
    root: tk.Misc,
    *,
    config_path: str | Path | None = DEFAULT_CONFIG_PATH,
    env_path: str | Path | None = DEFAULT_ENV_PATH,
    runner: CaptionRunner | None = None,
) -> AppWindow:
    """Build the main window's widgets and wire Start/Stop/Save. No mainloop."""
    root.title("obs-captions")
    if isinstance(root, tk.Tk):
        root.geometry("640x600")
        root.minsize(640, 600)

    values = config_io.load_settings(config_path, env_path)
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    registry: dict[str, Any] = {}
    collectors = sections.build_sections(notebook, values, registry=registry)

    controls = ttk.Frame(root)
    controls.pack(fill="x")

    show_advanced_var = tk.BooleanVar(value=False)

    def _on_toggle_advanced() -> None:
        apply_visibility = registry.get("apply_visibility")
        if apply_visibility is not None:
            apply_visibility(show_advanced=show_advanced_var.get())

    advanced_check = ttk.Checkbutton(
        controls, text="고급 설정 표시", variable=show_advanced_var, command=_on_toggle_advanced
    )
    advanced_check.pack(side="left")

    ttk.Label(controls, text="Sink").pack(side="left")
    sink_choice = ChoiceBox(controls, _SINK_CHOICES, "browser")
    sink_choice.widget.pack(side="left")

    status_label = ttk.Label(controls, text="stopped")
    status_label.pack(side="left", padx=8)

    log_widget = ScrolledText(root, height=10, state="disabled")
    log_widget.pack(fill="both", expand=True)

    active_runner = runner if runner is not None else CaptionRunner()

    def append_log(line: str) -> None:
        def _do_append() -> None:
            log_widget.config(state="normal")
            log_widget.insert(tk.END, line + "\n")
            log_widget.see(tk.END)
            log_widget.config(state="disabled")

        root.after(0, _do_append)

    def _config_display() -> str:
        return str(Path(config_path).resolve()) if config_path is not None else "메모리"

    def on_save() -> bool:
        try:
            config_io.save_settings(_collect_all(collectors), config_path, env_path)
        except (ValueError, OSError, ValidationError) as exc:
            messagebox.showerror("저장 실패", str(exc))
            return False
        status_label.config(text=f"저장됨: {_config_display()}")
        return True

    def _on_child_exit(returncode: int) -> None:
        def _apply() -> None:
            status_label.config(text=f"stopped (종료 코드 {returncode})")
            start_button.config(state="normal")
            stop_button.config(state="disabled")

        root.after(0, _apply)

    def on_start() -> None:
        if active_runner.is_running():
            return
        if not on_save():
            return
        try:
            active_runner.start(sink_choice.get(), append_log, on_exit=_on_child_exit)
        except OSError as exc:  # e.g. FileNotFoundError from Popen
            messagebox.showerror("실행 실패", str(exc))
            status_label.config(text="stopped")
            return
        status_label.config(text="running")
        start_button.config(state="disabled")
        stop_button.config(state="normal")

    def on_stop() -> None:
        active_runner.stop()
        status_label.config(text="stopped")
        start_button.config(state="normal")
        stop_button.config(state="disabled")

    save_button = ttk.Button(controls, text="Save", command=on_save)
    save_button.pack(side="right")
    stop_button = ttk.Button(controls, text="Stop", command=on_stop, state="disabled")
    stop_button.pack(side="right")
    start_button = ttk.Button(controls, text="Start", command=on_start)
    start_button.pack(side="right")

    return AppWindow(
        root=root,
        notebook=notebook,
        sink_choice=sink_choice,
        start_button=start_button,
        stop_button=stop_button,
        save_button=save_button,
        status_label=status_label,
        log_widget=log_widget,
        advanced_check=advanced_check,
        collectors=collectors,
    )


def main(config_path: str | None = None) -> None:
    """Launch the desktop GUI and block on the Tk mainloop."""
    root = tk.Tk()
    cfg = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    build_app(root, config_path=cfg, env_path=DEFAULT_ENV_PATH)
    root.mainloop()


__all__ = ["AppWindow", "build_app", "main"]
