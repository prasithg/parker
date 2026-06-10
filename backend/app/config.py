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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
