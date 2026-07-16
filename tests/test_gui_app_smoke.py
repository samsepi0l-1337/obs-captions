from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")


def _root():
    try:
        return tk.Tk()
    except tk.TclError:
        pytest.skip("no display")


class _FakeRunner:
    """Controllable stand-in for CaptionRunner (no real subprocess)."""

    def __init__(self, *, running: bool = False, start_error: Exception | None = None):
        self._running = running
        self._start_error = start_error
        self.on_exit = None
        self.calls: list[str] = []

    def start(self, sink, on_line, on_exit=None):
        if self._start_error is not None:
            raise self._start_error
        self.calls.append("start")
        self.sink = sink
        self.on_line = on_line
        self.on_exit = on_exit
        self._running = True

    def stop(self):
        self.calls.append("stop")
        self._running = False

    def is_running(self):
        return self._running


def test_build_app_has_start_stop_buttons():
    from obs_captions.gui.app import build_app

    root = _root()
    try:
        window = build_app(root)
        assert window.start_button is not None
        assert window.stop_button is not None
        assert str(window.start_button["text"]).lower() == "start"
        assert str(window.stop_button["text"]).lower() == "stop"
    finally:
        root.destroy()


def test_advanced_checkbox_present_and_toggles(monkeypatch):
    from obs_captions.gui.app import build_app

    root = _root()
    try:
        window = build_app(root, runner=_FakeRunner())
        assert window.advanced_check is not None
        assert "고급" in str(window.advanced_check["text"])
        # Toggling the advanced checkbox must not raise (re-applies visibility).
        window.advanced_check.invoke()
        window.advanced_check.invoke()
    finally:
        root.destroy()


def test_stop_button_disabled_initially():
    from obs_captions.gui.app import build_app

    root = _root()
    try:
        window = build_app(root, runner=_FakeRunner())
        assert str(window.stop_button["state"]) == "disabled"
    finally:
        root.destroy()


def test_build_app_has_save_button_and_log_widget():
    from obs_captions.gui.app import build_app

    root = _root()
    try:
        window = build_app(root)
        assert window.save_button is not None
        assert str(window.save_button["text"]).lower() == "save"
        assert window.log_widget is not None
        # Log is read-only except while appending.
        assert str(window.log_widget["state"]) == "disabled"
    finally:
        root.destroy()


def test_start_collects_saves_and_runs(monkeypatch, tmp_path):
    from obs_captions.gui import app as app_mod

    saved: dict = {}
    monkeypatch.setattr(
        app_mod.config_io,
        "save_settings",
        lambda values, cfg, env: saved.update({"values": values}),
    )

    runner = _FakeRunner()
    root = _root()
    try:
        window = app_mod.build_app(root, runner=runner)
        window.start_button.invoke()
        assert "values" in saved
        assert runner.sink == "browser"
        assert "start" in runner.calls
    finally:
        root.destroy()


def test_start_toggles_buttons_and_status(monkeypatch):
    from obs_captions.gui import app as app_mod

    monkeypatch.setattr(app_mod.config_io, "save_settings", lambda *a: None)
    runner = _FakeRunner()
    root = _root()
    try:
        window = app_mod.build_app(root, runner=runner)
        window.start_button.invoke()
        assert str(window.start_button["state"]) == "disabled"
        assert str(window.stop_button["state"]) == "normal"
        assert str(window.status_label["text"]) == "running"
    finally:
        root.destroy()


def test_start_noop_when_already_running(monkeypatch):
    from obs_captions.gui import app as app_mod

    monkeypatch.setattr(app_mod.config_io, "save_settings", lambda *a: None)
    runner = _FakeRunner(running=True)
    root = _root()
    try:
        window = app_mod.build_app(root, runner=runner)
        window.start_button.invoke()
        assert "start" not in runner.calls
    finally:
        root.destroy()


def test_child_exit_marshals_status_and_buttons(monkeypatch):
    from obs_captions.gui import app as app_mod

    monkeypatch.setattr(app_mod.config_io, "save_settings", lambda *a: None)
    runner = _FakeRunner()
    root = _root()
    # Run root.after callbacks synchronously (avoids a flaky macOS Tk event loop).
    root.after = lambda _delay, fn=None, *a: fn(*a) if fn else None
    try:
        window = app_mod.build_app(root, runner=runner)
        window.start_button.invoke()
        assert runner.on_exit is not None
        runner.on_exit(3)  # child died on its own
        assert "종료 코드 3" in str(window.status_label["text"])
        assert str(window.start_button["state"]) == "normal"
        assert str(window.stop_button["state"]) == "disabled"
    finally:
        root.destroy()


def test_save_failure_shows_error(monkeypatch):
    from obs_captions.gui import app as app_mod

    def _boom(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(app_mod.config_io, "save_settings", _boom)
    errors: list[tuple] = []
    monkeypatch.setattr(app_mod.messagebox, "showerror", lambda *a, **k: errors.append(a))

    root = _root()
    try:
        window = app_mod.build_app(root, runner=_FakeRunner())
        window.save_button.invoke()
        assert errors
        assert errors[0][0] == "저장 실패"
    finally:
        root.destroy()


def test_start_failure_shows_error_and_rolls_back(monkeypatch):
    from obs_captions.gui import app as app_mod

    monkeypatch.setattr(app_mod.config_io, "save_settings", lambda *a: None)
    errors: list[tuple] = []
    monkeypatch.setattr(app_mod.messagebox, "showerror", lambda *a, **k: errors.append(a))

    runner = _FakeRunner(start_error=FileNotFoundError("no exe"))
    root = _root()
    try:
        window = app_mod.build_app(root, runner=runner)
        window.start_button.invoke()
        assert errors and errors[0][0] == "실행 실패"
        assert str(window.status_label["text"]) == "stopped"
        assert str(window.start_button["state"]) == "normal"
    finally:
        root.destroy()


def test_start_aborts_when_save_fails(monkeypatch):
    from obs_captions.gui import app as app_mod

    def _boom(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(app_mod.config_io, "save_settings", _boom)
    monkeypatch.setattr(app_mod.messagebox, "showerror", lambda *a, **k: None)

    runner = _FakeRunner()
    root = _root()
    try:
        window = app_mod.build_app(root, runner=runner)
        window.start_button.invoke()
        assert "start" not in runner.calls
    finally:
        root.destroy()


def test_stop_button_stops_runner(monkeypatch):
    from obs_captions.gui import app as app_mod

    monkeypatch.setattr(app_mod.config_io, "save_settings", lambda *a: None)
    runner = _FakeRunner()
    root = _root()
    try:
        window = app_mod.build_app(root, runner=runner)
        window.start_button.invoke()  # enables the stop button
        window.stop_button.invoke()
        assert "stop" in runner.calls
        assert str(window.start_button["state"]) == "normal"
        assert str(window.stop_button["state"]) == "disabled"
    finally:
        root.destroy()
