# Desktop GUI Implementation Plan (P0 shared schema + P1 Tkinter GUI)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn `obs-captions.exe` into a desktop GUI (no-args → Tkinter window that edits the full config and starts/stops the run pipeline) while keeping the existing CLI when args are passed.

**Architecture:** A declarative settings schema drives both config I/O and GUI form generation. The GUI edits `config.toml`/`.env`, then Start launches `obs-captions run` as a subprocess and streams its log. CLI behavior is unchanged when argv has a subcommand.

**Tech Stack:** Python 3.12, Tkinter (stdlib), Pydantic (existing `config.py`), `tomllib` (read) + `tomli-w` (write), `subprocess`, PyInstaller.

## Global Constraints

- Python `>=3.12,<3.13`; no new heavy deps — only add `tomli-w` (tiny, pure-Python TOML writer).
- Files ≤ ~350 lines; one responsibility each; pure functions separated from I/O/UI.
- Existing CLI commands (`run`/`serve`/`list-devices`/`list-loopback-devices`/`config`/`check-engine`/`ipc-sidecar`) must behave identically when invoked with args.
- Config schema single source of truth stays `src/obs_captions/config.py` (Pydantic). The new schema module is presentation metadata only, validated against it.
- No secrets written to logs. `.env` is the only place API keys are persisted by the GUI.

---

## File Structure

- `src/obs_captions/settings_schema.py` (new) — declarative field metadata (label, widget, choices, section, applies_to, env-var for keys).
- `src/obs_captions/gui/__init__.py` (new)
- `src/obs_captions/gui/config_io.py` (new) — load/save `config.toml` + `.env`.
- `src/obs_captions/gui/runner.py` (new) — subprocess start/stop + line streaming.
- `src/obs_captions/gui/widgets.py` (new) — labeled/secret/choice widgets.
- `src/obs_captions/gui/sections.py` (new) — build tab frames from schema.
- `src/obs_captions/gui/app.py` (new) — main window, Notebook, run/stop/log panel, `main()`.
- `src/obs_captions/cli.py` (modify) — no-args dispatch to GUI.
- `obs_captions.spec` (modify) — `console=False`; add tkinter/tomli_w hidden imports if needed.
- `src/obs_captions/packaging.py` (modify) — Windows `attach_parent_console()` helper.
- `pyproject.toml` (modify) — add `tomli-w` dep.
- Tests under `tests/`.

---

## Task 1: Settings schema module

**Files:**
- Create: `src/obs_captions/settings_schema.py`
- Test: `tests/test_settings_schema.py`

**Interfaces:**
- Produces: `FIELDS: list[FieldSpec]` and `@dataclass(frozen=True) FieldSpec` with attrs: `key: str` (dotted path into AppConfig, e.g. `"local.model_size"`), `label: str`, `widget: Literal["text","choice","int","float","bool","path","secret","list"]`, `section: str`, `applies_to: frozenset[str]` (subset of `{"gui","plugin"}`), `choices: tuple[str,...] = ()`, `env_var: str | None = None` (for secrets).
- Produces: `ENGINES: tuple[str, ...]` (the 11 engine ids from README), `LOCAL_MODEL_SIZES: tuple[str,...]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings_schema.py
from obs_captions import settings_schema as s
from obs_captions.config import AppConfig

def test_engines_and_model_sizes_present():
    assert "local" in s.ENGINES and "openai" in s.ENGINES
    assert "small" in s.LOCAL_MODEL_SIZES

def test_every_field_key_resolves_in_appconfig():
    cfg = AppConfig()
    for f in s.FIELDS:
        node = cfg
        for part in f.key.split("."):
            assert hasattr(node, part) or isinstance(node, dict), f"bad key {f.key}"
            node = getattr(node, part, None)
            if node is None:
                break

def test_gui_covers_core_sections():
    sections = {f.section for f in s.FIELDS if "gui" in f.applies_to}
    assert {"General","Audio","Local","Output","Text","Export","OBS","API Keys"} <= sections
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/test_settings_schema.py -v` → ImportError.

- [ ] **Step 3: Implement `settings_schema.py`** — define `FieldSpec`, `ENGINES` (`local, openai, elevenlabs, google, xai, deepgram, assemblyai, azure, openrouter, replicate, groq`), `LOCAL_MODEL_SIZES` (`tiny, base, small, medium, large-v3`), and `FIELDS` enumerating each AppConfig field with label/widget/section/applies_to. Secrets: entries with `widget="secret"`, `env_var` set (e.g. `OPENAI_API_KEY`), `section="API Keys"`, `applies_to={"gui","plugin"}`. Exclude `[audio]`,`sink`,`[server]`,`[overlay]` from plugin `applies_to`.

- [ ] **Step 4: Run tests to verify pass** — `uv run pytest tests/test_settings_schema.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add src/obs_captions/settings_schema.py tests/test_settings_schema.py && git commit -m "feat(gui): declarative settings schema"`

---

## Task 2: Config I/O (config.toml + .env round-trip)

**Files:**
- Create: `src/obs_captions/gui/config_io.py`, `src/obs_captions/gui/__init__.py`
- Modify: `pyproject.toml` (add `tomli-w`)
- Test: `tests/test_gui_config_io.py`

**Interfaces:**
- Produces: `load_settings(config_path: str | Path | None, env_path: str|Path|None) -> dict[str, Any]` — returns a flat dict keyed by `FieldSpec.key` (+ `env:<VAR>` for secrets), using AppConfig defaults when files are missing.
- Produces: `save_settings(values: dict[str, Any], config_path, env_path) -> None` — writes TOML (nested from dotted keys) and `.env` (only secret keys, merged with existing).
- Consumes: `settings_schema.FIELDS`, `config.load_config`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_config_io.py
from obs_captions.gui import config_io

def test_roundtrip(tmp_path):
    cfg = tmp_path/"config.toml"; env = tmp_path/".env"
    values = config_io.load_settings(cfg, env)          # defaults
    values["local.model_size"] = "medium"
    values["engine"] = "deepgram"
    values["env:DEEPGRAM_API_KEY"] = "dg_test"
    config_io.save_settings(values, cfg, env)
    again = config_io.load_settings(cfg, env)
    assert again["local.model_size"] == "medium"
    assert again["engine"] == "deepgram"
    assert again["env:DEEPGRAM_API_KEY"] == "dg_test"
    assert "dg_test" in env.read_text()
    # config.toml must be loadable by the real loader
    from obs_captions.config import load_config
    load_config(str(cfg))

def test_missing_files_use_defaults(tmp_path):
    values = config_io.load_settings(tmp_path/"none.toml", tmp_path/"none.env")
    assert values["engine"] == "local"
```

- [ ] **Step 2: Run test to verify it fails** — ImportError.

- [ ] **Step 3: Add dep + implement.** Add `tomli-w` to `pyproject.toml` deps; `uv sync`. Implement `load_settings` (read via `load_config` → flatten by schema keys; read `.env` lines for secret vars) and `save_settings` (unflatten dotted keys into nested dict → `tomli_w.dump`; write secrets into `.env` merging existing lines). Do not write secret values into TOML.

- [ ] **Step 4: Run tests** — PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(gui): config.toml/.env load-save round-trip"`

---

## Task 3: Subprocess runner (start/stop + log streaming)

**Files:**
- Create: `src/obs_captions/gui/runner.py`
- Test: `tests/test_gui_runner.py`

**Interfaces:**
- Produces: `class CaptionRunner` with `start(sink: str, on_line: Callable[[str], None]) -> None`, `stop() -> None`, `is_running() -> bool`, and `build_argv(sink: str) -> list[str]` (pure, testable). `build_argv` returns `[sys.executable-or-frozen-exe, "run", "--sink", sink]` — frozen: `[sys.argv[0], "run", ...]`; dev: `[sys.executable, "-m", "obs_captions", "run", ...]`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_gui_runner.py
from obs_captions.gui.runner import CaptionRunner

def test_build_argv_dev(monkeypatch):
    monkeypatch.setattr("sys.frozen", False, raising=False)
    r = CaptionRunner()
    argv = r.build_argv("both")
    assert argv[-3:] == ["run", "--sink", "both"]
    assert "obs_captions" in argv

def test_lifecycle_with_fake_process(monkeypatch):
    r = CaptionRunner()
    lines = []
    # start a trivial process that prints and exits
    r._argv_override = ["python", "-c", "print('hello')"]
    r.start("browser", lines.append)
    r._thread.join(timeout=5)
    assert any("hello" in l for l in lines)
    assert not r.is_running()
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement `CaptionRunner`** — `Popen(argv, stdout=PIPE, stderr=STDOUT, text=True, bufsize=1)`, reader thread calls `on_line` per line; `stop()` terminates (Windows: `CTRL_BREAK_EVENT` if `CREATE_NEW_PROCESS_GROUP`, else `terminate()`→`kill()`); `is_running()` checks `poll()`. Support `_argv_override` for tests.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(gui): subprocess caption runner with log streaming"`

---

## Task 4: Widgets + sections (form building)

**Files:**
- Create: `src/obs_captions/gui/widgets.py`, `src/obs_captions/gui/sections.py`
- Test: `tests/test_gui_sections.py` (headless: skip if no Tk display)

**Interfaces:**
- Produces (`widgets.py`): `LabeledEntry`, `SecretEntry`(show="*"), `ChoiceBox`, `BoolCheck`, each with `.get()`/`.set(v)`.
- Produces (`sections.py`): `build_sections(notebook, values: dict) -> dict[str, Callable[[], dict]]` — creates one tab per distinct `section` for `applies_to∋"gui"` fields; returns a map `section -> collect()` where `collect()` reads current widget values into `{key: value}`. Engine choice wires a trace that shows/hides local/cloud fields.

- [ ] **Step 1: Write failing test** (guard Tk):

```python
# tests/test_gui_sections.py
import pytest
tk = pytest.importorskip("tkinter")
def _root():
    try: return tk.Tk()
    except tk.TclError: pytest.skip("no display")

def test_sections_build_and_collect():
    from tkinter import ttk
    from obs_captions.gui import config_io, sections
    root = _root(); nb = ttk.Notebook(root)
    values = config_io.load_settings(None, None)
    collectors = sections.build_sections(nb, values)
    assert "General" in collectors
    merged = {}
    for c in collectors.values(): merged.update(c())
    assert merged["engine"] == "local"
    root.destroy()
```

- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement widgets + `build_sections`.**
- [ ] **Step 4: Run → PASS (or skip headless).**
- [ ] **Step 5: Commit** — `git commit -m "feat(gui): schema-driven tab sections and widgets"`

---

## Task 5: Main window app

**Files:**
- Create: `src/obs_captions/gui/app.py`
- Test: `tests/test_gui_app_smoke.py` (headless-guarded)

**Interfaces:**
- Produces: `main(config_path: str|None = None) -> None` — builds `tk.Tk`, Notebook via `build_sections`, bottom frame with Save / Start / Stop buttons, status label, `ScrolledText` log; wires `CaptionRunner`. Start: collect → `save_settings` → `runner.start(sink, append_log)`.
- Consumes: `config_io`, `sections`, `runner`.

- [ ] **Step 1: Failing smoke test** — import `app`, construct root, call an internal `build_app(root)` that returns the window without entering mainloop; assert Start/Stop buttons exist. Skip if no display.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement `app.py`** with `build_app(root)` (testable) + `main()` (calls mainloop).
- [ ] **Step 4: Run → PASS/skip.**
- [ ] **Step 5: Commit** — `git commit -m "feat(gui): main window wiring start/stop/save/log"`

---

## Task 6: CLI entry dispatch (no-args → GUI)

**Files:**
- Modify: `src/obs_captions/cli.py`
- Test: `tests/test_cli_gui_dispatch.py`

**Interfaces:**
- Produces: `cli()` unchanged for args; new `main()` wrapper: `if len(sys.argv) == 1: from obs_captions.gui.app import main as gui_main; gui_main() else: cli()`. `pyproject.toml [project.scripts]` stays `obs_captions.cli:cli`? → change entry to `obs_captions.cli:main`.

- [ ] **Step 1: Failing test** — monkeypatch `sys.argv=["obs-captions"]`, monkeypatch `gui.app.main` to set a flag, call `cli.main()`, assert flag set; with `sys.argv=["obs-captions","config"]`, assert gui NOT called (click runs).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement `main()` in cli.py; update `[project.scripts]` to `obs_captions.cli:main`.**
- [ ] **Step 4: Run → PASS; also `uv run pytest tests/test_cli.py` still green.**
- [ ] **Step 5: Commit** — `git commit -m "feat(cli): launch GUI when no args, keep CLI for args"`

---

## Task 7: PyInstaller windowed build + parent-console attach

**Files:**
- Modify: `obs_captions.spec` (`console=False`), `src/obs_captions/packaging.py` (add `attach_parent_console()`), `src/obs_captions/cli.py` (call it in `main()` before `cli()` when args present, Windows only)
- Test: `tests/test_packaging_console.py`

**Interfaces:**
- Produces: `packaging.attach_parent_console() -> None` — Windows: `ctypes.windll.kernel32.AttachConsole(-1)` and rebind stdout/stderr; no-op elsewhere (guard `sys.platform`).

- [ ] **Step 1: Failing test** — on non-Windows, `attach_parent_console()` returns without error and does not raise; assert callable exists.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement helper; set `console=False` in spec; call helper in `cli.main()` CLI branch on Windows.**
- [ ] **Step 4: Run → PASS.** (Windows exe smoke is validated in CI, not locally.)
- [ ] **Step 5: Commit** — `git commit -m "build: windowed exe (console=False) + parent-console attach for CLI"`

---

## Self-Review notes
- Spec coverage: P0 (schema=T1, config_io=T2) ✓; P1 (runner=T3, widgets/sections=T4, app=T5, cli dispatch=T6, spec/console=T7) ✓. Overlay/text/export/OBS tabs are schema-driven (T1 defines fields, T4 renders) ✓.
- Deferred to a separate plan: **P2 — OBS plugin native settings page (C++ obs_properties)**, `docs/superpowers/plans/2026-07-15-plugin-settings.md`.
- API-key handling in the GUI writes only to `.env` (T2) — matches the security constraint. (Plugin-side OBS-direct storage is P2's concern.)
