from obs_captions.gui import config_io


def test_roundtrip(tmp_path):
    cfg = tmp_path / "config.toml"
    env = tmp_path / ".env"
    values = config_io.load_settings(cfg, env)  # defaults
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
    values = config_io.load_settings(tmp_path / "none.toml", tmp_path / "none.env")
    assert values["engine"] == "local"


def test_save_keeps_secrets_out_of_toml_and_preserves_other_env_lines(tmp_path):
    cfg = tmp_path / "config.toml"
    env = tmp_path / ".env"
    env.write_text("# keep this comment\nUNRELATED=value\nDEEPGRAM_API_KEY=old\n", encoding="utf-8")
    values = config_io.load_settings(cfg, env)
    values["env:DEEPGRAM_API_KEY"] = "new-secret"

    config_io.save_settings(values, cfg, env)

    assert "new-secret" not in cfg.read_text(encoding="utf-8")
    env_text = env.read_text(encoding="utf-8")
    assert "# keep this comment\nUNRELATED=value\n" in env_text
    assert env_text.count("DEEPGRAM_API_KEY=") == 1
    assert "DEEPGRAM_API_KEY=new-secret" in env_text


def test_none_paths_use_defaults_without_writing_files():
    values = config_io.load_settings(None, None)

    assert values["engine"] == "local"
    assert values["env:OPENAI_API_KEY"] == ""
