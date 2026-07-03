"""The family-administered config file: ``PARKER_HOME/config.json``.

This is the write side of the config story (the read side is the
``FamilyConfigFileSource`` in ``app.config``). Everything the onboarding
wizard or ``parker onboard`` sets lands here — and nothing else:

- Only allowlisted, non-secret keys are writable. Secrets (API keys,
  tokens, passwords) are refused loudly so they stay env-or-keychain
  only; a config.json can be backed up, screenshotted, and mailed to a
  grandchild without a second thought.
- Writes are merge + atomic replace, so a crash mid-write never leaves a
  truncated file.
- Written values are applied to the live ``settings`` object too — the
  engine picks up wizard changes without a restart (every consumer reads
  ``settings`` attributes at call time).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from app import paths
from app.config import SECRET_SETTINGS_KEYS, settings

# Settings fields a family administrator may set through the wizard/CLI.
CONFIG_ALLOWLIST: dict[str, type] = {
    "patient_name": str,
    "parker_family_contacts": str,
    "personal_lexicon": str,
    "parker_tts_enabled": bool,
    "parker_tts_voice": str,
    "parker_tts_rate_wpm": int,
    "repair_event_capture_consented": bool,
    "parker_brain_model": str,
    "parker_brain_max_tokens": int,
}

# App-level metadata keys that live in config.json but are not Settings
# fields (the settings source ignores them).
META_KEYS: dict[str, type] = {
    "onboarding_completed": bool,
}

_WRITABLE = {**CONFIG_ALLOWLIST, **META_KEYS}


class ConfigWriteError(ValueError):
    """A rejected config write — the detail is safe to show a family admin."""


def read_family_config() -> dict[str, Any]:
    """The raw config file contents ({} when missing/unreadable)."""

    try:
        raw = json.loads(paths.config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def needs_onboarding() -> bool:
    return read_family_config().get("onboarding_completed") is not True


def _coerce(key: str, value: Any, expected: type) -> Any:
    if expected is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ConfigWriteError(f"{key} must be true or false")
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ConfigWriteError(f"{key} must be an integer")
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigWriteError(f"{key} must be an integer") from exc
    if expected is str:
        if not isinstance(value, str):
            raise ConfigWriteError(f"{key} must be a string")
        return value
    raise ConfigWriteError(f"{key} has an unsupported type")  # pragma: no cover


def validate_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Validate + coerce an update mapping; raise ConfigWriteError on any problem."""

    if not updates:
        raise ConfigWriteError("no settings provided")
    secrets = sorted(set(updates) & SECRET_SETTINGS_KEYS)
    if secrets:
        raise ConfigWriteError(
            "refusing to store secrets in config.json (env-or-keychain only): "
            + ", ".join(secrets)
        )
    unknown = sorted(set(updates) - set(_WRITABLE))
    if unknown:
        raise ConfigWriteError("unknown config keys: " + ", ".join(unknown))
    return {key: _coerce(key, value, _WRITABLE[key]) for key, value in updates.items()}


def write_family_config(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge validated updates into config.json (atomic) and the live settings.

    Returns the coerced key/value pairs that were written.
    """

    clean = validate_updates(updates)

    merged = read_family_config()
    merged.update(clean)

    paths.ensure_parker_home()
    target = paths.config_path()
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".config-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:  # pragma: no cover — best-effort cleanup
            pass
        raise

    for key, value in clean.items():
        if key in CONFIG_ALLOWLIST:
            setattr(settings, key, value)

    return clean
