from __future__ import annotations

import sys
import types

import pytest


def _install_fake_gui(monkeypatch) -> dict:
    """Inject a fake ``obs_captions.gui.app`` into sys.modules.

    This lets ``cli.main()``'s lazy ``from obs_captions.gui.app import main``
    resolve without importing the real module (which imports ``tkinter``, not
    present on headless CI runners), so the dispatch logic is testable
    everywhere.
    """
    called: dict = {}

    def fake_gui_main(config_path=None):
        called["invoked"] = True

    fake_module = types.ModuleType("obs_captions.gui.app")
    fake_module.main = fake_gui_main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "obs_captions.gui.app", fake_module)
    return called


def test_no_args_launches_gui(monkeypatch):
    import obs_captions.cli as cli_mod

    called = _install_fake_gui(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["obs-captions"])

    cli_mod.main()

    assert called.get("invoked") is True


def test_args_present_runs_cli_not_gui(monkeypatch):
    import obs_captions.cli as cli_mod

    called = _install_fake_gui(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["obs-captions", "config"])

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()

    # click's group exits 0 on a successful subcommand invocation.
    assert exc_info.value.code == 0
    assert "invoked" not in called


def test_help_args_run_cli_not_gui(monkeypatch):
    import obs_captions.cli as cli_mod

    called = _install_fake_gui(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["obs-captions", "--help"])

    with pytest.raises(SystemExit):
        cli_mod.main()

    assert "invoked" not in called
