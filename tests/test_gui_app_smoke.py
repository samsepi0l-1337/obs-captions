from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")


def _root():
    try:
        return tk.Tk()
    except tk.TclError:
        pytest.skip("no display")


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


def test_build_app_has_save_button_and_log_widget():
    from obs_captions.gui.app import build_app

    root = _root()
    try:
        window = build_app(root)
        assert window.save_button is not None
        assert str(window.save_button["text"]).lower() == "save"
        assert window.log_widget is not None
    finally:
        root.destroy()


def test_start_collects_saves_and_runs(monkeypatch, tmp_path):
    from obs_captions.gui import app as app_mod

    started: dict = {}

    class _FakeRunner:
        def start(self, sink, on_line):
            started["sink"] = sink
            started["on_line"] = on_line

        def stop(self):
            started["stopped"] = True

        def is_running(self):
            return started.get("stopped") is not True

    saved: dict = {}
    monkeypatch.setattr(
        app_mod.config_io,
        "save_settings",
        lambda values, cfg, env: saved.update({"values": values}),
    )

    root = _root()
    try:
        window = app_mod.build_app(root, runner=_FakeRunner())
        window.start_button.invoke()
        assert "values" in saved
        assert started["sink"] == "browser"
    finally:
        root.destroy()


def test_stop_button_stops_runner(monkeypatch):
    from obs_captions.gui import app as app_mod

    calls: list[str] = []

    class _FakeRunner:
        def start(self, sink, on_line):
            calls.append("start")

        def stop(self):
            calls.append("stop")

        def is_running(self):
            return False

    root = _root()
    try:
        window = app_mod.build_app(root, runner=_FakeRunner())
        window.stop_button.invoke()
        assert "stop" in calls
    finally:
        root.destroy()
