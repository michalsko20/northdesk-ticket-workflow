from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "northdesk-tickets"
    llm_mode: Literal["fake", "openai"] = "fake"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: SecretStr = SecretStr("")
    helpdesk_failure_rate: float = Field(default=0.2, ge=0, le=1)
    helpdesk_db_path: str = "helpdesk.db"

    @model_validator(mode="after")
    def require_real_key(self) -> "Settings":
        if self.llm_mode == "openai" and not self.openai_api_key.get_secret_value():
            raise ValueError("OPENAI_API_KEY is required when LLM_MODE=openai")
        return self
