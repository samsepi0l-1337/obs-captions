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


def test_second_start_while_running_raises_and_keeps_first_process():
    import sys

    r = CaptionRunner()
    r._argv_override = [sys.executable, "-c", "import time; time.sleep(30)"]
    r.start("browser", lambda _line: None)
    try:
        assert r.is_running()
        first = r._process
        try:
            r.start("browser", lambda _line: None)
        except RuntimeError as exc:
            assert "already running" in str(exc)
        else:  # pragma: no cover - guard must raise
            raise AssertionError("second start() must raise RuntimeError")
        # the original handle must not have been overwritten/leaked
        assert r._process is first
    finally:
        r.stop()
        r._thread.join(timeout=5)
    assert not r.is_running()


def test_start_allowed_again_after_stop():
    import sys

    r = CaptionRunner()
    r._argv_override = [sys.executable, "-c", "import time; time.sleep(30)"]
    r.start("browser", lambda _line: None)
    r.stop()
    r._thread.join(timeout=5)
    assert not r.is_running()
    # restart must succeed (no lingering "already running" state)
    r.start("browser", lambda _line: None)
    assert r.is_running()
    r.stop()
    r._thread.join(timeout=5)


def test_on_exit_called_with_return_code():
    import sys
    import threading

    r = CaptionRunner()
    lines: list[str] = []
    codes: list[int] = []
    done = threading.Event()

    def on_exit(code: int) -> None:
        codes.append(code)
        done.set()

    r._argv_override = [sys.executable, "-c", "import sys; print('hi'); sys.exit(3)"]
    r.start("browser", lines.append, on_exit)
    assert done.wait(timeout=5)
    assert codes == [3]
    assert any("hi" in line for line in lines)
