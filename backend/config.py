from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # AWS Bedrock
    aws_default_region: str = "us-east-1"
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_session_token: Optional[str] = None
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    
    # Neo4j
    neo4j_uri: Optional[str] = None
    neo4j_user: Optional[str] = None
    neo4j_password: Optional[str] = None
    
    # MongoDB
    mongodb_uri: Optional[str] = None
    mongodb_db_name: str = "agentmirror"
    
    # MiniMax
    minimax_api_key: Optional[str] = None
    minimax_group_id: Optional[str] = None
    minimax_model: str = "MiniMax-Text-01"
    minimax_voice_id: str = "English_CalmWoman"

    # ElevenLabs
    elevenlabs_api_key: Optional[str] = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel

    # Google Gemini
    gemini_api_key: Optional[str] = None
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice: str = "Kore"

    # Datadog
    dd_api_key: Optional[str] = None
    dd_app_key: Optional[str] = None
    dd_site: str = "datadoghq.com"
    dd_llmobs_enabled: bool = True
    dd_llmobs_agentless_enabled: bool = True
    dd_llmobs_ml_app: str = "parrot"
    dd_service: str = "parrot"
    dd_env: str = "development"
    dd_version: str = "0.1.0"

    @property
    def datadog_enabled(self) -> bool:
        return self.dd_api_key is not None and self.dd_api_key != "your_datadog_api_key_here"
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
