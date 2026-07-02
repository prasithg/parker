"""Configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


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
    
    # Database
    database_url: str = "sqlite:///./parker.db"
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


settings = Settings()
