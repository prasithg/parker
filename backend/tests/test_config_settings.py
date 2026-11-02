"""Settings must ignore variables it does not declare.

Regression: pydantic-settings defaults to extra="forbid", so an unrelated
key in a developer's ``.env`` (e.g. EXA_API_KEY for another tool) raised
ValidationError when ``app.config`` was imported, and ``make test`` died in
conftest before a single test ran. All values here are synthetic; the
``.env`` files are written to pytest's tmp_path, never the repo.
"""

import pydantic
import pytest

from app import paths
from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_home(monkeypatch, tmp_path):
    # Keep the family config.json layer out of these tests.
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))


def test_unknown_dotenv_key_is_ignored(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "UNRELATED_SYNTHETIC_KEY=synthetic-value\nPATIENT_NAME=Synthetic\n",
        encoding="utf-8",
    )
    loaded = Settings(_env_file=env_file)
    assert loaded.patient_name == "Synthetic"
    assert not hasattr(loaded, "unrelated_synthetic_key")


def test_unknown_environment_variable_is_ignored(monkeypatch):
    monkeypatch.setenv("UNRELATED_SYNTHETIC_KEY", "synthetic-value")
    loaded = Settings(_env_file=None)
    assert loaded.patient_name == "Dad"
    assert not hasattr(loaded, "unrelated_synthetic_key")


def test_declared_fields_are_still_validated(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "UNRELATED_SYNTHETIC_KEY=synthetic-value\n"
        "DOSE_VERIFICATION_WINDOW_MINUTES=not-a-number\n",
        encoding="utf-8",
    )
    with pytest.raises(pydantic.ValidationError, match="dose_verification_window_minutes"):
        Settings(_env_file=env_file)
