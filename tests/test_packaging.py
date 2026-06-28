from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import obs_captions.packaging as packaging
from obs_captions.packaging import resolve_overlay_dir, resolve_web_dir


def test_resolve_web_dir_dev_points_at_real_package_web_dir():
    """Dev/installed mode: resolves to the package's bundled web/ dir (next to packaging.py).

    Uses the *literal* parent (no .resolve()) so symlinked-editable installs stay
    inside the install tree rather than following the symlink out to the real src.
    """
    web_dir = resolve_web_dir()
    # Match the literal used in packaging.py — no .resolve() — to catch any future
    # regression that re-introduces it.
    expected = Path(packaging.__file__).parent / "web"

    assert web_dir == expected
    # The move put the real assets here, so this must actually exist on disk.
    assert web_dir.is_dir(), f"package web dir missing: {web_dir}"


def test_resolve_overlay_dir_is_web_dir_slash_overlay():
    """The overlay assets live at <web>/overlay; callers consume this exact subpath."""
    assert resolve_overlay_dir() == resolve_web_dir() / "overlay"


def test_resolve_overlay_dir_dev_contains_overlay_assets():
    """After the git mv, overlay.html/css/js ship inside the package."""
    overlay_dir = resolve_overlay_dir()
    assert (overlay_dir / "overlay.html").is_file()
    assert (overlay_dir / "overlay.css").is_file()
    assert (overlay_dir / "overlay.js").is_file()


def test_resolve_web_dir_frozen_points_under_meipass(monkeypatch, tmp_path):
    """Frozen (PyInstaller) mode: resolves under sys._MEIPASS/obs_captions/web.

    This path MUST agree with the .spec `datas` dest ("obs_captions/web").
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resolve_web_dir() == tmp_path / "obs_captions" / "web"


def test_resolve_overlay_dir_frozen_is_meipass_web_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert resolve_overlay_dir() == tmp_path / "obs_captions" / "web" / "overlay"


# ---------------------------------------------------------------------------
# Gap #1 — .spec datas dest agrees with the frozen resolver
# ---------------------------------------------------------------------------


def test_spec_datas_web_dest_matches_frozen_resolver():
    """The .spec ``datas`` dest for the web tree MUST equal the component path that
    ``resolve_web_dir()`` assembles under ``sys._MEIPASS`` when frozen.

    Concretely: the frozen resolver does
        Path(sys._MEIPASS) / "obs_captions" / "web"
    so the .spec dest must be exactly ``"obs_captions/web"``.

    If someone changes the .spec dest to e.g. "web" this test will FAIL, catching
    the drift before a silent bundle breakage.

    We parse the ``datas`` list by extracting just that assignment (a plain list
    literal with no PyInstaller builtins), rather than exec()-ing the whole spec
    (which would fail outside PyInstaller because Analysis/PYZ/EXE are undefined).
    """
    import ast
    import re

    spec_path = Path(__file__).parent.parent / "obs_captions.spec"
    spec_text = spec_path.read_text(encoding="utf-8")

    # Extract just the `datas = [...]` assignment block (the list literal only).
    m = re.search(r"^datas\s*=\s*(\[.*?\])", spec_text, re.DOTALL | re.MULTILINE)
    assert m is not None, "Could not find `datas = [...]` assignment in obs_captions.spec"

    datas: list[tuple[str, str]] = ast.literal_eval(m.group(1))

    # The frozen resolver builds: Path(_MEIPASS) / "obs_captions" / "web"
    # so the spec dest component must be exactly this relative path.
    expected_dest = "obs_captions/web"

    web_entries = [(src, dest) for src, dest in datas if "web" in src and "obs_captions" in src]
    assert web_entries, (
        "No datas entry with 'obs_captions' + 'web' in src found in obs_captions.spec"
    )
    actual_dest = web_entries[0][1]

    assert actual_dest == expected_dest, (
        f"obs_captions.spec datas dest is {actual_dest!r} but "
        f"resolve_web_dir() (frozen) expects {expected_dest!r}. "
        "Update the .spec dest OR the frozen branch of resolve_web_dir() to agree."
    )


# ---------------------------------------------------------------------------
# Gap #3 — wheel contains the overlay HTML asset (non-slow, filesystem check)
# ---------------------------------------------------------------------------


def test_wheel_package_config_covers_web_overlay_assets():
    """Assert via tomllib that pyproject.toml's wheel config covers the overlay assets.

    Checks:
    1. overlay.html exists at src/obs_captions/web/overlay/overlay.html on disk.
    2. tool.hatch.build.targets.wheel.packages == ["src/obs_captions"] (parsed, not
       string-matched), so hatchling ships the entire package tree including web/.
    3. No 'exclude' key is present in the wheel target config (which could silently
       drop the web/ tree from the wheel).

    This is the reliable non-slow alternative to running ``uv build``: if the file
    exists under the declared package root, hatchling ships it automatically.
    """
    repo_root = Path(__file__).parent.parent

    # 1. Asset exists on disk (necessary for wheel inclusion).
    overlay_html = repo_root / "src" / "obs_captions" / "web" / "overlay" / "overlay.html"
    assert overlay_html.is_file(), (
        f"overlay.html not found at {overlay_html}; "
        "the wheel would be missing it. Run `git mv` to restore it inside the package."
    )

    # 2 & 3. Parse pyproject.toml with tomllib for robust structural assertions.
    pyproject = repo_root / "pyproject.toml"
    with pyproject.open("rb") as fh:
        data = tomllib.load(fh)

    wheel_cfg = data["tool"]["hatch"]["build"]["targets"]["wheel"]

    assert wheel_cfg.get("packages") == ["src/obs_captions"], (
        f"tool.hatch.build.targets.wheel.packages is {wheel_cfg.get('packages')!r}; "
        'expected ["src/obs_captions"]. Wheel asset inclusion is no longer guaranteed.'
    )

    assert "exclude" not in wheel_cfg, (
        f"tool.hatch.build.targets.wheel has an 'exclude' key: {wheel_cfg['exclude']!r}. "
        "An exclude rule could silently drop the web/ overlay assets from the wheel."
    )
