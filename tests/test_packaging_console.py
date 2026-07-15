from __future__ import annotations

import sys

from obs_captions.packaging import attach_parent_console


def test_attach_parent_console_is_callable():
    assert callable(attach_parent_console)


def test_attach_parent_console_noop_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    # Must return without raising.
    assert attach_parent_console() is None


def test_attach_parent_console_noop_on_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert attach_parent_console() is None


def test_attach_parent_console_calls_windows_api(monkeypatch, tmp_path):
    import types

    calls: dict = {}

    fake_kernel32 = types.SimpleNamespace(
        AttachConsole=lambda pid: calls.setdefault("attach_console_pid", pid) or 1
    )
    fake_windll = types.SimpleNamespace(kernel32=fake_kernel32)
    fake_ctypes = types.SimpleNamespace(windll=fake_windll)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    # "CONOUT$" is only a special device name on real Windows; on this
    # (non-Windows) test host, open() creates a literal file named "CONOUT$"
    # in the cwd instead of raising. chdir into tmp_path so that stray file
    # never lands in the repo, and pre-register sys.stdout/stderr with
    # monkeypatch (same value) so it restores the real streams afterward,
    # since attach_parent_console() reassigns them directly (not via monkeypatch).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)

    attach_parent_console()

    assert calls.get("attach_console_pid") == -1
