"""Configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    
    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""  # cloned voice ID
    
    # OpenAI
    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-4o-realtime-preview-2024-12-17"
    openai_realtime_voice: str = "alloy"
    
    # Patient
    patient_phone_number: str = ""
    patient_name: str = "Dad"
    
    # Database
    database_url: str = "sqlite:///./parkinsclaw.db"
    
    # Dashboard auth
    dashboard_username: str = "family"
    dashboard_password: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
