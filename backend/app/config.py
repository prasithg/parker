"""Configuration loaded from environment variables and the family config file.

Precedence (first wins): explicit env vars > ``.env`` (dev convenience) >
``PARKER_HOME/config.json`` (family-administered, written by the
onboarding wizard / ``parker onboard``) > code defaults.

The config file NEVER carries secrets: key/token/password fields are
dropped on read even if someone hand-edits them in (and the write path in
``app.parker.family_config`` refuses them). Secrets are env-or-keychain
only.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

logger = logging.getLogger("parker.config")

# Settings fields that must never be read from config.json.
SECRET_SETTINGS_KEYS = frozenset(
    {
        "anthropic_api_key",
        "openai_api_key",
        "elevenlabs_api_key",
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_phone_number",
        "dashboard_password",
        "parker_openclaw_gateway_token",
    }
)


def _default_database_url() -> str:
    from app import paths

    return paths.default_database_url()


class FamilyConfigFileSource(PydanticBaseSettingsSource):
    """``PARKER_HOME/config.json`` as a low-precedence settings source.

    Unknown keys are ignored (they may be app-level metadata like
    ``onboarding_completed``); secret keys are dropped unconditionally; a
    missing or malformed file contributes nothing — the engine must boot
    on a broken config file, never crash.
    """

    def _read_file(self) -> dict[str, Any]:
        from app import paths

        try:
            raw = json.loads(paths.config_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("ignoring unreadable config file %s: %s", paths.config_path(), exc)
            return {}
        if not isinstance(raw, dict):
            logger.warning("ignoring config file %s: top level is not an object", paths.config_path())
            return {}
        fields = self.settings_cls.model_fields
        return {
            key: value
            for key, value in raw.items()
            if key in fields and key not in SECRET_SETTINGS_KEYS
        }

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Unused — __call__ supplies the whole mapping at once.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._read_file()


class Settings(BaseSettings):
    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # ElevenLabs (voice cloning is optional and consent-gated; not the product)
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""  # cloned voice ID
    voice_clone_consented: bool = False  # explicit family consent recorded

    # OpenAI
    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-4o-realtime-preview-2024-12-17"
    openai_realtime_voice: str = "alloy"

    # Patient
    patient_phone_number: str = ""
    patient_name: str = "Dad"

    # Database (default resolves through app.paths / PARKER_HOME)
    database_url: str = Field(default_factory=_default_database_url)
    dose_verification_window_minutes: int = 30

    # Parker non-response escalation candidates
    parker_non_response_resurface_threshold: int = 3
    parker_non_response_quiet_minutes: int = 30

    # Dashboard auth
    dashboard_username: str = "family"
    dashboard_password: str = ""

    # Anthropic (model-driven repair choices; falls back to hardcoded when unset)
    anthropic_api_key: str = ""

    # Brain (conversational answers behind the policy gate; docs/brain-adapters.md).
    # Without ANTHROPIC_API_KEY the answer lane stays the deterministic stub.
    parker_brain_model: str = "claude-sonnet-5"
    parker_brain_max_tokens: int = 300

    # Local voice output (macOS say; no cloud, no dependencies)
    parker_tts_enabled: bool = True
    parker_tts_voice: str = ""  # empty = system default voice
    parker_tts_rate_wpm: int = 0  # 0 = system default rate

    # Learning flywheel v0 (both default OFF / empty; consent is explicit)
    # When consented, each repair exchange (hypotheses, offered choices, the
    # user's selection) is stored locally as a labeled example. Never audio.
    repair_event_capture_consented: bool = False
    # Comma-separated names/words Parker should be biased toward hearing
    # (family names, medication-free daily vocabulary). Used as the local
    # ASR initial prompt and available to repair candidate generation.
    personal_lexicon: str = ""

    # Capability administration (app/parker/contacts.py). Comma-separated
    # family/caregiver contact names the admin has enabled for messages.
    # Within this allowlist the patient's own confirmation releases a message
    # (recorded as capability policy, visible in review); off-allowlist
    # recipients stay caregiver-approval-gated. Contacts also feed the ASR
    # bias lexicon so the allowlist and recognition never drift apart.
    # Empty (default): nothing auto-releases; behavior is unchanged.
    parker_family_contacts: str = ""

    # OpenClaw gateway (docs/brain-adapters.md v1). URL of the family's
    # patient-identity OpenClaw instance, e.g. http://127.0.0.1:18789.
    # Empty (default): no gateway — the brain falls back to Claude/stub and
    # gateway-backed action types are neither proposable nor executable.
    # The token maps to the gateway's OPENCLAW_GATEWAY_TOKEN; leave empty
    # for loopback dev gateways without auth.
    parker_openclaw_gateway_url: str = ""
    parker_openclaw_gateway_token: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            FamilyConfigFileSource(settings_cls),
            file_secret_settings,
        )


settings = Settings()
