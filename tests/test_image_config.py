import json

from utils.image_config import DEFAULT_IMAGE_SETTINGS, load_image_settings


def test_load_image_settings_defaults_when_no_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORGANISATION_IMAGE_CONFIG", raising=False)

    settings = load_image_settings()

    assert settings == DEFAULT_IMAGE_SETTINGS


def test_load_image_settings_prefers_env_override(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "organisation_image.json"
    config_path.write_text(
        json.dumps({"model": "config-model", "size": "512x512"}),
        encoding="utf-8",
    )
    env_path = tmp_path / "env.json"
    env_path.write_text(
        json.dumps({"model": "env-model", "size": None, "bogus": "x"}),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORGANISATION_IMAGE_CONFIG", str(env_path))

    settings = load_image_settings()

    assert settings["model"] == "env-model"
    assert settings["size"] == DEFAULT_IMAGE_SETTINGS["size"]
    assert set(settings.keys()) == set(DEFAULT_IMAGE_SETTINGS.keys())


def test_load_image_settings_falls_back_on_invalid_json(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "organisation_image.json"
    config_path.write_text(json.dumps({"model": "config-model"}), encoding="utf-8")
    env_path = tmp_path / "env.json"
    env_path.write_text("{not-json}", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORGANISATION_IMAGE_CONFIG", str(env_path))

    settings = load_image_settings()

    assert settings["model"] == "config-model"
