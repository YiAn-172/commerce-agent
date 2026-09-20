from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_secret: str = Field(min_length=8)
    log_level: str = "INFO"
    demo_auth_enabled: bool = True
    deepseek_model: str = "deepseek-flash"
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    deepseek_max_retries: int = Field(default=1, ge=0, le=1)
    deepseek_temperature: float = Field(default=0.1, ge=0, le=1)
    checkpoint_db: str = "/cache/checkpoints/checkpoints.sqlite"
    approval_checkpoint_db: str = "/cache/checkpoints/approval-checkpoints.sqlite"
    redis_url: str = "redis://redis:6379/0"
    jwt_ttl_seconds: int = Field(default=3600, ge=300, le=86400)
    sse_retention_seconds: int = Field(default=600, ge=60, le=3600)
    sse_heartbeat_seconds: int = Field(default=15, ge=5, le=60)
    faq_cache_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    intent_model_path: str = "/workspace/models/intent_classifier/current"
    max_graph_steps: int = Field(default=30, ge=1, le=100)
    max_tool_calls: int = Field(default=5, ge=1, le=20)
    max_llm_calls: int = Field(default=3, ge=1, le=10)

    @property
    def model_path(self) -> str:
        return self.intent_model_path


@lru_cache
def get_settings() -> Settings:
    return Settings()
