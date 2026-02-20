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
    
    # Datadog
    dd_service: str = "agentmirror-backend"
    dd_env: str = "development"
    dd_version: str = "0.1.0"
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
