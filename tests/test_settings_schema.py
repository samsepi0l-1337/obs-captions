import subprocess
import sys

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
    assert {"General", "Audio", "Local", "Output", "Text", "Export", "OBS", "API Keys"} <= sections


def test_fieldspec_has_help_and_engines_attrs():
    for f in s.FIELDS:
        assert isinstance(f.help, str)
        assert isinstance(f.engines, tuple)


def test_key_fields_have_help_text():
    by_key = {f.key: f for f in s.FIELDS}
    for key in ("engine", "language", "audio.source", "local.model_size", "local.vad_threshold"):
        assert by_key[key].help, f"missing help for {key}"
    # loopback jargon is explained for beginners.
    assert "loopback" in by_key["audio.source"].help.lower()


def test_provider_fields_and_keys_carry_engine_meta():
    by_key = {f.key: f for f in s.FIELDS}
    assert by_key["providers.openai.model"].engines == ("openai",)
    assert by_key["providers.google.mode"].engines == ("google",)
    assert by_key["providers.azure.region"].engines == ("azure",)
    for provider in ("openai", "elevenlabs", "google", "azure", "deepgram"):
        secret = by_key[f"providers.{provider}"]
        assert secret.widget == "secret"
        assert secret.engines == (provider,)
    # OBS password is not engine-specific — always shown.
    assert by_key["obs.obs_ws_password"].engines == ()


def test_secret_masking_and_values_unchanged():
    by_key = {f.key: f for f in s.FIELDS}
    # env_var / choices real values must not have been localized.
    assert by_key["providers.openai"].env_var == "OPENAI_API_KEY"
    assert by_key["engine"].choices == s.ENGINES
    assert by_key["audio.source"].choices == ("mic", "loopback")


def _import_fields_module_first(module: str) -> subprocess.CompletedProcess:
    """Run a fresh interpreter that imports ``module`` before any sibling.

    Forces the import order via subprocess (no module cache reuse) so a
    circular-import regression between settings_schema/settings_fields would
    actually be exercised, regardless of which module pytest happened to
    import first in-process.
    """
    code = f"import {module} as m; print(len(m.FIELDS))"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )


def test_settings_fields_importable_first_no_circular_import():
    result = _import_fields_module_first("obs_captions.settings_fields")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "80"


def test_settings_schema_importable_first_no_circular_import():
    result = _import_fields_module_first("obs_captions.settings_schema")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "80"


def test_settings_types_importable_standalone():
    # settings_types has no FIELDS; just confirm it imports cleanly on its own.
    proc = subprocess.run(
        [sys.executable, "-c", "import obs_captions.settings_types as m; print(m.ENGINES[0])"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "local"
