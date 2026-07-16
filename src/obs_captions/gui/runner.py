"""Start/stop the ``obs-captions run`` subprocess and stream its log lines.

The GUI never re-implements the caption pipeline: it launches the same CLI
entry point (dev: ``python -m obs_captions``; frozen: the running exe) as a
child process and relays its stdout/stderr to the caller line-by-line.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable

_STARTUPINFO_FLAG = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class CaptionRunner:
    """Manage the lifecycle of one ``obs-captions run`` child process."""

    def __init__(self) -> None:
        self._argv_override: list[str] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._thread: threading.Thread | None = None

    def build_argv(self, sink: str) -> list[str]:
        """Return the argv to launch the caption pipeline for ``sink``.

        Frozen (PyInstaller): re-invoke the running exe (``sys.argv[0]``).
        Dev/installed: ``sys.executable -m obs_captions``.
        """
        if getattr(sys, "frozen", False):
            base = [sys.argv[0]]
        else:
            base = [sys.executable, "-m", "obs_captions"]
        return [*base, "run", "--sink", sink]

    def start(
        self,
        sink: str,
        on_line: Callable[[str], None],
        on_exit: Callable[[int], None] | None = None,
    ) -> None:
        """Launch the subprocess and stream its combined stdout/stderr to ``on_line``.

        Guards against orphaning a live child: if a pipeline is already running,
        raise instead of overwriting ``self._process`` (which would leak the old
        handle). ``on_exit`` — when given — is invoked from the background pump
        thread with the child's return code once its stdout is drained.
        """
        if self.is_running():
            raise RuntimeError("caption pipeline already running")
        argv = self._argv_override if self._argv_override is not None else self.build_argv(sink)
        self._process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=_STARTUPINFO_FLAG if sys.platform == "win32" else 0,
        )
        self._thread = threading.Thread(target=self._pump, args=(on_line, on_exit), daemon=True)
        self._thread.start()

    def _pump(
        self,
        on_line: Callable[[str], None],
        on_exit: Callable[[int], None] | None = None,
    ) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            on_line(line.rstrip("\n"))
        process.wait()
        if on_exit is not None:
            on_exit(process.returncode)

    def stop(self) -> None:
        """Terminate the running subprocess, if any."""
        process = self._process
        if process is None or process.poll() is not None:
            return
        if sys.platform == "win32" and _STARTUPINFO_FLAG:
            process.send_signal(subprocess.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def is_running(self) -> bool:
        """Return whether the subprocess is currently alive."""
        return self._process is not None and self._process.poll() is None


__all__ = ["CaptionRunner"]
