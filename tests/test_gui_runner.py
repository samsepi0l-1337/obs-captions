from __future__ import annotations

from obs_captions.gui.runner import CaptionRunner


def test_build_argv_dev(monkeypatch):
    monkeypatch.setattr("sys.frozen", False, raising=False)
    r = CaptionRunner()
    argv = r.build_argv("both")
    assert argv[-3:] == ["run", "--sink", "both"]
    assert "obs_captions" in argv


def test_build_argv_frozen(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["obs-captions.exe"], raising=False)
    r = CaptionRunner()
    argv = r.build_argv("obs")
    assert argv == ["obs-captions.exe", "run", "--sink", "obs"]


def test_lifecycle_with_fake_process(monkeypatch):
    import sys

    r = CaptionRunner()
    lines: list[str] = []
    # start a trivial process that prints and exits
    r._argv_override = [sys.executable, "-c", "print('hello')"]
    r.start("browser", lines.append)
    r._thread.join(timeout=5)
    assert any("hello" in line for line in lines)
    assert not r.is_running()


def test_is_running_false_before_start():
    r = CaptionRunner()
    assert r.is_running() is False


def test_stop_when_not_running_is_a_noop():
    r = CaptionRunner()
    r.stop()  # must not raise
    assert not r.is_running()


def test_stop_terminates_running_process():
    import sys

    r = CaptionRunner()
    lines: list[str] = []
    r._argv_override = [sys.executable, "-c", "import time; time.sleep(30)"]
    r.start("browser", lines.append)
    assert r.is_running()
    r.stop()
    r._thread.join(timeout=5)
    assert not r.is_running()
