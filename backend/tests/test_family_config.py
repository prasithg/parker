"""config.json — the family-administered layer under env vars.

The two hard rules pinned here: env always beats the file, and secrets
never live in the file (refused on write, dropped on read even when
hand-edited in). Plus the boring-but-load-bearing bits: atomic merge
writes, live-settings application, and a malformed file never crashing
the engine.
"""

import json

import pytest

from app import paths
from app.config import Settings, settings
from app.parker import family_config


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path))
    return tmp_path


@pytest.fixture
def restore_settings():
    snapshot = settings.model_dump()
    yield settings
    for key, value in snapshot.items():
        setattr(settings, key, value)


def test_write_then_read_roundtrip(home, restore_settings):
    written = family_config.write_family_config(
        {"patient_name": "Ravi", "parker_tts_rate_wpm": 165}
    )
    assert written == {"patient_name": "Ravi", "parker_tts_rate_wpm": 165}
    on_disk = json.loads((home / "config.json").read_text())
    assert on_disk["patient_name"] == "Ravi"
    assert on_disk["parker_tts_rate_wpm"] == 165


def test_write_merges_instead_of_replacing(home, restore_settings):
    family_config.write_family_config({"patient_name": "Ravi"})
    family_config.write_family_config({"personal_lexicon": "Sarah, Priya"})
    on_disk = family_config.read_family_config()
    assert on_disk["patient_name"] == "Ravi"
    assert on_disk["personal_lexicon"] == "Sarah, Priya"


def test_write_applies_to_live_settings(home, restore_settings):
    family_config.write_family_config({"patient_name": "Ravi"})
    assert settings.patient_name == "Ravi"


def test_meta_keys_are_writable_but_never_touch_settings(home, restore_settings):
    before = settings.model_dump()
    family_config.write_family_config({"onboarding_completed": True})
    assert family_config.read_family_config()["onboarding_completed"] is True
    assert settings.model_dump() == before


def test_needs_onboarding_flips_on_completion_flag(home, restore_settings):
    assert family_config.needs_onboarding() is True
    family_config.write_family_config({"patient_name": "Ravi"})
    assert family_config.needs_onboarding() is True  # partial setup is not done
    family_config.write_family_config({"onboarding_completed": True})
    assert family_config.needs_onboarding() is False


def test_secrets_are_refused_on_write(home):
    with pytest.raises(family_config.ConfigWriteError, match="secrets"):
        family_config.validate_updates({"anthropic_api_key": "sk-nope"})
    with pytest.raises(family_config.ConfigWriteError, match="secrets"):
        family_config.validate_updates({"dashboard_password": "hunter2"})
    assert not (home / "config.json").exists()


def test_unknown_keys_are_refused(home):
    with pytest.raises(family_config.ConfigWriteError, match="unknown"):
        family_config.validate_updates({"database_url": "sqlite:///elsewhere.db"})


def test_type_coercion_and_rejection(home):
    clean = family_config.validate_updates(
        {"parker_tts_enabled": "true", "parker_tts_rate_wpm": "160"}
    )
    assert clean == {"parker_tts_enabled": True, "parker_tts_rate_wpm": 160}
    with pytest.raises(family_config.ConfigWriteError):
        family_config.validate_updates({"parker_tts_rate_wpm": "fast"})
    with pytest.raises(family_config.ConfigWriteError):
        family_config.validate_updates({"patient_name": 7})
    with pytest.raises(family_config.ConfigWriteError):
        family_config.validate_updates({})


# --- the read side: Settings layering ------------------------------------


def test_config_file_feeds_settings_under_env(home, monkeypatch):
    (home / "config.json").write_text(json.dumps({"patient_name": "FromFile"}))
    assert Settings(_env_file=None).patient_name == "FromFile"

    monkeypatch.setenv("PATIENT_NAME", "FromEnv")
    assert Settings(_env_file=None).patient_name == "FromEnv"  # env wins


def test_secrets_in_config_file_are_ignored_on_read(home):
    (home / "config.json").write_text(
        json.dumps({"anthropic_api_key": "sk-hand-edited", "patient_name": "Ravi"})
    )
    fresh = Settings(_env_file=None)
    assert fresh.anthropic_api_key == ""  # dropped, not honored
    assert fresh.patient_name == "Ravi"


def test_unknown_and_meta_keys_in_config_file_are_ignored(home):
    (home / "config.json").write_text(
        json.dumps({"onboarding_completed": True, "not_a_setting": 1})
    )
    assert Settings(_env_file=None).patient_name == "Dad"


def test_malformed_config_file_never_crashes_settings(home):
    (home / "config.json").write_text("{this is not json")
    assert Settings(_env_file=None).patient_name == "Dad"
    (home / "config.json").write_text(json.dumps(["a", "list"]))
    assert Settings(_env_file=None).patient_name == "Dad"


def test_missing_config_file_is_fine(home):
    assert Settings(_env_file=None).patient_name == "Dad"
