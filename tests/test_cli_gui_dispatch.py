from __future__ import annotations

import sys

import pytest


def test_no_args_launches_gui(monkeypatch):
    import obs_captions.cli as cli_mod

    called: dict = {}

    def fake_gui_main(config_path=None):
        called["invoked"] = True

    # Patch the gui.app module so cli.main()'s lazy import resolves to the fake.
    import obs_captions.gui.app as gui_app_mod

    monkeypatch.setattr(gui_app_mod, "main", fake_gui_main)
    monkeypatch.setattr(sys, "argv", ["obs-captions"])

    cli_mod.main()

    assert called.get("invoked") is True


def test_args_present_runs_cli_not_gui(monkeypatch):
    import obs_captions.cli as cli_mod

    called: dict = {}

    def fake_gui_main(config_path=None):
        called["invoked"] = True

    import obs_captions.gui.app as gui_app_mod

    monkeypatch.setattr(gui_app_mod, "main", fake_gui_main)
    monkeypatch.setattr(sys, "argv", ["obs-captions", "config"])

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    # click's group exits 0 on a successful subcommand invocation.
    assert exc_info.value.code == 0
    assert "invoked" not in called


def test_help_args_run_cli_not_gui(monkeypatch, capsys):
    import obs_captions.cli as cli_mod

    called: dict = {}

    def fake_gui_main(config_path=None):
        called["invoked"] = True

    import obs_captions.gui.app as gui_app_mod

    monkeypatch.setattr(gui_app_mod, "main", fake_gui_main)
    monkeypatch.setattr(sys, "argv", ["obs-captions", "--help"])

    with pytest.raises(SystemExit):
        cli_mod.main()

    assert "invoked" not in called
