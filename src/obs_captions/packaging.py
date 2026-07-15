from __future__ import annotations

import sys
from pathlib import Path

# Subdir (under the package web/ root) that holds the static overlay assets
# (overlay.{html,css,js}). The server mounts this dir; the .spec ships the whole
# web/ tree, so this stays valid in every mode below.
_OVERLAY_SUBDIR = "overlay"


def resolve_web_dir() -> Path:
    """Locate the bundled ``web/`` asset dir across dev, pip-install, and frozen runs.

    Three modes:

    * **frozen** (PyInstaller): the .spec copies ``src/obs_captions/web`` to the
      bundle as ``obs_captions/web``, so it lands at
      ``sys._MEIPASS / "obs_captions" / "web"``. This dest string MUST stay in
      sync with the ``datas`` entry in ``obs_captions.spec``.
    * **installed / dev**: the assets ship inside the package, so they sit next
      to this file at ``Path(__file__).parent / "web"``. Works regardless of the
      current working directory (the old CWD-relative ``Path("web")`` did not).

    The returned path is not guaranteed to exist at call time; callers decide
    whether a missing dir is fatal (the server simply skips mounting it).
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "obs_captions" / "web"
    return Path(__file__).parent / "web"


def resolve_overlay_dir() -> Path:
    """Return the static overlay asset dir (``<web>/overlay``) the server mounts."""
    return resolve_web_dir() / _OVERLAY_SUBDIR


def attach_parent_console() -> None:
    """Reattach stdout/stderr to the launching console (Windows windowed builds only).

    The GUI build sets ``console=False`` in ``obs_captions.spec``, so a frozen
    windowed exe has no console at all and any CLI output (``run``,
    ``list-devices``, ``check-engine``, ...) invoked from an existing
    cmd.exe/PowerShell would otherwise go nowhere. ``AttachConsole(-1)``
    attaches to that parent console when one exists; stdout/stderr are then
    reopened against it. No-op on non-Windows platforms, and silently gives up
    if no parent console is available (e.g. launched by double-click).
    """
    if sys.platform != "win32":
        return

    import ctypes

    attach_parent_process = -1
    if not ctypes.windll.kernel32.AttachConsole(attach_parent_process):
        return

    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
    except OSError:
        pass
