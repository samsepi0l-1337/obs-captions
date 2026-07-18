"""Main Tkinter window: schema-driven settings tabs + Start/Stop/Save controls.

:func:`build_app` constructs the window without entering the Tk mainloop, so
it is directly testable. :func:`main` is the real entry point used by the
CLI's no-args dispatch (see ``obs_captions.cli``).
"""

from __future__ import annotations

import queue
import subprocess
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from obs_captions.gui import config_io, sections
from obs_captions.gui import controls as _controls
# Own name (tests monkeypatch app_mod._detect_recommendation directly).
from obs_captions.gui.controls import detect_recommendation as _detect_recommendation
from obs_captions.gui.runner import CaptionRunner
from obs_captions.gui.widgets import ChoiceBox
from obs_captions.stt import validate

if TYPE_CHECKING:
    from obs_captions.stt.hardware import HardwareInfo

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
    recommend_label: ttk.Label | None = None
    apply_recommend_button: ttk.Button | None = None
    engine_widget: ChoiceBox | None = None
    test_key_button: ttk.Button | None = None
    key_status_label: ttk.Label | None = None
    open_folder_button: ttk.Button | None = None
    collectors: dict[str, Any] = field(default_factory=dict)


def _collect_all(collectors: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for collect in collectors.values():
        merged.update(collect())
    return merged


def _run_in_background(fn: Any) -> None:
    """Run ``fn`` on a daemon thread (indirection so tests can run it inline)."""
    threading.Thread(target=fn, daemon=True).start()


def _wire_model_recommendation(
    root: tk.Misc, registry: dict[str, Any]
) -> tuple[ttk.Label | None, ttk.Button | None]:
    """Add a recommendation label + "추천값 적용" button beside the local model box.

    Detection runs on a background thread that only pushes its result onto a
    queue; a ``root.after`` poller drains it and updates the widgets on the Tk thread.
    """
    entry = registry.get("field_widgets", {}).get("local.model_size")
    if entry is None:
        return None, None
    model_widget = entry[2]
    parent = model_widget.widget.master
    rec_row = registry.get("recommend_row", 100)

    rec_label = ttk.Label(
        parent, text="추천 모델 계산 중...", foreground="gray", font=("TkDefaultFont", 8)
    )
    rec_label.grid(row=rec_row, column=0, columnspan=2, sticky="w", padx=4)
    pending: dict[str, str | None] = {"model": None}

    def _apply() -> None:
        if pending["model"] is not None:
            model_widget.set(pending["model"])

    apply_button = ttk.Button(parent, text="추천값 적용", command=_apply, state="disabled")
    apply_button.grid(row=rec_row + 1, column=0, sticky="w", padx=4, pady=2)

    result_q: queue.Queue[tuple[str, HardwareInfo] | None] = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            result_q.put(_detect_recommendation())
        except Exception:  # noqa: BLE001 - detection must never crash the GUI
            result_q.put(None)

    def _poll(remaining: int = 100) -> None:
        try:
            result = result_q.get_nowait()
        except queue.Empty:
            if remaining > 0:
                root.after(100, lambda: _poll(remaining - 1))
            return
        if result is None:
            rec_label.config(text="추천을 계산할 수 없습니다.")
            return
        model, info = result
        pending["model"] = model
        rec_label.config(text=_controls.format_recommendation(model, info))
        apply_button.config(state="normal")

    threading.Thread(target=_worker, daemon=True).start()
    root.after(100, _poll)
    return rec_label, apply_button


def _wire_key_test(
    controls: ttk.Frame, root: tk.Misc, registry: dict[str, Any]
) -> tuple[ttk.Button, ttk.Label]:
    """Add a "키 테스트" button that validates the selected engine's API key.

    Validation runs off-thread and pushes its result onto a shared queue; a
    ``root.after`` poller applies it. A probe can outlive the bounded ~10s
    poll, so each click is tagged with a ``generation`` (queue: ``(gen,
    result)``); the poller discards a stale-generation result instead of
    showing it, and the queue is drained at the start of every click too.
    """
    status_label = ttk.Label(controls, text="", foreground="gray")
    result_q: queue.Queue[tuple[int, validate.ValidationResult]] = queue.Queue(maxsize=1)
    generation = {"current": 0}

    def _poll(expected_generation: int, remaining: int = 100) -> None:
        try:
            result_generation, result = result_q.get_nowait()
        except queue.Empty:
            if remaining > 0:
                root.after(100, lambda: _poll(expected_generation, remaining - 1))
            else:  # bounded retries: never leave the button permanently disabled
                status_label.config(text="검증 시간이 초과되었습니다.", foreground="red")
                test_button.config(state="normal")
            return
        if result_generation != expected_generation:
            # Superseded click's late result — discard, keep waiting for ours.
            root.after(100, lambda: _poll(expected_generation, remaining))
            return
        status_label.config(text=result.message, foreground=_controls.result_color(result))
        if result.ok:
            messagebox.showinfo("키 검증", result.message)
        else:
            messagebox.showwarning("키 검증", result.message)
        test_button.config(state="normal")

    def _on_test() -> None:
        engine = registry["engine_widget"].get() if registry.get("engine_widget") else ""
        key_widget = _controls.current_key_widget(registry, engine)
        if key_widget is None:
            status_label.config(text="이 엔진은 API 키가 필요 없습니다.", foreground="gray")
            messagebox.showinfo("키 검증", "이 엔진은 API 키가 필요 없습니다.")
            return
        api_key = key_widget.get()
        generation["current"] += 1
        my_generation = generation["current"]
        _controls.drain_queue(result_q)  # drop any stale unconsumed prior result
        test_button.config(state="disabled")
        status_label.config(text="검증 중...", foreground="gray")

        def _work() -> None:
            try:
                result = validate.validate_engine(engine, api_key)
            except Exception:  # noqa: BLE001 - a probe crash must not wedge the button
                result = validate.ValidationResult(False, "network", "검증 중 오류가 발생했습니다.")
            result_q.put((my_generation, result))

        _run_in_background(_work)
        root.after(100, lambda: _poll(my_generation))

    test_button = ttk.Button(controls, text="키 테스트", command=_on_test)
    test_button.pack(side="left", padx=4)
    status_label.pack(side="left")
    return test_button, status_label


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

    # Two rows (run controls on top, advanced helpers below) so 640px never clips.
    controls = ttk.Frame(root)
    controls.pack(fill="x", padx=4, pady=(4, 0))
    controls2 = ttk.Frame(root)
    controls2.pack(fill="x", padx=4, pady=(0, 4))

    show_advanced_var = tk.BooleanVar(value=False)

    def _on_toggle_advanced() -> None:
        apply_visibility = registry.get("apply_visibility")
        if apply_visibility is not None:
            apply_visibility(show_advanced=show_advanced_var.get())

    advanced_check = ttk.Checkbutton(
        controls2, text="고급 설정 표시", variable=show_advanced_var, command=_on_toggle_advanced
    )
    advanced_check.pack(side="left")

    recommend_label, apply_recommend_button = _wire_model_recommendation(root, registry)
    test_key_button, key_status_label = _wire_key_test(controls2, root, registry)

    def _on_open_folder() -> None:
        command = _controls.open_folder_command(str(_controls.config_folder(config_path)))
        try:
            subprocess.Popen(command)
        except OSError as exc:
            messagebox.showerror("폴더 열기 실패", str(exc))

    open_folder_button = ttk.Button(controls2, text="설정 폴더 열기", command=_on_open_folder)
    open_folder_button.pack(side="left", padx=4)

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

    def _on_close() -> None:
        if active_runner.is_running():  # never orphan a live caption child
            active_runner.stop()
        root.destroy()

    if hasattr(root, "protocol"):
        root.protocol("WM_DELETE_WINDOW", _on_close)

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
        recommend_label=recommend_label,
        apply_recommend_button=apply_recommend_button,
        engine_widget=registry.get("engine_widget"),
        test_key_button=test_key_button,
        key_status_label=key_status_label,
        open_folder_button=open_folder_button,
        collectors=collectors,
    )


def main(config_path: str | None = None) -> None:
    """Launch the desktop GUI and block on the Tk mainloop."""
    root = tk.Tk()
    cfg = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    build_app(root, config_path=cfg, env_path=DEFAULT_ENV_PATH)
    root.mainloop()


__all__ = ["AppWindow", "build_app", "main"]
