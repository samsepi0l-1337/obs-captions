import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from obs_captions.cli import cli
from obs_captions.config import AppConfig, ExportConfig, LocalConfig, OverlayConfig, ProviderConfig, TextConfig, load_config
from obs_captions.text import ReplacementRule


def test_load_config_uses_m0_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    config = load_config(None)

    assert config.engine == "local"
    assert config.language == "ko"
    assert config.audio.device is None
    assert config.audio.samplerate == 16000
    assert config.audio.channels == 1
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8765
    assert config.overlay.font_family == "Pretendard, 'Noto Sans KR', sans-serif"
    assert config.overlay.font_size == 48
    assert config.overlay.font_weight == 700
    assert config.overlay.color == "#ffffff"
    assert config.overlay.partial_color == "#aaaaaa"
    assert config.overlay.background == "rgba(0,0,0,0.35)"
    assert config.overlay.outline_width == 2
    assert config.overlay.outline_color == "#000000"
    assert config.overlay.shadow == "0 2px 6px rgba(0,0,0,0.6)"
    assert config.overlay.position == "bottom"
    assert config.overlay.align == "center"
    assert config.overlay.max_lines == 3
    assert config.overlay.line_height == 1.3
    assert config.overlay.padding == 24
    assert config.overlay.letter_spacing == 0
    assert config.overlay.fade_ms == 200
    assert config.overlay.uppercase is False
    assert config.overlay.custom_css is None


def test_invalid_engine_is_rejected():
    with pytest.raises(ValidationError):
        AppConfig(engine="bad")


def test_api_keys_are_read_from_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "eleven-secret")

    config = load_config(None)

    assert config.openai_api_key == "openai-secret"
    assert config.elevenlabs_api_key == "eleven-secret"


def test_toml_values_override_defaults(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
engine = "openai"
language = "en"

[audio]
device = "BlackHole 2ch"
samplerate = 8000
channels = 2

[server]
host = "0.0.0.0"
port = 9000

[overlay]
font_family = "Pretendard"
font_size = 64
font_weight = 900
color = "#00ff00"
partial_color = "#444444"
background = "transparent"
outline_width = 4
outline_color = "#111111"
shadow = "none"
position = "top"
align = "left"
max_lines = 2
line_height = 1.5
padding = 12
letter_spacing = 1
fade_ms = 150
uppercase = true
custom_css = "web/overlay/custom.css"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.engine == "openai"
    assert config.language == "en"
    assert config.audio.device == "BlackHole 2ch"
    assert config.audio.samplerate == 8000
    assert config.audio.channels == 2
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 9000
    assert config.overlay.font_family == "Pretendard"
    assert config.overlay.font_size == 64
    assert config.overlay.font_weight == 900
    assert config.overlay.color == "#00ff00"
    assert config.overlay.partial_color == "#444444"
    assert config.overlay.background == "transparent"
    assert config.overlay.outline_width == 4
    assert config.overlay.outline_color == "#111111"
    assert config.overlay.shadow == "none"
    assert config.overlay.position == "top"
    assert config.overlay.align == "left"
    assert config.overlay.max_lines == 2
    assert config.overlay.line_height == 1.5
    assert config.overlay.padding == 12
    assert config.overlay.letter_spacing == 1
    assert config.overlay.fade_ms == 150
    assert config.overlay.uppercase is True
    assert config.overlay.custom_css == "web/overlay/custom.css"


def test_config_command_outputs_json_with_redacted_api_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "eleven-secret")
    config_path = tmp_path / "config.toml"
    config_path.write_text('engine = "elevenlabs"\n', encoding="utf-8")

    result = CliRunner().invoke(cli, ["config", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["engine"] == "elevenlabs"
    assert payload["openai_api_key"] == "***"
    assert payload["elevenlabs_api_key"] == "***"
    assert "openai-secret" not in result.output
    assert "eleven-secret" not in result.output


def test_overlay_position_and_align_literals_are_validated():
    assert OverlayConfig(position="top", align="left").position == "top"
    assert OverlayConfig(position="middle", align="center").align == "center"
    assert OverlayConfig(position="bottom", align="right").position == "bottom"

    with pytest.raises(ValidationError):
        OverlayConfig(position="center")
    with pytest.raises(ValidationError):
        OverlayConfig(align="middle")


def test_overlay_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        OverlayConfig(font="Arial")


def test_provider_config_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ProviderConfig(model_name="typo-field")


def test_local_config_device_and_compute_type_defaults():
    local = LocalConfig()
    assert local.device == "auto"
    assert local.compute_type is None


def test_local_config_hint_defaults_are_none():
    local = LocalConfig()
    assert local.initial_prompt is None
    assert local.hotwords is None


def test_local_config_accepts_hints():
    local = LocalConfig(initial_prompt="게임 방송", hotwords="닉네임")
    assert local.initial_prompt == "게임 방송"
    assert local.hotwords == "닉네임"


def test_local_config_accepts_valid_device_and_compute_type():
    local = LocalConfig(device="cuda", compute_type="float16")
    assert local.device == "cuda"
    assert local.compute_type == "float16"
    assert LocalConfig(device="cpu").device == "cpu"


def test_local_config_rejects_invalid_device():
    with pytest.raises(ValidationError):
        LocalConfig(device="gpu")


def test_local_config_round_trips_from_toml(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
engine = "local"

[local]
model_size = "large-v3"
device = "cuda"
compute_type = "float16"
cpu_threads = 2
""".strip(),
        encoding="utf-8",
    )

    config = load_config(str(config_path))

    assert config.local.model_size == "large-v3"
    assert config.local.device == "cuda"
    assert config.local.compute_type == "float16"
    assert config.local.cpu_threads == 2


# ---------------------------------------------------------------------------
# TextConfig + ReplacementRule
# ---------------------------------------------------------------------------


def test_text_config_defaults():
    cfg = TextConfig()
    assert cfg.replacements == []
    assert cfg.filter_words == []
    assert cfg.filter_mode == "mask"
    assert cfg.filter_mask == "***"


def test_replacement_rule_defaults():
    rule = ReplacementRule(match="hello", replace="hi")
    assert rule.regex is False
    assert rule.ignore_case is True
    assert rule.whole_word is False


def test_replacement_rule_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ReplacementRule(match="x", replace="y", typo_field=True)


def test_replacement_rule_invalid_regex_raises_value_error():
    with pytest.raises(ValueError, match="[Ii]nvalid regex"):
        ReplacementRule(match="[bad", replace="x", regex=True)


def test_text_config_from_toml_replacements(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[[text.replacements]]\nmatch = \"whisper\"\nreplace = \"Whisper\"\n",
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    assert len(config.text.replacements) == 1
    assert config.text.replacements[0].match == "whisper"
    assert config.text.replacements[0].replace == "Whisper"


def test_text_config_invalid_regex_raises_at_config_load(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[[text.replacements]]\nmatch = \"[invalid\"\nreplace = \"x\"\nregex = true\n",
        encoding="utf-8",
    )
    with pytest.raises((ValueError, ValidationError)):
        load_config(str(config_path))


def test_text_config_filter_words_from_toml(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[text]\nfilter_words = ["damn", "hell"]\nfilter_mode = "remove"\nfilter_mask = "---"\n',
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    assert config.text.filter_words == ["damn", "hell"]
    assert config.text.filter_mode == "remove"
    assert config.text.filter_mask == "---"


# ---------------------------------------------------------------------------
# ExportConfig
# ---------------------------------------------------------------------------


def test_export_config_defaults():
    cfg = ExportConfig()
    assert cfg.enabled is False
    assert cfg.path == "captions.srt"
    assert cfg.format == "srt"


def test_export_config_rejects_unknown_format():
    with pytest.raises(ValidationError):
        ExportConfig(format="ass")


def test_export_config_from_toml(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[export]\nenabled = true\npath = "out.vtt"\nformat = "vtt"\n',
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    assert config.export.enabled is True
    assert config.export.path == "out.vtt"
    assert config.export.format == "vtt"


def test_app_config_has_text_and_export_fields():
    config = load_config(None)
    assert hasattr(config, "text")
    assert hasattr(config, "export")
    assert isinstance(config.text, TextConfig)
    assert isinstance(config.export, ExportConfig)
    # Both default to off / empty
    assert config.export.enabled is False
    assert config.text.replacements == []


def test_app_config_rejects_unknown_text_subfield():
    with pytest.raises(ValidationError):
        AppConfig(text={"unknown_field": True})


# ---------------------------------------------------------------------------
# Finding 2 fix: TextConfig strips blank strings from filter_words at load
# ---------------------------------------------------------------------------


def test_text_config_strips_blank_filter_words():
    """Blank/whitespace-only strings in filter_words are removed at TextConfig
    validation to prevent r'\\b\\b' zero-width corruption of all captions."""
    cfg = TextConfig(filter_words=["", "bad", "  ", "word"])
    assert "" not in cfg.filter_words
    assert "  " not in cfg.filter_words
    assert cfg.filter_words == ["bad", "word"]


def test_text_config_all_blank_filter_words_yields_empty():
    """If every entry is blank, filter_words becomes empty (identity transform)."""
    cfg = TextConfig(filter_words=["", "   "])
    assert cfg.filter_words == []


def test_text_config_blank_filter_words_from_toml(tmp_path):
    """Blank filter_words loaded from a TOML file are stripped at config-load."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[text]\nfilter_words = ["", "curse", "  "]\n',
        encoding="utf-8",
    )
    config = load_config(str(config_path))
    assert config.text.filter_words == ["curse"]
