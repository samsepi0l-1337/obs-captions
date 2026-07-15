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
